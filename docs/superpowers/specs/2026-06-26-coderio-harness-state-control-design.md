# coderio · 单 Agent Loop Harness 状态控制设计

- **日期**：2026-06-26
- **状态**：设计已批准，待写实现计划
- **范围**：`agent/loop.py` 的 `run_agent` / `_execute_turn` 架构重构——给裸 ReAct 循环加一层 **harness 状态控制**（硬约束，非提示词规则）
- **依赖**：S0 的 `run_step`/`Session`/`Message`/`StreamHandler`，S1 的 `RichStream`/`Config`，`tools/todo.py` 的 `TodoStore`
- **触发原因**：实测中 agent 写完 500 行贪吃蛇游戏后直接声明"完成"，从未运行、从未验证——核心链只活在系统提示词里，是软规则，可被忽略

---

## 0. 背景与定位

### 0.1 问题：核心链是软规则

当前 `loop.py` 的 `_execute_turn` 是一个**无状态的 ReAct 循环**：

```
loop:
    ai = run_step(...)              # 调模型
    if ai 无 tool_calls:
        emit(ai 文本); return       # ← 模型说停就停，harness 无权干预
    for tc in ai.tool_calls:
        执行工具; emit(结果)
```

核心链（clarify→spec→task→execute→verify→commit）**只存在于 `build_system_prompt` 注入的文本里**。模型可以——而且实测会——跳过验证直接说"我做完了"。这是贪吃蛇、贪鸽两次测试都翻车的同一根因。

提示词无论写多强（"MANDATORY"、"MUST"）都是**软规则**：模型决定停就停，循环立即返回。这不是 claude code 的做法。

### 0.2 目标：把软规则变成结构约束

claude code 的 harness 在模型说"完成"时**有权不放行**：它会检查 ground truth（工具调用记录），如果发现未验证的代码变更，就**强制注入一条续跑消息**让模型继续，而不是结束。

本次重构给 `_execute_turn` 加一层 **Harness**，它控制两件模型无法覆盖的事：

1. **终止权**：模型返回"无 tool_calls"时，harness 决定是**真正结束**还是**拦截并强制续跑**。
2. **工具结果增强**：harness 可以在工具结果里追加结构性内容（提醒、状态），或在循环中插入**系统注入消息**。

这两者都基于**已发生的工具调用/结果（ground truth）**，不基于模型*声称*自己做了什么。

### 0.3 已确认的关键决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 验证门强度 | **硬门 + 逐级升级** | claude code 式硬约束：未验证的写入拦截终止、注入强制续跑；连续 2 次仍不验证则放行但 UI 显式警告。永不无限循环、永不静默放水 |
| 计划门强度 | **软提醒** | todo 为空时在工具结果追加提醒，不阻断。保留改错别字等平凡任务的顺畅；完成声明时的 todo 完成度检查是硬门 |
| harness 位置 | `_execute_turn` 内部一层 | 它已经是 S0/S2 共享的 turn 循环；harness 接管"是否结束"和"工具结果是否增强" |
| 状态来源 | 观察到的工具调用/结果 | 不是模型的自述；写文件工具返回 `Wrote N chars` 即视为一次写入 |
| 配置开关 | `Config.skills.harness` (默认开) | 可关，便于测试/排错；默认开启 = 新行为 |

### 0.4 与现有 stage_auto_inject 的关系

- `stage_auto_inject`（`triggers.py`）：会话**开始时**按用户输入关键词预激活 skill。**保留**，是入口侧的 skill 预热。
- 本 harness：循环**进行中**的结构约束。两者正交，互不影响。

---

## 1. 三道门（机制定义）

Harness 在 `_execute_turn` 的每一轮循环里运行三道门。它们读**工具调用历史**和 **todo 状态**，产出"拦截"决策。

### 1.1 计划门（软）—— `PlanGate`

**触发**：模型调用 `write_file` / `edit_file` / `multi_edit`，且此时 `TodoStore.todos` 为空。

**动作**：在工具结果字符串**末尾追加**一段提醒（不阻断执行，工具照常跑）：

```
[nudge] You're writing code but have no task list yet. For non-trivial
work, call todo(action="add", ...) first to decompose the task into
verifiable steps. (Trivial fixes like a typo can ignore this.)
```

**为什么软**：改一个错别字、加一行注释也要先建 todo 是过度摩擦。提醒施压 + 完成门的硬检查兜底。

**实现**：`_execute_turn` 执行工具后，调用 `harness.after_tool_call(name, args, result)` —— 若返回增强文本，替换 `result` 再 emit。

### 1.2 验证门（硬，逐级升级）—— `VerifyGate` ★核心

**触发**：模型返回**无 tool_calls 的文本**（即想结束），且**存在未验证的写入**。

**"未验证的写入"定义**：自上次 `bash`（或显式验证动作）以来，发生过 ≥1 次成功写入（`write_file`/`edit_file`/`multi_edit` 返回非 Error 的结果）。

> 即：写完后没跑过 bash = 未验证。跑过 bash（哪怕命令失败）= 视为已尝试验证，门放行。这避免 agent 用"跑了一下报错"卡死，又阻止"写完就说完成"。

**逐级升级动作**（由 `VerifyGate.attempt` 计数器决定）：

| attempt | 动作 | 注入消息（作为续跑的 user/system 消息） |
|---------|------|----------------------------------------|
| 0（首次拦截） | **强制续跑**，注入明确指令 | `[harness] You wrote code but haven't verified it. You MUST run it (use bash to execute/test/lint the files you changed) before declaring done. Do not summarize or claim completion — call bash now.` |
| 1（第二次拦截） | **强制续跑**，注入更严厉指令 + 列出未验证文件 | `[harness] STILL no verification. Files written but not run: a.py, b.html. Run them with bash now. Do NOT reply with text — call bash.` |
| 2（第三次） | **放行 + UI 警告** | 不再拦截，`stream.on_harness_warn(...)` 显示醒目警告面板，文本正常返回 |

**为什么 2 次就放行**：防止无限循环（模型若真不肯验证，硬卡死对用户无益）；但**绝不静默**——放行时必出警告。这是"逐级升级"：硬 → 更硬 → 带警告放行。

**实现**：`_execute_turn` 在 `if not tool_calls: return text` 之前，先问 `harness.should_continue(text)`：
- 返回 `(True, inject_msg)`：不 return，把 `inject_msg` 作为一条 `Message` 追加到 convo（role=user，带 `[harness]` 标记），继续循环。
- 返回 `(False, warn)`：正常 return；若 `warn` 非空，调 `stream.on_harness_warn(warn)`。

### 1.3 完成门（硬，todo 完成度）—— `CompletionGate`

**触发**：模型返回无 tool_calls 文本，且**已通过验证门**（即已验证或已升级到放行），但 `TodoStore` 有非平凡 todo（≥1 项且存在 status != completed）。

**动作**：与验证门同形的逐级升级——
- attempt 0/1：强制续跑，注入 `[harness] Your task list has N unfinished item(s). Mark them complete via todo(action="update", status="completed") only if truly done, or finish the remaining work. Do not claim overall completion with pending todos.`
- attempt 2：放行 + 警告。

**为什么独立**：验证门管"代码跑没跑"，完成门管"声称做完了但 todo 还有坑"。两者都是硬门，但检查的是不同 ground truth。

**平凡任务豁免**：`TodoStore.todos` 为空时此门**跳过**（没有 todo 的小问答/小修改不应被卡）。这与计划门呼应：计划门软提醒建 todo，完成门只在 todo 存在时硬检查。

### 1.4 门的执行顺序

每次模型返回无 tool_calls 文本时，harness **按序**检查（短路）：

```
text 无 tool_calls 时:
    if VerifyGate.should_continue():  → 强制续跑（最高优先级，代码没跑一切免谈）
    elif CompletionGate.should_continue():  → 强制续跑
    else:  → 正常 return（真正完成）
```

工具调用后的增强只涉及计划门，与上述终止检查正交。

---

## 2. 数据结构

### 2.1 HarnessState

新增 `agent/harness.py`。Harness 是**纯函数式状态机**——状态从工具调用历史派生，没有模型能写的隐藏字段。

```python
@dataclass
class HarnessState:
    """Per-turn harness state, derived from observed tool calls (ground truth)."""
    writes_since_verify: list[str] = field(default_factory=list)  # 文件路径，未验证的写入
    last_verify_attempt: int = -1   # 上次 bash 时的 attempt 快照（reset 用）
    verify_attempts: int = 0        # 验证门拦截次数（本 turn）
    completion_attempts: int = 0    # 完成门拦截次数
    plan_nudged: bool = False       # 本 turn 是否已 nudge 过（避免重复提醒）
```

### 2.2 Harness 门接口

```python
@dataclass
class Harness:
    state: HarnessState
    todos: TodoStore                # 引用同一个 TodoStore（计划门/完成门读它）
    enabled: bool = True

    # 工具结果增强（计划门）。返回 None = 不增强。
    def after_tool_call(self, name: str, args: dict, result: str) -> str | None: ...

    # 终止检查（验证门 + 完成门，按序短路）。
    # 返回 (should_continue, inject_message_or_None, warn_message_or_None)。
    def check_termination(self, final_text: str) -> tuple[bool, str | None, str | None]: ...

    # 工具执行后更新内部状态（写/bash 标记）。
    def observe(self, name: str, args: dict, result: str) -> None: ...
```

`observe` 在每次工具执行后被调用，记录：`write_file/edit_file/multi_edit` 成功 → push 路径到 `writes_since_verify`；`bash` → 清空 `writes_since_verify` + reset `verify_attempts`。

> **路径提取**：write_file 的 `args["path"]`；multi_edit/edit_file 的 `args["path"]`。已确认三个工具都有 `path` 字段。

> **成功判定**：结果字符串不以 `"Error"` 开头。三个写工具的错误格式统一是 `Error: ...`（已核对 `write_file.py`/`edit_file.py`/`multi_edit.py`）。

### 2.3 配置

`Config` 新增字段（`config/models.py`）：

```python
@dataclass(frozen=True)
class SkillsConfig:
    auto_load: bool = True
    stage_auto_inject: bool = True
    harness: bool = True            # ← 新增：harness 硬约束开关，默认开
    repo_url: str = "..."
```

`run_agent` 新增 `harness: bool = True` 参数透传到 `_execute_turn`。REPL 从 `cfg.skills.harness` 读。

---

## 3. _execute_turn 改造（核心 diff）

当前 `_execute_turn` 的循环骨架改造点（伪代码，保留所有现有行为）：

```python
def _execute_turn(..., harness: Harness | None = None):
    ...
    for _ in range(max_rounds):
        ai = run_step(bound_cache, langchain_tools, active_prompt, convo, stream)
        tool_calls = list(getattr(ai, "tool_calls", []) or [])

        # === 终止检查（新增，在 return 之前）===
        if not tool_calls:
            text = _content_to_text(getattr(ai, "content", ""))
            if harness is not None and harness.enabled:
                cont, inject, warn = harness.check_termination(text)
                if cont and inject is not None:
                    # 拦截：不结束，注入续跑消息继续循环
                    msg = Message.user(inject)
                    convo.append(msg)
                    if on_message: on_message(msg)
                    continue
                if warn and hasattr(stream, "on_harness_warn"):
                    stream.on_harness_warn(warn)
            _emit(Message.assistant(text))
            stream.on_finish()
            return text

        # === 工具执行（现有）===
        _emit(Message.assistant(... tool_calls ...))
        for tc in tool_calls:
            ...执行...
            # === 工具后增强（新增：计划门 + observe）===
            if harness is not None and harness.enabled:
                harness.observe(name, args, result)
                aug = harness.after_tool_call(name, args, result)
                if aug:
                    result = aug   # 用增强后的结果 emit
            stream.on_tool_end(name, result)
            _emit(Message.tool_result(..., content=result))
            ...
```

**改动量**：3 处插入（终止检查块、observe、after_tool_call 增强），其余完全不动。S2 crew 的 `_execute_turn` 调用传 `harness=None` 保持原行为（crew 有自己的 verify→修复循环，见 §6）。

### 3.1 round 预算

验证门/完成门的强制续跑**消耗 round**（每次拦截 = 1 轮）。为避免吃掉正常工作预算：
- 默认 `max_rounds=25`，harness 最多加 2+2=4 轮拦截，余 21 轮给正常工作，充足。
- 若 round 耗尽仍卡在门上，走现有 `"Stopped: reached max rounds"` 兜底（不会静默假完成）。

---

## 4. 注入消息的呈现

### 4.1 stream 协议扩展

`agent/stream.py` 的 `StreamHandler` 新增一个**可选**钩子：

```python
def on_harness_warn(self, message: str) -> None: ...
```

`NullStream` 加空实现。`RichStream` 实现为**醒目黄色警告面板**（区别于普通蓝色助手面板）：

```
┌─ ⚠ harness 警告 ────────────────────────┐
│ agent 写入了代码但未运行验证后声明完成。    │
│ 产出可能未经验证，请人工复核。              │
└──────────────────────────────────────────┘
```

### 4.2 续跑消息的可见性

强制续跑注入的 `[harness] ...` 消息以 user role 进入 convo（模型视角是"用户追加了硬要求"）。在 UI 上，harness 注入消息前缀 `[harness]`，`RichStream`/会话回放可据此识别并**降亮显示**（dim 样式），不与真实用户输入混淆。**第一版不过度设计 UI**：注入消息作为普通 user 消息进 convo 即可，模型能读到就行；UI 区分留作后续打磨。

### 4.3 会话持久化

注入的续跑消息通过现有 `on_message` 回调写入 session jsonl（role=user，content 带 `[harness]` 前缀）。`/resume` 重放时能看到 harness 介入过。这是结构约束可审计的体现。

---

## 5. 为什么这是"系统级"而非"加规则"

用户的核心要求：**harness 必须是系统级结构约束，不是软规则**。本设计满足的三条判据：

| 判据 | 软规则（现状） | 结构约束（本设计） |
|------|---------------|-------------------|
| 模型能否绕过 | 能（提示词可忽略） | **否**——终止权在 harness，模型说停 harness 可以不让停 |
| 基于 ground truth | 否（基于模型自述） | **是**——读工具调用历史，写没写、跑没跑是事实 |
| 可审计 | 否（静默） | **是**——注入消息入 session、警告面板可见、有 attempt 计数 |

关键：**终止决策权从模型手里移到了 harness 手里**。这是 claude code 的本质——不是"请模型自觉验证"，而是"你不验证我不让你结束"。

---

## 6. 与 S2 crew 的关系

- S2 `CrewOrchestrator` 已有自己的 verify→修复循环（`_verification_passed` 启发式 + `max_fix_loops`），它调用 `_execute_turn` 时传 `harness=None`，**保持原行为**。
- 本 harness 是 **S1 单 agent REPL** 的结构约束。两套机制职责不同：crew 是 agent 间接力验证，harness 是单 agent 内的自验证。不冲突。
- 未来若要让 crew 的单个 role 也享受 harness，可 per-role 传 harness；本次不做。

---

## 7. 测试矩阵（实现计划须覆盖）

| 测试 | 场景 | 断言 |
|------|------|------|
| `test_verify_gate_blocks_unverified_done` | 模型写文件后直接回文本 | harness 拦截，注入续跑消息，convo 出现 `[harness]` user 消息，循环继续 |
| `test_verify_gate_passes_after_bash` | 写文件→bash→回文本 | 不拦截，正常 return，无警告 |
| `test_verify_gate_escalation_to_warn` | 写文件后连续 3 次回文本不 bash | 第 3 次放行，`on_harness_warn` 被调用，返回文本 |
| `test_verify_gate_resets_on_bash` | 写→bash→又写→回文本 | 第二次写入触发拦截（bash 只清前面的） |
| `test_plan_gate_nudge_on_write_no_todos` | 无 todo 时 write_file | 结果含 `[nudge]`，工具照常执行 |
| `test_plan_gate_no_nudge_with_todos` | 有 todo 时 write_file | 结果无 `[nudge]` |
| `test_plan_gate_nudge_once_per_turn` | 同一 turn 多次 write 无 todo | 只 nudge 第一次 |
| `test_completion_gate_blocks_pending_todos` | 有未完成 todo，已验证，回文本 | 拦截，注入 todo 完成度消息 |
| `test_completion_gate_skipped_no_todos` | 无 todo，回文本 | 不拦截（平凡任务豁免） |
| `test_harness_disabled_passthrough` | `harness=None` 或 `enabled=False` | 行为与改造前完全一致（回归） |
| `test_failed_write_not_counted` | write_file 返回 `Error:...` | 不计入未验证写入，不触发验证门 |
| `test_crew_unaffected` | S2 传 `harness=None` | crew verify 循环行为不变（回归） |

新增测试文件：`tests/agent/test_harness.py`（门逻辑，纯单元）+ `tests/agent/test_loop.py` 追加集成测试。

---

## 8. 文件改动清单

| 文件 | 改动 | 性质 |
|------|------|------|
| `src/coderio/agent/harness.py` | **新增**：`HarnessState` + `Harness`（三道门逻辑） | 新文件 |
| `src/coderio/agent/loop.py` | `_execute_turn` 加 3 处插入 + `run_agent` 透传 harness | 改造（核心） |
| `src/coderio/agent/stream.py` | `StreamHandler` 加 `on_harness_warn`；`NullStream` 实现 | 协议扩展 |
| `src/coderio/cli/stream.py` | `RichStream.on_harness_warn` 警告面板 | UI |
| `src/coderio/config/models.py` | `SkillsConfig.harness` 字段 | 配置 |
| `src/coderio/cli/repl.py` | `run_agent(...)` 传 `harness=cfg.skills.harness`；`build_runtime` 让 TodoStore 可共享 | 接线 |
| `tests/agent/test_harness.py` | **新增**：门逻辑单元测试 | 新文件 |
| `tests/agent/test_loop.py` | 追加 harness 集成测试 | 扩展 |

**不改动**：`tools/*`（只读它们的 args/result）、`prompts.py`（核心链提示词保留，作为软引导与硬门互补）、`crew/*`（传 None 保持原行为）。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 逐级升级仍可能让 agent 困在"写→被拦→又写→被拦" | round 预算兜底（max_rounds），到顶走 "Stopped" 不静默；attempt≥2 放行 |
| TodoStore 跨 turn 不共享会导致完成门误判 | REPL 的 TodoStore 需在 turn 间持久（见 §3.1，`build_runtime` 持有它）；当前 `build_default_tools` 每次新建，需调整为 REPL 持有一个共享实例 |
| 模型把 `[harness]` 消息当普通对话回复（"好的我这就验证"但又不调 bash） | 续跑消息明确写 "Do NOT reply with text — call bash"；第 2 次拦截列文件名施压；attempt 计数确保最终放行不卡死 |
| 注入消息污染 /cost token 统计 | 注入消息很短（~100 token），可忽略；usage 统计已含所有轮次，无需特殊处理 |
| 配置层 frozen dataclass 改动 | `SkillsConfig` 已是 frozen dataclass，加字段有默认值，向后兼容 |

---

## 10. 验收标准（"做完"的定义）

1. `harness.py` 三道门单测全绿（§7 矩阵）。
2. `test_loop.py` 集成测试证明：写文件后直接声明完成 → 被拦截续跑；跑 bash 后 → 放行。
3. `harness=None` / `enabled=False` 时，现有 192 个测试**零回归**。
4. 手测：REPL 里让 agent 写个小脚本但不运行，观察 harness 拦截 + 最终警告面板。
5. S2 crew 任务不受影响（`verify_s2_live.py` 仍通过）。
