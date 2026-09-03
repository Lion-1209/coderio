# coderio 架构设计文档

- **文档版本**：2026-09-03（基于实际代码库，随代码演进更新；测试与规模数据以 §10 / CI 实测为准）
- **代码规模**：15,000+ 行 Python（src/coderio），测试与规模数据以 §10 / CI 实测为准
- **技术栈**：Python 3.11 + langchain + langgraph + Textual + Rich + Typer，Windows 优先
- **Skill 底座**：Lion-Skills 0.3.0（12 skill，bundled 随包）
- **CI**：GitHub Actions，lint（ruff E/F/W/I/S）+ test matrix（Ubuntu/Windows/macOS × Python 3.11/3.12）+ wheel build smoke

> 本文档描述的是**当前代码库的实际状态**（deepagents 引擎重构后）。

---

## 0. 一句话定位

coderio 是一个**技能驱动的编程 agent**：它的"骨架"是 Lion-Skills 套件（clarify→spec→task→execute→verify→commit 这条流水线），coderio 给这套骨架配上真正能干活的工具、一个强制遵循工作流的 **harness 状态控制层**、以及一个基于 deepagents 的交互式 agent 引擎。参照对象是 claude code / codex / zcode。

核心理念（贯穿整个设计）：**skill 是操作手册，harness 是执行纪律，工具是手**。三者分层，互不替代。

---

## 1. 总体架构：分层单体

分层单体（layered monolith），依赖**严格单向向下**。没有 IPC、没有微服务、没有独立进程——所有模块在同一个 Python 进程里，上层调下层，下层不反向依赖。

```
┌─────────────────────────────────────────────────────────────┐
│  CLI 层 (cli/)          Typer app + Textual TUI + 流式 UI     │
│    app.py · tui.py · repl.py · stream.py · commands.py       │
├─────────────────────────────────────────────────────────────┤
│  Agent 层 (agent/)      deepagents 引擎 + harness/permission   │
│    deep_loop.py · harness_middleware.py · permission_*.py     │
│    harness.py · prompts.py · stream.py                        │
├─────────────────────────────────────────────────────────────┤
│  能力层                  tools/ · skills/ · llm/ · session/ · config/  │
└─────────────────────────────────────────────────────────────┘
```

### 模块职责与规模

| 模块 | 行数 | 职责 | 关键文件 |
|------|------|------|----------|
| `agent/` | ~3600 | deepagents 引擎、harness/permission middleware、计划产物、自定义子代理、提示词构建、流式协议 | deep_loop.py, harness_middleware.py, permission_middleware.py, harness.py, plan_artifact.py, custom_agents.py, prompts.py |
| `cli/` | ~5300 | Typer 应用、Textual TUI（渲染）+ TuiRuntime（输入分发/会话生命周期）、自定义 slash 命令、Rich 流式 UI、凭证/onboarding | tui.py, tui_runtime.py, repl.py, stream.py, app.py, commands.py, custom_commands.py, tui_onboarding.py |
| `tools/` | ~3000 | 工具集 + 文件写入 checkpoint + 权限门 + 命令黑名单 + OS 沙箱 + langchain 适配 | bash.py, permission.py, command_policy.py, checkpoint.py, base.py |
| `config/` | ~900 | 三层 TOML 配置合并 + 仓库信任门 + 用户目录 bootstrap | loader.py, models.py, trust.py |
| `skills/` | ~255 | SkillStore 三层加载（bundled < user < project）+ 共享 frontmatter 解析 | store.py, parser.py, models.py |
| `session/` | ~330 | jsonl 追加式会话存储 + resume + 压缩截断 | store.py, message.py |
| `llm/` | 320 | 模型工厂 + provider context window 探测 | factory.py, probe.py |

### 依赖方向（关键约束）

```
cli/ ──调──► agent/deep_loop.run_deep_agent ──调──► tools/, skills/, session/, config/, llm/
agent/ ──不反向依赖──► cli/   (cli 是壳，agent 是核)
```

---

## 2. 运行引擎：deepagents + coderio middleware

coderio 使用 [deepagents](https://github.com/langchain-ai/deepagents) 作为主引擎，在其上叠加 coderio 的 harness 和权限 middleware。

### 2.1 deepagents 引擎（主引擎）

- **入口**：`coderio` 命令 → `cli/tui.py` → `agent/deep_loop.py:run_deep_agent`
- **引擎**：`deepagents.create_deep_agent`，提供上下文管理（offload + 摘要）、子 agent（task 工具）、文件系统后端
- **coderio middleware**：HarnessMiddleware（四道门硬约束）+ PermissionMiddleware（四级权限 + 工作区边界）
- **流式**：三模式 stream（messages 逐 token / updates 完整消息 / custom harness 信号）

### 2.2 为什么不直接用 deepagents 裸跑

deepagents 是"信任 agent"的——它不强制验证、不检查引用、不管工作区边界。coderio 的差异化全在 middleware 层：

| middleware | coderio 独有 | deepagents 没有 |
|---|---|---|
| HarnessMiddleware | 四道门硬约束（验证/完成/grounding/plan） | 不强制验证 |
| PermissionMiddleware | 四级权限（plan/confirm/auto_edit/full）控制工具执行 | 仅粗粒度 FilesystemPermission |

---

## 3. Agent 层：deepagents 引擎 + harness 状态控制（核心）

这是 coderio 最关键的设计——**把"遵循工作流"从软提示词规则变成系统级结构约束**。

### 3.1 为什么需要 harness（设计动机）

裸 agent 循环（模型调工具→看结果→再调→…→说"完成"）有一个致命弱点：**模型说"完成"就结束**。如果模型写了 500 行代码但从未运行就声称"我做完了"，循环立即返回——产出未经验证。

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

- **"已验证"定义**：跑过 bash 且 exit_code=0 = 验证通过。bash 失败（非 0 退出码）**不算验证通过**——退出码从 BashTool 结果的 `[exit_code: N]` marker 解析。避免 agent "跑了一下报错就说验证了"，又阻止"写完就说完成"。
- **智能跳过非代码文件**：写 `.md`/`.json`/`.yaml`/`.txt`/`.toml` 等文档/配置文件不触发 VerifyGate——读一遍确认格式即可，不需要跑 pytest。只有 `.py`/`.js`/`.ts`/`.go`/`.rs` 等真正的代码文件才需要 bash 验证。

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
- **"已读"定义**：`HarnessState.content_read_files` 记录所有 read_file 的 path（归一化：小写 + 正斜杠 + 折叠 `..`）。匹配是 basename 或完整路径精确比较。
- **路径归一化**：`_norm_path()` 统一小写 + 正斜杠 + 折叠 `../`，Windows 大小写不敏感文件系统（NTFS/APFS）正确处理 `Loop.py` == `loop.py`。
- **跳过不存在的路径**：`not_found_files` 记录 read_file 返回 "file not found" 的路径，gate 不再强制模型读不存在的文件（正则可能匹配到文档文本里的路径字符串）。
- **只守代码声明，不碰对话**：正则要求有点扩展名（`.py/.js/.md...`），纯散文（"the loader"、"step 2"、"config.harness"无扩展名）不触发
- **为什么需要**：这是 coderio 自分析项目时踩过的坑——它读了文档没读源码，就断言"config.harness 未接入 loader"（实际三处全通）。文档是意图、代码是现实，两者 gap 就是 bug 栖身处。软规则"先读再下结论"已被证明会被跳过，所以做成硬结构约束。和 VerifyGate 同构：基于 ground truth（工具历史），不信任模型自述。

### 3.4 harness 在循环里的接线

`run_deep_agent`（`agent/deep_loop.py`）是 deepagents 引擎的入口，harness 作为 middleware（`HarnessMiddleware`）挂载，通过 deepagents 的 hook 机制（wrap_tool_call / after_model）实现四道门：

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

## 5. CLI 层与流式 UI

### 5.1 REPL 结构（`cli/repl.py`）

- `build_runtime(...)` → 8 元组（cfg, store, model, tools, gate, session, active, stream）
- `_loop(...)`：`▸ you` 提示符 → slash 命令分发 / 普通 agent turn
- slash 命令（以 `commands.py` 的内置表为准）：`/help /exit /clear /cost /mode /model /setup /sessions /resume /skills /config /export /think /undo /profile`
- `/mode`、`/model` 原地重建（保留 session）

### 5.2 流式 UI（`cli/stream.py: RichStream`）

实现 `StreamHandler` 协议。核心设计：**单个 always-on busy 指示器**，带已用秒数计时器，覆盖整个模型等待周期（思考 + 生成 + 工具间隙），屏幕永不僵死。

关键技术点：
- 用 `Live(get_renderable=self._busy_renderable)` 回调（**不是**静态 renderable）——Rich 自动刷新每 tick 重新调用回调，重新读 `time.monotonic()`，所以计时器和 spinner 同步跳动。这是修复"点在转但秒数不动"bug 的关键。
- assistant 回复用蓝色 Panel（cc 风格），tool 输出折叠 3 行，思考用 spinner + 预览，截断/harness 警告用黄/红 Panel。

### 5.3 on_step_start：消除"卡住"感

`run_deep_agent` 在 stream 循环开始前调 `stream.on_step_start()`，启动 busy 指示器。

### 5.4 StreamHandler 协议（`agent/stream.py`）

```
on_step_start → on_token / on_thinking → on_tool_start → on_tool_end → ... → on_finish
                                                                   on_truncated（截断）
                                                                   on_harness_warn（harness 放行警告）
                                                                   on_harness_continue（harness 强制续跑提示）
                                                                   on_phase_change（任务阶段变化）
                                                                   on_turn_end（轮末文件修改汇总）
is_interrupted()（用户中断检查，agent 线程在每轮开头调用）
```

`NullStream` 全空实现，用于测试/headless。

### 5.5 TUI 交互（`cli/tui.py` + `cli/tui_runtime.py`）

Textual 8.x App，核心设计：

- **渲染与分发分离**（S3 拆分）：`CoderioTUI` 只管 widget 树与流式渲染；输入路由、slash 处理、会话生命周期在 `TuiRuntime`（两阶段构造：先建 runtime，`bind()` 时把带 TUI 引用的 gate 接上——confirm 模式否则会在 Textual 接管终端后死于 input() 死锁）
- **线程模型**：agent 在后台线程跑，UI 更新通过 `_render_q`（thread-safe deque）+ 定时器排空
- **流式渲染**：dict 分派表映射 action → handler，每个 handler 返回 streaming/final/none 决定滚动策略
- **中断**：`Esc` / `⏹ 中断` 按钮 → `_interrupted` 标志位，agent 流循环检查 `is_interrupted()` → `InterruptedError` → 黄色"已中断"面板
- **confirm 模式**：`TuiPermissionGate` 用 `ConfirmMenu`（纵向选择菜单）+ `threading.Event` 跨线程同步，↑↓ 选择 + Enter 确认
- **可视化选择器**：`/mode`（ModePickerScreen）、`/profile`（ProfilePickerScreen）、`/resume`（SessionPickerScreen）
- **文件修改可视化**：写工具结果用黄色 `📝` 行（即时）+ 轮末汇总面板（`on_turn_end`）
- **错误恢复**：异常红色 Panel + 输入框回填失败的用户消息（Enter 重试）
- **空响应中断**：`_empty_response` 用红色 Panel 而非灰色 tool result

---

## 7. 能力层

### 7.1 工具层（`tools/`，12 个工具）

| 类别 | 工具 |
|------|------|
| 读 | read_file, list_dir, glob, grep |
| 写 | write_file, edit_file, multi_edit（写前自动 checkpoint，`/undo` 可回滚）|
| 执行 | bash（Git Bash，Windows 自动探测，.venv 自动激活，进程树超时杀）|
| 计划 | write_todos（deepagents 原生，镜像到 `.coderio/plan.md` 供用户编辑）|
| 外部 | web_search, web_fetch |
| 记忆 | note（跨会话长期记忆）|

**权限门**（`permission.py`）：plan / confirm / auto_edit / full 四模式。`DESTRUCTIVE_TOOLS`（write_file/edit_file/multi_edit/execute/web_fetch/note）在 plan 模式全挡、confirm 模式逐个问、auto_edit 自动放行文件编辑但仍问高危工具、full 全放。

**文件 checkpoint**（`checkpoint.py`，S4）：三个结构化写工具落盘前快照进内存栈（50 条 / 64MB 上限），`/undo` 逐级回滚；bash 重定向等 shell 路径不覆盖——OS 级沙箱才是那一层的答案。

**计划产物**（`plan_artifact.py`，S5）：write_todos 成功后任务清单镜像到 `<project>/.coderio/plan.md`；用户在两轮之间手改它，下一轮开始自动采纳其版本并注入提示。

**命令审查层**（`command_review.py` + `command_policy.py`）：权限门只管"哪个工具能执行"，不管"命令内容是什么"。shell（execute）不受 virtual_mode 约束，所以额外加了一层 `CommandReviewMiddleware`——内置黑名单挡住 `rm -rf /`、`mkfs`、fork bomb、`dd of=/dev/`、`shutdown` 等破坏性命令。即使 FULL 模式也挡（安全优先于"full=全放行"字面语义）。用户可在 config.toml `[tools].blocked_commands` 追加正则黑名单。`network_allowed=false` 可禁用 web_fetch/web_search（离线模式）。

  **这不是真 OS 沙箱**：混淆命令（base64 解码、变量展开）可绕过正则。目标是挡住意外/粗心的破坏（占真实事故绝大多数），不是对抗性模型。真隔离需要容器/seccomp（未来工作）。

**文件路径隔离**：deepagents 后端的 `virtual_mode=True` 把文件工具（write_file/edit_file/read_file/ls/grep/glob）限制在工作区根目录内——agent 看到的 `/foo.py` 映射到 `{workdir}/foo.py`。**注意：shell（execute）不受 virtual_mode 约束**，shell 命令可任意读写工作区外的文件、访问网络。真正的 OS 级沙箱是未来工作。旧的 coderio 自研 `WorkspacePolicy`（路径 resolve + relative_to 边界检查）已删除——它无法处理 deepagents 的虚拟路径（`/foo.py` 被 resolve 成 `C:\foo.py`，总是落在工作区外被误拒）。

**shell（execute）工具特性**：
- **进程树超时杀**：deepagents 后端用 `Popen` + timeout + 进程树 kill（Windows Job Object / POSIX killpg），解决 `subprocess.run(timeout=...)` 在 Windows 上不杀孙子进程导致永久挂起的问题
- **exit_code marker**：`HarnessMiddleware._result_to_text` 从 deepagents 的结构化 ExecuteResponse 提取 exit_code，追加 `[exit_code: N]` 到结果文本，harness VerifyGate 解析它判断验证是否通过
- **注意**：旧的 coderio 自研 bash 工具的 `.venv` 自动激活逻辑在生产路径不生效（deepagents 后端不经过它）。如需 venv，在 shell 命令里显式激活

**统一接口**（`base.py`）：每个工具声明 pydantic `args_schema` + `run()`，经 `to_langchain_tool` 适配成 `StructuredTool` 绑定给模型。

### 7.2 Skill 层（`skills/`，Lion-Skills 0.3.0）

**三层加载**（优先级低→高）：bundled（随包，`src/coderio/skills/lion-skills/`）< user（`~/.coderio/skills/`）< project（`./.coderio/skills/`）。高层覆盖低层。

- `SkillStore._load_layer` 递归 glob `**/SKILL.md`，兼容 Lion-Skills 嵌套布局
- body 懒加载（只在用到时读文件），元数据缓存
- 12 个 Lion-Skills skill（clarifying-questions / spec-writing / task-breakdown / commit-message / code-review / debugging / error-handling / naming / testing / verify-and-fix / onboarding-unknown-codebase / lion-writing-skills）

**skill 激活**：模型通过 `activate_skill(name)` 工具按需加载 skill body（系统提示词里只列名称+描述，~2K tokens）。旧的 `triggers.py` 关键词阶段触发已删除——召回低（"帮我改 bug"不触发）、易误触发（`\bcommit\b` 匹配 "I commit to..."），且引用了不存在的 skill（`executing-plans`）。改为完全依赖模型自主判断 + `activate_skill`。

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
- `model`: default, provider, base_url, provider_id, max_output_tokens=16384, context_limit=0（onboarding 自动探测）
- `tools`: bash_shell, permission_mode, workspace_root=""（空=用 cwd）
- `context`: enabled, trigger_ratio=0.6, keep_recent=8, model_context_limit=200000
- `skills`: auto_load, **harness=True**, repo_url
- `cli`: theme, show_tool_output

### 7.5 Provider 注册表（`cli/providers.py`）+ Context Window 探测（`llm/probe.py`）

7 个 provider：智谱 coding plan / 阶跃 coding plan / 智谱 API / 阶跃 API / OpenAI / Anthropic / Ollama / OpenAI 自定义。coding plan 走 Anthropic 协议，API key 模式走 OpenAI 兼容。

**Context window 探测**（`llm/probe.py`）：onboarding 时 `_verify_and_probe` 调用 `probe_context_limit()` 查询 provider 的 `/v1/models/{id}` 端点，探测真实 context window（如 step-3.7-flash 的 256K）。结果持久化到 `Profile.context_limit` / `ModelConfig.context_limit`。失败时静默退化为默认值（200K），不阻断 onboarding。

### 7.6 会话（`session/`）

jsonl 追加式存储（`~/.coderio/sessions/`）。支持 `Session.create / load / load_by_id / list_recent / append`。Message 有 user/assistant/tool/system 四种 role + tool_calls + kind（phase_timeline / context_summary）。

**压缩持久化 + 截断**：`Session.load` 时调用 `_truncate_at_last_summary`——找到最后一个 `context_summary` system 消息，丢弃它之前的对话消息（user/assistant/tool），保留 system 消息（phase_timeline）。这确保压缩效果跨轮保留，不会重建时加载回未压缩的全量历史。

---

## 8. 数据流：一次 CODE 任务的完整路径

以"写个 hello.py 并测试"为例，单 agent 模式：

```
用户输入 "写个 hello.py 内容 print(1)，写好告诉我完成了"
  │
  ▼
repl._loop → run_agent(harness_enabled=True)
  │  1. build_system_prompt（注入意图分类 + core chain + skill 列表）
  │  2. 构造 Harness（找到 TodoStore）
  │  3. session.append(user msg)
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

2. **harness 作为 deepagents middleware**：`HarnessMiddleware` 通过 `after_model` 的 `jump_to="model"` 实现 force-continue（需 `@hook_config(can_jump_to=["model"])` 装饰器，否则 langchain factory 不建条件边导致静默失效）。

3. **deepagents 是唯一引擎**：`deepagents >=0.7.6,<0.8` 在主 dependencies（版本下限 2026-08-28 审计后修正）。旧 ReAct 引擎（`loop.py`）已完全删除——deepagents 是唯一生产引擎，没有 fallback。测试用 fake model + 真实 graph 覆盖，Live 脚本（`scripts/verify_deepagent_live.py`）用真实 provider 验证。

4. **shell 内容审查是正则级，不是安全边界**：deepagents 后端 `virtual_mode=True` 限制文件工具路径；shell（execute）命令内容走三层——`command_policy` 黑/白名单（防手滑，正则可被混淆绕过）、权限门、以及 **OS 级沙箱**（Linux bubblewrap 真文件写隔离；Windows job 对象仅资源限制，write 档无文件隔离——见 win_sandbox.py 头注释）。macOS 无 OS 级沙箱。对抗性场景仍应使用 VM；hooks 子进程环境走白名单（不透传完整 os.environ）。

5. **ToolResult 非结构化**：bash exit_code 靠正则从 result 字符串提取 `[exit_code: N]`。如果 provider 或工具版本变化导致 marker 格式漂移，解析会断。长期应改为结构化 ToolResult（含 exit_code 字段）。

6. ~~**无依赖锁文件**~~ 已解决（2026-08-14）：`uv.lock` 已入库，CI 用 `uv sync --frozen` 安装，依赖一致性与 pip-audit 阻断均已上 CI。

7. **真实模型 Live eval 缺失**：1000+ 个 mock/fake 测试不证明真实 provider 的 streaming block、tool-call shape、限流恢复兼容性。需建立至少两个 provider 的 nightly Live eval。

---

## 10. 测试与验证体系

- **1000+ 单元/集成测试**（2026-09-02：1074 collected，CI 全绿），覆盖所有模块；`tests/e2e/` 以黑盒方式驱动真实 Typer app（LLM 边界 stub）
- **CI**（GitHub Actions）：lint（ruff E/F/W/I/S）+ test matrix（Ubuntu/Windows/macOS × Python 3.11/3.12，coverage 卡 75% 下限）+ wheel build smoke + pip-audit 阻断 + mypy 硬门
- **Live 验证脚本**（`scripts/verify_*_live.py`）：连真实智谱/阶跃端点验证
  - `verify_harness_live.py`：4 场景（验证门触发/通过/禁用/工具错误韧性）
  - `verify_deepagent_live.py`：deepagents 引擎验证
- **Release 工作流**：tag 驱动，自动构建 wheel + 真实 venv 安装 + `coderio --help` smoke + bundled skills 验证

测试设计原则：mock 只 mock 模型，工具是真的（避免"mock 通过但真实 provider 翻车"——这是项目历史教训）。

---

## 11. 设计哲学小结

1. **skill 是手册，harness 是纪律，工具是手**——三层不互相替代。skill 告诉"该怎么做"，harness 强制"必须这么做"，工具让它"能这么做"。
2. **硬约束靠 ground truth，软路由靠提示词**——harness 读工具调用历史（事实），意图分类读用户消息（语义）。两者正交。
3. **工具错误是信号不是错误**——agent 的容错边界只在 LLM API 层，工具调用失败是模型该读到并自我修正的反馈。
4. **逐级升级，永不无限循环、永不静默**——harness 拦截 2 次后放行+警告，既硬又不卡死。
5. **站在巨人肩膀上**——deepagents 提供上下文管理/子 agent/文件系统，coderio 聚焦 harness 纪律和权限控制，不重复造轮子。
