# 实现计划 · 单 Agent Loop Harness 状态控制

- **对应 spec**：`docs/superpowers/specs/2026-06-26-coderio-harness-state-control-design.md`
- **目标**：给 `_execute_turn` 加 harness 层（三道门），把"写完不验证就说完成"从软规则变结构约束
- **基线**：192 测试通过；本计划完成后须零回归 + 新测试全绿

---

## 阶段 0 · 准备

- [ ] 0.1 跑一遍现有测试确认基线绿：`.venv/Scripts/python.exe -m pytest -q`（记录通过数）
- [ ] 0.2 确认 `write_file`/`edit_file`/`multi_edit` 的成功/错误返回格式（已核对 spec §2.2：成功 `Wrote/Edited ...`，错误 `Error: ...`）

## 阶段 1 · harness 核心逻辑（纯单元，无 loop 依赖）

新增 `src/coderio/agent/harness.py`：

- [ ] 1.1 `HarnessState` dataclass（`writes_since_verify: list[str]`, `verify_attempts: int`, `completion_attempts: int`, `plan_nudged: bool`）
- [ ] 1.2 `Harness` dataclass：
  - `observe(name, args, result)`：写工具成功→push path；bash→清空 writes + reset verify_attempts=0
  - `after_tool_call(name, args, result) -> str | None`：计划门——write 类工具 + todos 空 + 未 nudge → 追加 `[nudge]` 文本；否则 None
  - `check_termination(text) -> (should_continue, inject, warn)`：验证门（attempt 0/1 注入续跑、2 放行+warn）→ 完成门（同形，仅 todos 非空时生效）
  - 常量：`WRITE_TOOLS = {"write_file","edit_file","multi_edit"}`，`VERIFY_TOOL = "bash"`，`_MAX_ATTEMPTS = 2`
  - 注入消息文案见 spec §1.2/§1.3
- [ ] 1.3 `_is_success(result)`：`not result.startswith("Error")`
- [ ] 1.4 新增 `tests/agent/test_harness.py`，覆盖 spec §7 前 9 条（纯单元，不涉及 loop）：
  - observe 正确记录/reset
  - 计划门 nudge 触发/不触发/只一次
  - 验证门拦截/放行/升级/重置
  - 完成门拦截/无 todo 豁免
- [ ] 1.5 阶段验证：`pytest tests/agent/test_harness.py -q` 全绿

## 阶段 2 · stream 协议 + NullStream

- [ ] 2.1 `agent/stream.py`：`StreamHandler` 加 `on_harness_warn(self, message: str) -> None`（注释标"可选"）
- [ ] 2.2 `NullStream` 加 `def on_harness_warn(self, message): pass`
- [ ] 2.3 阶段验证：现有 `tests/agent/test_loop.py` 仍绿（NullStream 实现了新钩子）

## 阶段 3 · _execute_turn 接线（核心改造）

改 `src/coderio/agent/loop.py`：

- [ ] 3.1 `_execute_turn` 签名加 `harness: Harness | None = None`（默认 None = 原行为，向后兼容）
- [ ] 3.2 在 `if not tool_calls:` 块**开头**插入终止检查：`harness.check_termination` → `(True, inject, _)` 则 append `[harness]` user 消息到 convo + `on_message` + `continue`；`(False, _, warn)` 则 `on_harness_warn(warn)` 后走正常 return
- [ ] 3.3 在工具执行循环里，`stream.on_tool_end` **之前**：`harness.observe(name,args,result)` + `aug = harness.after_tool_call(...)`，若 aug 非空则 `result = aug`
- [ ] 3.4 `run_agent` 签名加 `harness_enabled: bool = True`；若 True 则构造 `Harness(HarnessState(), todos=<todo store>)` 传入 `_execute_turn`
- [ ] 3.5 `run_agent` 从 tools 列表里**找 TodoTool 的 store**（遍历找 `TodoTool`，取 `.store`）——避免改 build_default_tools 签名
- [ ] 3.6 阶段验证：`pytest tests/agent/test_loop.py -q`（含新增集成测试，见阶段 5）

## 阶段 4 · 配置 + REPL 接线

- [ ] 4.1 `config/models.py`：`SkillsConfig` 加 `harness: bool = True`
- [ ] 4.2 `cli/repl.py`：`run_agent(...)` 调用传 `harness_enabled=cfg.skills.harness`
- [ ] 4.3 确认 REPL 的 `tools` 在 turn 间复用同一 TodoStore（当前 `_loop` 持有 tools 列表不变即满足）——若 `/mode` 等重建 tools 时 TodoStore 会重置，这是可接受行为（切模式=新上下文）
- [ ] 4.4 `cli/stream.py`：`RichStream.on_harness_warn` 渲染黄色 `Panel`（标题 `⚠ harness 警告`）
- [ ] 4.5 阶段验证：`pytest tests/cli/ -q` 绿；`tests/config/` 绿

## 阶段 5 · loop 集成测试

`tests/agent/test_loop.py` 追加（用现有 `_model_returning` mock，多步序列）：

- [ ] 5.1 `test_verify_gate_blocks_unverified_done`：write_file→纯文本 → 拦截，convo 有 `[harness]` user 消息
- [ ] 5.2 `test_verify_gate_passes_after_bash`：write→bash→纯文本 → 正常 return 无 warn
- [ ] 5.3 `test_harness_disabled_passthrough`：`harness_enabled=False` → 行为同改造前
- [ ] 5.4 `test_failed_write_not_counted`：write 返回 `Error:...` → 不触发验证门
- [ ] 5.5 阶段验证：全绿

## 阶段 6 · 回归 + 收尾

- [ ] 6.1 全量回归：`pytest -q`，确认 192 → 192+N 全绿，零回归
- [ ] 6.2 S2 crew 不受影响：`pytest tests/crew/ -q`（harness=None 路径）
- [ ] 6.3 检查 `_execute_turn` 的 S2 调用点（`orchestrator.py`）仍传 None / 不传 harness（向后兼容默认 None）
- [ ] 6.4 手测脚本（可选，留给用户）：REPL 写小脚本不运行，观察拦截 + 警告面板

## 验收

- [ ] spec §10 五条全部满足
- [ ] harness 是结构约束（终止权在 harness、基于 ground truth、可审计）三判据成立
