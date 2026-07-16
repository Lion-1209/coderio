# coderio 架构设计文档

- **文档版本**：2026-07-02（基于实际代码库，非早期 spec）
- **代码规模**：~3600 行 Python（src/coderio），327 测试全绿
- **技术栈**：Python 3.11 + langchain + langgraph + Textual + Rich + Typer，Windows 优先
- **Skill 底座**：Lion-Skills 0.3.0（12 skill，bundled 随包）

> 本文档描述的是**当前代码库的实际状态**。S0/S1/S2 早期设计 spec 见 `docs/superpowers/specs/`，本文是它们实现后的整合视图。

---

## 0. 一句话定位

coderio 是一个**技能驱动的编程 agent**：它的"骨架"是 Lion-Skills 套件（clarify→spec→task→execute→verify→commit 这条流水线），coderio 给这套骨架配上真正能干活的工具、一个强制遵循工作流的 **harness 状态控制层**、以及两种运行模式（交互式单 agent / 6-agent 流水线）。参照对象是 claude code / codex / zcode。

核心理念（贯穿整个设计）：**skill 是操作手册，harness 是执行纪律，工具是手**。三者分层，互不替代。

---

## 1. 总体架构：分层单体

分层单体（layered monolith），依赖**严格单向向下**。没有 IPC、没有微服务、没有独立进程——所有模块在同一个 Python 进程里，上层调下层，下层不反向依赖。

```
┌─────────────────────────────────────────────────────────────┐
│  CLI 层 (cli/)          Typer app + Rich REPL + 流式 UI       │
│    app.py · repl.py · stream.py · commands.py · ...          │
├─────────────────────────────────────────────────────────────┤
│  Agent 层 (agent/)      ReAct 循环 + harness 状态控制          │
│    loop.py · harness.py · prompts.py · stream.py              │
├─────────────────────────────────────────────────────────────┤
│  编排层 (crew/)          6-agent 串行流水线（可选高级模式）      │
│    orchestrator.py · agents.py · state.py                     │
├────────────────────────────────────────────────────────────────┤
│  能力层                  tools/ · skills/ · llm/ · session/ · config/  │
└─────────────────────────────────────────────────────────────┘
```

### 模块职责与规模

| 模块 | 行数 | 职责 | 关键文件 |
|------|------|------|----------|
| `agent/` | 780 | ReAct 循环、harness 硬约束、提示词构建、流式协议 | loop.py, harness.py, prompts.py |
| `cli/` | 960 | Typer 应用、交互 REPL、Rich 流式 UI、slash 命令、凭证/onboarding | repl.py, stream.py, app.py |
| `tools/` | 841 | 12 个工具 + 权限门 + langchain 适配 | __init__.py, permission.py, base.py |
| `crew/` | 376 | 6-agent 流水线编排 + verify→修复循环 | orchestrator.py, agents.py |
| `config/` | 241 | 三层 TOML 配置合并 + 用户目录 bootstrap | loader.py, models.py |
| `skills/` | 213 | SkillStore 三层加载 + 阶段触发映射 | store.py, triggers.py |
| `session/` | 139 | jsonl 追加式会话存储 + resume | store.py, message.py |
| `llm/` | 48 | 模型工厂（OpenAI/Anthropic 协议 + provider 注册表） | factory.py |

### 依赖方向（关键约束）

```
cli/ ──调──► agent/loop.run_agent ──调──► tools/, skills/, session/, config/, llm/
crew/orchestrator ──复用──► agent/loop._execute_turn (传 harness=None)
agent/ ──不反向依赖──► cli/   (cli 是壳，agent 是核)
```

唯一"复用而非依赖"的特殊接缝：`crew/orchestrator.py` 复用 `agent/loop._execute_turn`，但传 `harness=None`（crew 有自己的 verify→修复循环，见 §5）。

---

## 2. 两种运行模式

coderio 有两种运行模式，共用底层能力，面向不同场景：

### 2.1 单 agent 模式（S0+S1，默认）

交互式 REPL，日常用。一个 agent + 全套工具 + harness 硬约束。用户说一句，agent 跑一轮（可能多步工具调用 + 验证），回复。

- **入口**：`coderio` 命令 → `cli/app.py:main_entry` → `cli/repl.py:run_repl`
- **核心循环**：`agent/loop.py:run_agent` → `_execute_turn`（带 harness）
- **harness 生效**：写完代码不验证会被硬拦截

### 2.2 Crew 流水线模式（S2，高级）

一条命令跑完整需求，6 个专职 agent 串行接力。每个 agent 只拥有自己阶段的工具（物理隔离），通过共享 `ProjectState` 交接。

- **入口**：`coderio crew "<需求>"` → `crew/cli_cmd.py` → `CrewOrchestrator.run`
- **核心**：`crew/orchestrator.py`，顺序跑 clarify→spec→task→execute→verify→commit
- **harness 不生效**：crew 调 `_execute_turn` 时传 `harness=None`，它有自己的 verify→修复循环（见 §5.3）

### 2.3 两者的关系

| | 单 agent | Crew |
|---|---|---|
| 适用 | 日常交互、问答、小修改、单点编码任务 | 大需求、完整功能开发 |
| agent 数 | 1 个（全工具） | 6 个（每阶段工具隔离） |
| harness | 硬约束生效 | 不生效（crew 自有 verify 循环） |
| 人机介入 | 随时对话 | clarify 后、spec 后暂停等确认 |
| 触发 | `coderio` | `coderio crew "<需求>"` |

---

## 3. Agent 层：ReAct 循环 + harness 状态控制（核心）

这是 coderio 最关键的设计——**把"遵循工作流"从软提示词规则变成系统级结构约束**。

### 3.1 为什么需要 harness（设计动机）

裸 ReAct 循环（模型调工具→看结果→再调→…→说"完成"）有一个致命弱点：**模型说"完成"就结束**。如果模型写了 500 行代码但从未运行就声称"我做完了"，循环立即返回——产出未经验证。

提示词无论写多强（"MANDATORY"、"MUST verify"）都是**软规则**：模型决定停就停，循环无权干预。实测中贪吃蛇游戏两次翻车，根因都是这个。

### 3.2 harness 的核心机制：接管终止权

claude code 的 harness 在模型说"完成"时**有权不放行**：它检查 ground truth（工具调用记录），发现未验证的代码变更就**强制注入一条续跑消息**让模型继续，而不是结束。

coderio 的 harness（`agent/harness.py`）实现同样的事：它控制两件模型无法覆盖的事——

1. **终止权**：模型返回"无 tool_calls"想结束时，harness 决定是**真正结束**还是**拦截并强制续跑**。
2. **工具结果增强**：harness 在工具结果里追加结构性内容（提醒、nudge）。

**关键**：所有决策基于**已发生的工具调用/结果（ground truth）**，不基于模型*声称*自己做了什么。

### 3.3 四道门

Harness 维护四道门，读工具调用历史和 todo 状态：

#### VerifyGate（硬，逐级升级）★核心修复

- **触发**：模型返回无 tool_calls 文本想结束，且**存在未验证的写入**（自上次 bash 以来有过成功的 write_file/edit_file/multi_edit）
- **逐级升级**：

| attempt | 动作 |
|---------|------|
| 0（首次拦截）| 强制续跑，注入 `[harness] You MUST run it (use bash...)` |
| 1（第二次）| 强制续跑，列出未验证文件名，措辞更严厉 |
| 2（第三次）| **放行 + UI 红色警告面板**（永不无限循环、永不静默放水）|

- **"已验证"定义**：跑过 bash（哪怕命令失败）= 已尝试验证，门放行。这避免 agent 用"跑了一下报错"卡死，又阻止"写完就说完成"。

#### CompletionGate（硬，逐级升级）

- **触发**：已过验证门，但 `TodoStore` 有非平凡 todo（≥1 项 status != completed）
- **动作**：同 VerifyGate 的逐级升级（2 次拦截后放行+警告）
- **平凡任务豁免**：todo 为空时此门跳过（小问答/小修改不被卡）

#### PlanGate（软）

- **触发**：模型 write/edit/multi_edit，且 `TodoStore.todos` 为空
- **动作**：工具结果末尾追加 `[nudge] ...先 todo add 分解任务...`，**不阻断**（工具照常执行），且每 turn 只 nudge 一次
- **为什么软**：改错别字也要先建 todo 是过度摩擦

#### GroundingGate（硬，逐级升级）★分析正确性

- **触发**：已过验证门和完成门，模型最终文本里**显式引用了代码位置**（`foo.py`、`src/x.py:42`），但该文件**本 turn 从未被 read_file/grep/glob/list_dir 读过**
- **逐级升级**：同 VerifyGate（0/1 强制续跑要求先读，2 放行+警告）
- **"已读"定义**：`HarnessState.read_files` 记录所有 read-only 工具的 path/pattern。匹配是宽松的——basename 相同、或读取路径是引用路径的子串（`list_dir("src/agent/")` 覆盖 `src/agent/loop.py`）
- **只守代码声明，不碰对话**：正则要求有点扩展名（`.py/.js/.md...`），纯散文（"the loader"、"step 2"、"config.harness"无扩展名）不触发
- **为什么需要**：这是 coderio 自分析项目时踩过的坑——它读了文档没读源码，就断言"config.harness 未接入 loader"（实际三处全通）。文档是意图、代码是现实，两者 gap 就是 bug 栖身处。软规则"先读再下结论"已被证明会被跳过，所以做成硬结构约束。和 VerifyGate 同构：基于 ground truth（工具历史），不信任模型自述。

### 3.4 harness 在循环里的接线

`_execute_turn`（`agent/loop.py`）是 S0 和 S2 共享的 turn 循环。harness 在里面有三处插入点：

```python
for _ in range(max_rounds):
    stream.on_step_start()          # ← UI 计时器启动（见 §6.2）
    ai = run_step(...)              # 调模型
    if not ai.tool_calls:           # 模型想结束
        # ★ 插入点 1：终止检查
        cont, inject, warn = harness.check_termination(text)
        if cont:                    # 拦截 → 注入续跑消息 → continue（不 return）
            ...
        if warn: stream.on_harness_warn(warn)
        return text                 # 真正结束
    for tc in tool_calls:
        result = _invoke_tool(...)  # ★ 工具错误变 result（见 §3.6）
        # ★ 插入点 2+3：observe + after_tool_call
        harness.observe(name, args, result)        # 记录 ground truth
        aug = harness.after_tool_call(...)          # PlanGate nudge
        if aug: result = aug
```

### 3.5 "系统级"的三条判据

| 判据 | 软规则（改造前） | 结构约束（harness） |
|------|---------------|-------------------|
| 模型能否绕过 | 能（提示词可忽略） | **否**——终止权在 harness |
| 基于 ground truth | 否（基于模型自述） | **是**——读工具调用历史 |
| 可审计 | 否（静默） | **是**——注入消息入 session、警告面板可见、attempt 有计数 |

### 3.6 工具错误韧性 + 分级（_invoke_tool）

`tool.run(**args)` 被 `_invoke_tool` 包裹：任何异常（TypeError/ValueError/...）都变成结构化 tool result 回灌给模型，**不中断 turn**。错误按**可重试性**分级，给模型可操作的信号：

| 标记 | 触发 | 模型该做什么 |
|------|------|-------------|
| `[retryable]` | TypeError（参数签名错）/ 未知 Exception | 改参数或改调用方式，再试一次 |
| `[non-retryable]` | PermissionError / FileNotFoundError / IsADirectoryError | 这是环境约束——**别重复同一调用**，换路径或换方法 |

- 模型给 bash 传了 `path`（bash 只认 `cwd`）→ 返回 `[retryable] tool 'bash' rejected the arguments...`，模型改参数重试
- 模型 read_file 一个无权限路径 → `[non-retryable] tool 'read_file' cannot proceed: PermissionError...`，模型换路径而非死磕
- 设计原则：**工具调用层面的错误不是错误，是信号**。只有底层 LLM API 错误（auth/网络/限流，从 run_step 抛出）才是致命的。BaseException（SystemExit/KeyboardInterrupt）不被捕获——那是用户/系统中断，不是工具结果。

---

## 4. 意图分类：CODE / QA / ANALYZE（提示词层）

harness 是硬约束（基于 ground truth），意图分类是**提示词层的软路由**——告诉模型何时走编码工作流、何时直接答。两者正交。

### 4.1 三种意图（`agent/prompts.py: _BASE_INSTRUCTIONS`）

每条用户消息先分类：

| 意图 | 信号 | 行为 |
|------|------|------|
| **CODE** | "实现/写/改/修/重构/构建" | 走完整编码工作流（见 §4.2）|
| **QA** | "X 是什么/为什么/解释一下" | 简洁中文直接答，可读代码但不写文件、不建 todo |
| **ANALYZE** | "这样设计好不好/帮我评审" | 先读相关代码，用证据回答，给权衡非武断结论 |

### 4.2 CODE 工作流（6 步 + 执行段）

仅 CODE 模式触发，playbook 体由 CORE_CHAIN_SKILLS 注入：

```
0. EXPLORE FIRST（先探索再动手）
1. clarifying-questions（澄清模糊）
2. spec-writing（写设计）
3. task-breakdown（拆任务，用 todo）
4. executing-plans（逐任务实现）
     └─ 执行段：testing / debugging / code-review（按需 activate_skill）
5. verify-and-fix（验证完成，harness 硬拦截未验证的"完成"）
6. commit-message（规范提交）
```

### 4.3 通用 agent 保障（QA/ANALYZE 模式）

- 简洁直接，中文进中文出，不铺垫不道歉
- 答案 grounded in project（关于本仓库的问题先 read_file/grep）
- 区分事实与推测，给权衡
- 统一澄清原则：不确定时问一个聚焦问题（QA 轻量问、CODE 走结构化 skill）

### 4.4 系统提示词构建（`build_system_prompt`）

```
_BASE_INSTRUCTIONS（意图分类 + 工作流 + 通用保障）
  + CORE_CHAIN_SKILLS 的 body（clarify/spec/task/executing-plans/verify-and-fix/commit-message，
    作为 CODE 模式 runtime rules 始终注入）
  + 横切 skill 的分组列表（执行段/横切/上手元，opt-in，见 §7.3）
  + 用户显式 activate 的 skill body
```

---

## 5. Crew 流水线（S2，高级模式）

### 5.1 6 角色 + 物理工具隔离

`crew/agents.py` 定义 6 个 `AgentRole`，**每个角色只拥有自己阶段的工具**（不是提示词约束，是物理隔离）：

| 阶段 | 角色 | 工具 | 产出字段 | 暂停 |
|------|------|------|---------|------|
| clarify | Clarifier | read_file/list_dir/glob/grep（**无 write**）| clarification | ✓ |
| spec | SpecWriter | + write_file | spec | ✓ |
| task | TaskPlanner | + todo | task_list | |
| execute | Implementer | + edit_file/multi_edit/bash | implementation | |
| verify | Verifier | read/glob/grep/bash（**无 write**）| verification | |
| commit | Committer | bash/read_file/list_dir | commit_message | |

> Clarifier 物理上没有 write_file——它**必须**产出澄清结论才轮到下一个。这是"硬编排"。

### 5.2 共享状态交接

`crew/state.py: ProjectState` 是 6 个 agent 的共享黑板，每个 agent 读上游产出、写自己的字段：

```
request → clarification → spec → task_list → implementation → verification → commit_message
```

### 5.3 verify→修复循环

Verifier 产出后，`_verification_passed()` 启发式判断（含 fail/未通过/❌ 等信号 = 未通过）。未通过则回退到 execute 阶段重跑，最多 `max_fix_loops=2` 次：

```
execute → verify → (未通过) → execute → verify → ... → (达上限) → commit
```

这是 crew 自己的验证机制，**不依赖单 agent 的 harness**（crew 调 `_execute_turn` 传 `harness=None`）。

### 5.4 人机介入点

非 auto 模式下，clarify 后和 spec 后暂停，通过 `on_pause` 回调等用户输入（澄清答案 / spec 确认）。`--auto` 跳过所有暂停。

---

## 6. CLI 层与流式 UI

### 6.1 REPL 结构（`cli/repl.py`）

- `build_runtime(...)` → 8 元组（cfg, store, model, tools, gate, session, active, stream）
- `_loop(...)`：`▸ you` 提示符 → slash 命令分发 / 普通 agent turn
- slash 命令：`/help /exit /clear /cost /mode /model /skills /config /resume /list`
- `/mode`、`/model` 原地重建（保留 session）

### 6.2 流式 UI（`cli/stream.py: RichStream`）

实现 `StreamHandler` 协议。核心设计：**单个 always-on busy 指示器**，带已用秒数计时器，覆盖整个模型等待周期（思考 + 生成 + 工具间隙），屏幕永不僵死。

关键技术点：
- 用 `Live(get_renderable=self._busy_renderable)` 回调（**不是**静态 renderable）——Rich 自动刷新每 tick 重新调用回调，重新读 `time.monotonic()`，所以计时器和 spinner 同步跳动。这是修复"点在转但秒数不动"bug 的关键。
- assistant 回复用蓝色 Panel（cc 风格），tool 输出折叠 3 行，思考用 spinner + 预览，截断/harness 警告用黄/红 Panel。

### 6.3 on_step_start：消除"卡住"感

`_execute_turn` 每次 `run_step` 前调 `stream.on_step_start()`，启动 busy 指示器。这样工具结果出来后到下一轮模型输出的间隙也有动效，不再"一动不动"。

### 6.4 StreamHandler 协议（`agent/stream.py`）

```
on_step_start → on_token / on_thinking → on_tool_start → on_tool_end → ... → on_finish
                                                                   on_truncated（截断）
                                                                   on_harness_warn（harness 放行警告）
```

`NullStream` 全空实现，用于测试/headless。

---

## 7. 能力层

### 7.1 工具层（`tools/`，12 个工具）

| 类别 | 工具 |
|------|------|
| 读 | read_file, list_dir, glob, grep |
| 写 | write_file, edit_file, multi_edit |
| 执行 | bash（Git Bash，Windows 自动探测）|
| 计划 | todo（TodoStore，harness 读它）|
| 外部 | web_search, web_fetch |
| 记忆 | note（跨会话长期记忆）|

**权限门**（`permission.py`）：confirm / plan / auto 三模式。`DESTRUCTIVE_TOOLS`（write/edit/multi_edit/bash/web_fetch/note）在 plan 模式全挡、confirm 模式逐个问、auto 全放。

**统一接口**（`base.py`）：每个工具声明 pydantic `args_schema` + `run()`，经 `to_langchain_tool` 适配成 `StructuredTool` 绑定给模型。

### 7.2 Skill 层（`skills/`，Lion-Skills 0.3.0）

**三层加载**（优先级低→高）：bundled（随包，`src/coderio/skills/lion-skills/`）< user（`~/.coderio/skills/`）< project（`./.coderio/skills/`）。高层覆盖低层。

- `SkillStore._load_layer` 递归 glob `**/SKILL.md`，兼容 Lion-Skills 嵌套布局
- body 懒加载（只在用到时读文件），元数据缓存
- 12 个 Lion-Skills skill + 1 个 bundled `executing-plans` = 13 个

**阶段触发**（`triggers.py`）：`detect_stage(user_input)` 按关键词（"开始实现"/"commit"）预激活对应 skill。

### 7.3 skill 在提示词里的呈现（分组）

`descriptions_for_prompt()` 按**角色分组**列出 opt-in skill（不扁平堆砌），且已注入 body 的 core-chain skill 不重复列出：

```
CODE 执行段（写完代码后按需）:
  - testing / debugging / code-review
横切（任何阶段按需）:
  - naming / error-handling
上手/元:
  - onboarding-unknown-codebase / lion-writing-skills
```

### 7.4 配置（`config/`）

**三层 TOML 合并**：defaults < user（`~/.coderio/config.toml`）< project（`./.coderio/config.toml`）< env。`frozen` dataclass。

关键字段：
- `model`: default, provider, base_url, provider_id, max_output_tokens=16384
- `tools`: bash_shell, permission_mode, max_tool_rounds=25
- `skills`: auto_load, stage_auto_inject, **harness=True**, repo_url
- `cli`: theme, show_tool_output

### 7.5 Provider 注册表（`cli/providers.py`）

5 个 provider：智谱 coding plan / 阶跃 coding plan / 智谱 API / 阶跃 API / OpenAI 自定义。coding plan 走 Anthropic 协议，API key 模式走 OpenAI 兼容。无 Z.ai。

### 7.6 会话（`session/`）

jsonl 追加式存储（`~/.coderio/sessions/`）。支持 `Session.create / load / load_by_id / list_recent / append`。Message 有 user/assistant/tool 三种 role + tool_calls。

---

## 8. 数据流：一次 CODE 任务的完整路径

以"写个 hello.py 并测试"为例，单 agent 模式：

```
用户输入 "写个 hello.py 内容 print(1)，写好告诉我完成了"
  │
  ▼
repl._loop → run_agent(harness_enabled=True)
  │  1. detect_stage（无阶段信号）
  │  2. build_system_prompt（注入意图分类 + core chain + skill 列表）
  │  3. 构造 Harness（找到 TodoStore）
  │  4. session.append(user msg)
  │
  ▼
_execute_turn(harness=h)  循环：
  │
  ├─ round 1: run_step → 模型返回 tool_calls=[write_file]
  │    on_step_start (UI 计时启动)
  │    stream.on_token（流式输出模型的思考文本）
  │    _invoke_tool(write_file) → "Wrote 12 chars to hello.py"
  │    harness.observe(write_file, success) → writes_since_verify=["hello.py"]
  │    harness.after_tool_call → [nudge]（无 todo，提醒分解）追加到结果
  │
  ├─ round 2: run_step → 模型返回 tool_calls=[]（"完成了"，想结束）
  │    harness.check_termination("完成了")
  │      → VerifyGate: writes_since_verify 非空 → attempt 0 → (True, "[harness] You MUST run it...", None)
  │    ★ 拦截：不 return，注入 [harness] user 消息，continue
  │
  ├─ round 3: run_step → 模型读到 [harness] 要求，返回 tool_calls=[bash(py hello.py)]
  │    _invoke_tool(bash) → "1"
  │    harness.observe(bash) → writes_since_verify 清空, verify_attempts=0
  │
  ├─ round 4: run_step → 模型返回 tool_calls=[]（"完成了，运行输出 1"）
  │    harness.check_termination
  │      → VerifyGate: writes_since_verify 空 → pass
  │      → CompletionGate: todos 空 → 豁免 pass
  │    → (False, None, None) → 真正结束
  │    stream.on_finish → 蓝色 Panel 渲染
  │
  ▼
返回 "完成了，运行验证输出 1"
```

---

## 9. 已知问题与设计债务

诚实记录，待后续处理：

1. **子模块状态**：Lion-Skills 作为目录拷贝存在于 `src/coderio/skills/lion-skills/`，是 vendored 拷贝而非 git submodule，更新需手动同步。用户可通过 `coderio skills install` 从上游 repo 拉取最新版到用户目录。

2. **代码注释/docstring 为重建近似**：部分文件从 `.pyc` 反编译重建（见 git log `d4b24e2`），行为正确（327 测试验证），但注释措辞非原始逐字。

3. **harness 的 `harness_enabled` 只有 run_agent 入口**：crew 路径硬编码 `harness=None`，未来若想让 crew 单个 role 也享受 harness 需 per-role 传入。

4. **deepagents engine 实验性**：`deep_loop.py` + `harness_middleware.py` 实现了 deepagents 引擎（harness 作为 middleware），但未接入 CLI（默认用 ReAct）。deepagents 已改为可选依赖（`pip install coderio[deepagent]`）。

---

## 10. 测试与验证体系

- **327 单元/集成测试**（pytest），覆盖所有模块
- **Live 验证脚本**（`scripts/verify_*_live.py`）：连真实智谱/阶跃端点验证
  - `verify_harness_live.py`：4 场景（验证门触发/通过/禁用/工具错误韧性）
  - `verify_crew_live.py`：crew 流水线真实验证
  - `verify_deepagent_live.py`：deepagents engine 验证（实验性）
- **VS Code tasks**（`.vscode/tasks.json`）：REPL 启动、测试、live 验证一键运行

测试设计原则：mock 只 mock 模型，工具是真的（避免"mock 通过但真实 provider 翻车"——这是项目历史教训）。

---

## 11. 设计哲学小结

1. **skill 是手册，harness 是纪律，工具是手**——三层不互相替代。skill 告诉"该怎么做"，harness 强制"必须这么做"，工具让它"能这么做"。
2. **硬约束靠 ground truth，软路由靠提示词**——harness 读工具调用历史（事实），意图分类读用户消息（语义）。两者正交。
3. **工具错误是信号不是错误**——agent 的容错边界只在 LLM API 层，工具调用失败是模型该读到并自我修正的反馈。
4. **物理隔离胜过提示词约束**——crew 的 Clarifier 没有 write_file 工具，比"提示词叫它别写"可靠得多。
5. **逐级升级，永不无限循环、永不静默**——harness 拦截 2 次后放行+警告，既硬又不卡死。
