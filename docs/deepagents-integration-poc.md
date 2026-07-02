# Deepagents 集成 PoC 报告

- **日期**：2026-07-02
- **状态**：实验性集成（engine 可用，但未达生产级）
- **范围**：`agent/harness_middleware.py` + `agent/deep_loop.py`（与现有 ReAct engine 并存）

## 做了什么

把 coderio 的 harness（"写完必须验证"硬约束）移植成 deepagents 的 `AgentMiddleware`，并提供 `run_deep_agent` 入口与现有 `run_agent` 并存。

### 已验证可行（实测）

| 项 | 结果 |
|----|------|
| deepagents 0.6.12 安装 | ✅ 网络/依赖就位 |
| `create_deep_agent(model=ChatAnthropic(智谱))` 跑真实端点 | ✅ |
| middleware `after_model` 返回 `jump_to:'model'` 强制续跑 | ✅（harness 核心机制在 deepagents 成立） |
| `wrap_tool_call` observe write/execute（ground truth） | ✅ |
| LocalShellBackend 写真实磁盘 | ✅（需 `virtual_mode=True`） |
| Windows GBK shell 输出编码崩溃 | ✅ 已修（`_WinLocalShellBackend` 用 errors='replace'） |
| HarnessMiddleware 单元测试 | ✅ 13 passed |
| 现有 251 测试零回归 | ✅ |

### 未解决（阻塞生产可用）

1. **模型不主动 execute 验证**：deepagents 默认提示词不教"写完要验证"。注入 coderio 的 27k 系统提示词后，每次模型调用太慢（3 分钟超时）。harness 虽能拦截（实测 intercept=True），但模型被强制续跑后仍不调用 execute，2 次升级后放行。

2. **提示词与性能张力**：coderio 的完整系统提示词（CORE_CHAIN_SKILLS body 注入后 ~27k 字符）对 deepagents 的每次模型调用过重。

3. **工具名映射**：deepagents 用 `file_path`（coderio 用 `path`）、`execute`（coderio 用 `bash`）、`write_todos`（coderio 用 `todo`）——已在 middleware 映射，但系统提示词里的 "bash" 措辞需进一步对齐。

## 架构判断

deepagents 的 harness 抽象和 coderio 的"写完必须验证"纪律在 prompt 层面有张力：
- ReAct engine（run_agent）能工作：精简提示词 + `_execute_turn` 让模型更顺从
- deepagents 重型中间件栈（TodoList/Filesystem/Summarization/PatchToolCalls）增加模型行为不确定性

## 当前处置

- `run_deep_agent` 保留为**实验性 engine**，不在 REPL 默认启用
- 现有 ReAct engine（run_agent + harness + crew）不变，仍是默认
- 配置开关 `engine: react|deepagent` 待接入（阶段 3），默认 react
- 后续若要让 deepagent engine 达到生产级，需：精简 deepagents 专用提示词 + 调优 middleware 顺序

## 文件清单

| 文件 | 作用 |
|------|------|
| `agent/harness_middleware.py` | HarnessMiddleware：deepagents 适配层 |
| `agent/deep_loop.py` | run_deep_agent 入口 + _WinLocalShellBackend（Windows 编码修复） |
| `tests/agent/test_harness_middleware.py` | 13 单元测试 |
