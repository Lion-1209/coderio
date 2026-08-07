# coderio 最新提交详细分析报告

- 项目：<https://github.com/Lion-1209/coderio>
- 分析日期：2026-08-04（Asia/Shanghai）
- 被测分支：`main`
- 被测提交：`34b8849674a1bc7a63d1ea644d6488e4e1ff6c2d`
- 上一轮基线：`2b9b54b3a44c5423d52b79945b17189dc2dfe58a`
- 最终评分：**7.4/10**
- 版本趋势：**上升（7.0 → 7.4，+0.4）**

> 证据标签：**[实测]** 为本次实际命令或临时探针结果；**[代码审阅]** 为当前源码直接证据；**[推测/待验证]** 表示缺少生产环境或真实模型时无法完全确认。

## 1. 执行摘要

仓库已使用 `git pull --ff-only origin main` 从 `2b9b54b` 严格快进到 `34b8849`。本次共有 **7 个新增提交**，涉及 20 个文件，合计 **753 insertions / 2773 deletions**。更新的核心方向是：

1. 修复上一轮报告中的会话删除路径、Deep Agent 退出码、SQLite 异常资源释放和 CompletionGate 跨轮同步问题；
2. 新增 Deep Agent 辅助函数测试和 9 个 headless TUI 测试；
3. 删除 1,055 行旧 ReAct/compact 生产代码及其大批旧测试，deepagents 成为唯一引擎；
4. 将 `run_deep_agent` 拆分，C901 从上一轮本机实测 22 降到 12；
5. CI 增加 `pip check` 和覆盖率报告；Release 改为安装 wheel 的完整依赖。

本轮质量提升是实质性的：上一轮四个可复现 P1 均通过临时探针确认已关闭；C901 总数从 16 降到 12；Release 安装路径和 TUI 冒烟明显增强。

但当前仍有两个影响评分上限的核心问题：

- **Bash 仍没有文件系统/网络 sandbox**，WorkspacePolicy 只检查 `cwd`，绝对路径重定向和网络命令继续通过策略检查；
- deepagents 已成为唯一生产引擎，但 `deep_loop.py` 覆盖率只有 **20%**，`permission_middleware.py` 只有 **37%**；新增测试明确只测辅助函数，没有实际调用 `run_deep_agent` 或完整 graph。

本机结果为 **438 passed, 3 skipped，覆盖率 68%**。三个 skip 全部来自当前复用环境缺少 `langgraph-checkpoint-sqlite`；仓库 CI 会安装完整依赖，因此 CI 中理论上应执行这些测试，但本报告没有核查远端 Actions 历史。

综合判断：项目已从“功能丰富但关键修复未闭环”进步到“主架构更单一、正常路径更可靠、测试可观测性更好”的阶段，评分上调到 **7.4/10**。与 Claude Code、Codex 等成熟编码 Agent 相比，主要差距仍是安全隔离、唯一生产引擎的集成验证、依赖可重复性和发布级运行冒烟。

## 2. 被测版本与新增提交

| 项目 | 结果 |
|---|---|
| 拉取命令 | `git pull --ff-only origin main` |
| 拉取结果 | `2b9b54b..34b8849`，Fast-forward |
| 当前分支 | `main` |
| 完整 SHA | `34b8849674a1bc7a63d1ea644d6488e4e1ff6c2d` |
| HEAD 时间 | `2026-08-04T10:22:13+08:00` |
| HEAD 摘要 | `refactor: split run_deep_agent (complexity 23→12) + CI coverage baseline` |
| 拉取前未提交内容 | 仅此前生成的 `outputs/` 报告 |
| 源码状态 | 拉取后无本地源码改动 |

### 2.1 七个新增提交

| SHA | 提交摘要 | 核心影响 |
|---|---|---|
| `a311c31` | resolve prompt conflict + update docs | 清空 deepagents 基础提示词，改由 coderio prompt 独占 |
| `660a725` | fix P1 issues from audit report | 修复删除目录、exit_code、SQLite 异常关闭、Todo 失败同步 |
| `73a6033` | deepagents engine core function tests | 新增 234 行 Deep Agent 辅助函数测试 |
| `82b1417` | remove dead code + stale comments | 小规模清理 |
| `d39596e` | remove dead ReAct engine | 删除 `loop.py`、`compact.py` 及对应旧测试 |
| `584195a` | audit fixes + headless TUI tests | 跨轮 todo、双 Del 确认、完整依赖 Release、TUI 冒烟 |
| `34b8849` | split run_deep_agent + coverage baseline | 拆分入口函数，CI 输出覆盖率 |

### 2.2 变更规模

```text
20 files changed
753 insertions(+)
2773 deletions(-)
```

删除内容包括：

- `src/coderio/agent/loop.py`：886 行；
- `src/coderio/agent/compact.py`：169 行；
- 旧 loop/compact/context-rot/e2e 测试：约 1,487 行。

新增主要包括：

- `tests/agent/test_deep_loop.py`：234 行；
- `tests/cli/test_tui_startup.py`：189 行。

删除旧引擎本身是积极的：减少了“双引擎行为不一致”和维护死代码的风险。但测试迁移不完全，唯一生产引擎的关键主流程覆盖仍偏低。

## 3. 实际执行命令与结果

环境：macOS 26.5.2 arm64、Python 3.12.13、pytest 9.1.1、Ruff 0.15.22。当前仓库无 `.venv`，复用上一轮虚拟环境，没有安装或升级依赖。

| 命令 | 原始结果摘要 | 判定 |
|---|---|---|
| `python -m compileall -q src tests` | 无输出，exit 0 | 通过 |
| `ruff check src tests` | `All checks passed!` | 通过 |
| `ruff format --check src tests` | `115 files already formatted` | 通过 |
| `ruff check src --select C901` | `Found 12 errors` | 未通过；比上一轮 16 项改善 |
| `ruff check src --select S` | `All checks passed!` | 通过；不等于没有真实安全问题 |
| `python -m pip check` | `No broken requirements found.` | 通过；仅代表复用环境内部一致 |
| `coderio --help` | exit 0，命令正常显示 | 通过 |
| `pytest -q --cov=coderio --cov-report=term-missing` | `438 passed, 3 skipped in 45.38s` | 通过 |

项目 HEAD 提交信息记录的是 `440 passed, 1 skipped`；本机差异来自依赖环境。本机三个 skip 均为：

```text
langgraph-checkpoint-sqlite not installed
```

`tests/agent/test_deep_loop.py` 单独执行结果：`13 passed, 3 skipped in 0.17s`。

### 3.1 覆盖率结果

| 模块 | 覆盖率 | 评价 |
|---|---:|---|
| 总体 | **68%** | 已建立可见基线，但暂无门槛 |
| `agent/harness.py` | 98% | 很好 |
| `agent/harness_middleware.py` | 77% | 较好 |
| `agent/deep_loop.py` | **20%** | 唯一生产引擎入口覆盖严重不足 |
| `agent/permission_middleware.py` | **37%** | 核心安全适配层覆盖不足 |
| `cli/tui.py` | 45% | 新增 headless 测试后仍有大量事件路径未覆盖 |
| `tools/bash.py` | 65% | 中等，平台和异常路径仍需补测 |
| `session/store.py` | 95% | 很好 |

CI `.github/workflows/ci.yml:62-68` 已输出 coverage，但明确不设 hard threshold，因此覆盖下降不会阻止合并。

## 4. 上一轮四个 P1 的关闭验证

### 4.1 会话删除路径与双重确认：已修复

当前 `SessionPickerScreen` 接收实际 `save_dir`，调用处也传入配置路径：

- `src/coderio/cli/tui.py:1120-1128`
- `src/coderio/cli/tui.py:1223-1258`
- `src/coderio/cli/tui.py:2535-2539`

临时目录探针：

```text
delete_after_first_custom_exists=True
delete_after_second_custom_exists=False
delete_default_exists=True
```

结论：第一次 Del 只进入确认状态，第二次 Del 删除自定义目录文件，默认目录同 ID 文件保留。上一轮“删错目录”和“一次按键立即删除”均已修复。

仍需改进：删除器不知道当前活动 session ID，因此用户仍可两次 Del 删除正在使用的 session；删除相关行为也没有进入 `tests/cli/test_tui_picker.py` 或新 TUI 测试。

### 4.2 Deep Agent 失败退出码：已修复

`src/coderio/agent/harness_middleware.py:53-88` 现在读取 `ExecuteResponse.output` 和结构化 `exit_code`，追加 `[exit_code: N]`。

探针结果：

```text
exit_adapter_text='FAILED tests/test_x.py\n[exit_code: 1]'
failed_execute_writes_remaining=['src/x.py']
failed_execute_verify_attempts=1
```

非零退出码不再清除待验证写入，Harness 不会把失败测试误报为已验证。对应单元测试位于 `tests/agent/test_deep_loop.py:168-189`。

### 4.3 SQLite 异常路径连接泄漏：已修复

- `_try_create_checkpointer()` 在 saver/setup 异常时关闭连接：`src/coderio/agent/deep_loop.py:354-385`；
- `run_deep_agent()` 的 `try/finally` 已覆盖 agent factory、on_step_start 和 stream：`src/coderio/agent/deep_loop.py:317-330`。

探针结果：

```text
setup_failure_result=(None, None)
setup_failure_conn_closed=True
```

对应 setup failure 测试已加入，但本机因缺少 checkpoint 插件而 skip；完整依赖 CI 应执行。

### 4.4 CompletionGate 失败与跨轮同步：已修复

- `src/coderio/agent/harness_middleware.py:153-166` 不再同步 `Error...` 的失败 `write_todos`；
- `src/coderio/agent/harness_middleware.py:215-228` 会从 graph state 恢复上一轮 todos。

探针结果：

```text
todo_failed_call_synced=0
todo_restored_state_blocks=True
```

结论：上一轮发现的同轮失败状态分叉和 checkpoint 跨轮不可见问题均已关闭。

## 5. 架构变化分析

### 5.1 单一 Deep Agent 引擎：方向正确

旧 ReAct 引擎和 compact 模块已经删除，README 也声明 deepagents 是唯一引擎。收益包括：

- 不再维护两套工具循环、流式事件和上下文压缩实现；
- 旧引擎与 deepagents 行为漂移的风险降低；
- 代码净减少约 1,900 行；
- `run_deep_agent` 已拆成 prompt、extra tools、research subagent、inputs、stream 五类辅助函数。

代价是故障回退路径消失。SQLite 不可用时仍会传完整 session history，但 deepagents 的跨轮 checkpoint/summarization 状态无法持久化；唯一引擎一旦因上游 API 变化启动失败，没有旧 ReAct fallback。

### 5.2 Prompt 独占解决冲突，但实现依赖全局 monkey-patch

`src/coderio/agent/deep_loop.py:264-273` 直接将：

```python
deepagents.graph.BASE_AGENT_PROMPT = ""
```

这能消除两套系统提示词冲突，作者也报告 Live 对话从“自动探索”恢复为简单问候直接回答。行为目标合理。

风险在于：

- 修改第三方模块全局状态，会影响同进程内其他 deepagents 实例；
- `BASE_AGENT_PROMPT` 是实现细节，未锁定的 deepagents 升级可能改名、改类型或改变拼接逻辑；
- 当前新增测试没有导入/运行真实 deepagents graph，无法提前发现该兼容性破坏。

应优先使用公开参数关闭默认 prompt；若上游没有公开 API，应封装版本适配层并对支持版本做明确约束和集成测试。

### 5.3 Research subagent 隔离仍依赖私有 API

`src/coderio/agent/deep_loop.py:162-193` 继续使用私有 `_ToolExclusionMiddleware`，并以黑名单排除四个写/执行工具。项目依赖仅声明 `deepagents>=0.6`，没有上限或 lock。

这意味着工具集合扩展或私有 API 变更可能弱化“只读”保证。更稳妥的方式是只读白名单，并对 research subagent 的最终 tool list 做真实图级断言。

## 6. 当前 P0 / P1 / P2 问题

### P0

#### P0-1：Bash 仍可绕过 WorkspacePolicy，缺少进程级文件系统/网络隔离

该问题本轮未修改。

- `src/coderio/tools/workspace.py:49-53` 对非直接写文件工具默认放行；
- `src/coderio/tools/workspace.py:79-84` 对 Bash 只检查 `cwd`；
- FULL 模式会自动允许通过策略的 Bash。

当前探针：

```text
bash_abs_redirect_allowed=True
bash_network_allowed=True
```

因此 Bash 中的绝对路径重定向、`cd`、子 shell、解释器和网络命令仍不受 WorkspacePolicy 约束。成熟编码 Agent 的关键差异不只是确认弹窗，而是文件系统与网络 sandbox。该问题继续限制安全评分上限。

### P1

#### P1-1：唯一生产引擎主路径覆盖率仅 20%

`tests/agent/test_deep_loop.py:1-8` 明确说明只测试 building blocks，不运行完整 deepagents graph；测试文件没有调用 `run_deep_agent()`。

覆盖报告显示 `src/coderio/agent/deep_loop.py:262-338` 的主体 setup/agent factory/stream/finalize 基本未覆盖，PermissionMiddleware 仅 37%。这意味着：

- middleware 顺序和真实 ToolMessage/ExecuteResponse 形态；
- private prompt/tool-exclusion API；
- checkpoint + stream + Harness + Permission 组合；
- agent factory/stream 异常后的 session 一致性；

都可能在单元测试全绿时回归。

建议用 fake BaseChatModel 或 deterministic graph fixture 跑通至少四条离线集成路径：纯问答、写入后失败验证、权限拒绝、两轮 checkpoint/todo 恢复。

#### P1-2：会话删除仍可删除当前活动会话

`SessionPickerScreen` 只接收 summaries 和 save_dir，没有 active session ID。当前 session 会出现在 `/resume` 列表中，用户两次 Del 后可删除当前 JSONL/SQLite；若随后取消 picker，运行时仍持有旧 Session 对象，下一次 append 可能重新创建一个缺少历史/meta 的同名文件。

双 Del 降低误触概率，但没有消除数据一致性风险。建议传入 `active_session_id` 并禁止删除，或删除后立即切换到新 session；同时添加真实文件级测试。

#### P1-3：依赖和第三方私有 API 仍不可重复

- 无 `uv.lock`、constraints 或等价锁文件；
- `pyproject.toml:30-44` 使用宽泛下界；
- `deepagents.graph.BASE_AGENT_PROMPT` 被全局 monkey-patch；
- research 隔离依赖 `_ToolExclusionMiddleware` 私有类。

CI 的 `pip check` 能发现已安装环境中的冲突，但不能保证下次安装得到相同版本或相同行为。对于唯一生产引擎，这是可靠性和安全边界的共同风险。

### P2

#### P2-1：Release 已安装完整依赖，但只 import、不执行生产引擎

`.github/workflows/release.yml:40-60` 现在正常安装 wheel 全部依赖，这是明显改进；但 smoke 只执行 `coderio --help` 和导入 `run_deep_agent` 等符号。由于 deepagents 在函数内部 lazy import，该步骤不会触发 `deepagents.graph.BASE_AGENT_PROMPT`、research 私有 middleware、backend 创建或 graph 构建。

建议增加无网络 fake-model 的一次 `create_deep_agent`/最短 turn 冒烟。

#### P2-2：覆盖率仅展示，不设门槛

当前总覆盖 68%，但 `deep_loop` 20%、TUI 45%、PermissionMiddleware 37%。CI 不设置 `--cov-fail-under` 或分模块规则，关键模块覆盖下降不会失败。

建议先固定当前 68% 总体 baseline，再为 `deep_loop`/permission middleware 设置独立最低线，避免大量高覆盖工具模块掩盖核心入口盲区。

#### P2-3：删除旧引擎后仍有大量过期注释与文档

已确认的矛盾包括：

- `src/coderio/agent/deep_loop.py:14-17` 声称旧 ReAct engine 仍为 crew/tests 保留，实际文件已删除；
- `src/coderio/cli/tui.py:2660-2661` 声称 `loop.run_agent` 仍保留；
- `docs/coderio-architecture.md:412` 声称旧引擎保留为 fallback；
- `pyproject.toml:106-108` 仍称 deep_loop 是 experimental、未接入默认 CLI；
- `CONTRIBUTING.md:59` 仍列出已移除的 crew 目录；
- TUI 多处注释仍引用 ReAct round、`_execute_turn` 和 `loop.py`。

这些不是纯文字问题：它会误导安全审计、贡献者和后续重构判断。建议用 `rg 'loop.py|compact.py|ReAct|crew|fallback|experimental'` 做一次清理。

#### P2-4：C901 改善明显，但 TUI 复杂度仍高

C901 从 16 降至 12，且 `run_deep_agent` 从 22 降至 12，是本轮重要进步。当前最高仍为：

- `src/coderio/cli/tui.py:2425` `run_tui`：36；
- `src/coderio/cli/tui.py:2500` `on_input`：24；
- `src/coderio/cli/commands.py:220` `handle_slash`：18；
- `src/coderio/cli/tui.py:1776` `on_key`：18；
- `src/coderio/agent/harness.py:307` `observe`：15。

下一步应从 TUI runtime/session controller 开始拆分，而不是继续只拆 Agent 入口。

#### P2-5：版本与可发布元数据仍停留在 0.1.0

`pyproject.toml:7` 仍为 `0.1.0`；`deepagents>=0.6` 同时出现在必需依赖和 `deepagent` extra。当前功能、架构和测试相对初始 0.1.0 已大幅变化，应清理冗余 extra，并建立 tag/version 一致性检查。

## 7. 已确认的工程进步

- 上一轮四个 P1 均通过当前代码和探针验证关闭；
- 退出码从字符串猜测转为结构化适配；
- SQLite 生命周期覆盖 setup/factory/start/stream 异常路径；
- CompletionGate 能处理失败调用和 checkpoint 跨轮状态；
- 删除操作使用实际配置目录，并增加双击确认和失败不移除 UI；
- 旧 ReAct/compact 引擎被移除，减少约 1,900 行生产/测试维护面；
- C901 由 16 项降为 12 项，Deep Agent 入口复杂度显著下降；
- 新增 headless TUI 流程测试；
- CI 增加 `pip check` 和 coverage 输出；
- Release 改为安装 wheel 的完整依赖，而非 `--no-deps` 手工补包。

这些变化说明作者能有效吸收审计反馈，并开始从“修单点 bug”转向“清理架构、建立基线”。

## 8. 与上一版本对比

| 维度 | `2b9b54b` | `34b8849` | 趋势 |
|---|---|---|---|
| Bash 安全隔离 | cwd-only | cwd-only | 持平，仍是 P0 |
| Harness 退出码 | 可 fail-open | 结构化 exit_code | 明显上升 |
| SQLite 资源管理 | 部分异常泄漏 | 关键异常路径关闭 | 上升 |
| CompletionGate | 同轮部分有效 | 失败/跨轮均修复 | 上升 |
| 会话删除 | 路径错误、无确认 | 正确路径、双确认 | 上升，但 active session 未保护 |
| Agent 架构 | deepagents + 死 ReAct 代码 | deepagents 唯一引擎 | 上升 |
| C901 | 16 项 | 12 项 | 上升 |
| 测试 | 480，无 coverage baseline | 438+3 skip，68% baseline | 可观测性上升，主路径盲区仍在 |
| Release | 部分依赖 import smoke | 完整依赖安装 + import smoke | 上升 |
| 文档一致性 | 部分过期 | 删除旧引擎后矛盾增多 | 下降 |

## 9. 与成熟编码 Agent 基线的简要对比

| 维度 | coderio 当前状态 | 相对 Claude Code / Codex 等成熟基线 |
|---|---|---|
| 安全隔离 | 四级权限、文件工具路径边界；Bash/网络无 OS sandbox | 明显落后 |
| Agent 可靠性 | Harness 四门、exit/todo/checkpoint 已改善 | 思路有特色，但唯一主流程验证不足 |
| 长任务/上下文 | deepagents summarization + SQLite checkpoint | 方向接近，异常降级和真实长任务证据不足 |
| 多 Agent/扩展 | research subagent、skills 可执行工具 | 能力不错，私有 API/黑名单隔离风险较高 |
| TUI/CLI | 折叠思考、Todo、token、权限菜单、resume 管理 | 个人项目中突出，但复杂度和覆盖不足 |
| CI/Release | 3 OS × 2 Python、68% coverage 可见、完整依赖安装 | 基础良好，缺运行级 smoke 和版本锁定 |

## 10. 评分明细

| 维度 | 权重 | 得分 | 说明 |
|---|---:|---:|---|
| 安全隔离与权限 | 1.5 | 0.8 | P0 Bash sandbox 未解决 |
| Agent 执行可靠性 | 1.5 | 1.3 | 四个 P1 修复，但唯一引擎主流程覆盖不足 |
| 上下文与长任务 | 1.0 | 0.8 | checkpoint 生命周期改善，缺真实长任务证据 |
| 多 Agent 与扩展 | 1.0 | 0.8 | research/skills 较强，私有 API 和黑名单扣分 |
| TUI/CLI 体验 | 1.0 | 0.85 | 删除确认和 headless 测试进步，active session 风险仍在 |
| 测试质量 | 1.5 | 1.2 | 68% baseline；deep_loop 20%、permission 37% 拉低 |
| CI 与发布 | 1.0 | 0.85 | pip check、coverage、完整依赖安装均有提升 |
| 可维护性与文档 | 1.5 | 0.8 | 死代码和复杂度下降，但过期文档、无锁和 monkey-patch 扣分 |
| **合计** | **10.0** | **7.4** | **相比上一轮 +0.4** |

**最终评分：7.4/10。**

## 11. 优化建议：按收益、成本、优先级排序

| 排名 | 建议 | 收益 | 成本 | 优先级 |
|---:|---|---|---|---|
| 1 | 为 Bash 增加 OS/container 文件系统与网络 sandbox | 极高：关闭唯一 P0 | 高 | P0 |
| 2 | 增加 fake-model 的完整 `run_deep_agent` 离线集成测试 | 极高：覆盖唯一生产引擎 | 中 | P1 |
| 3 | 为 PermissionMiddleware 增加真实 tool-call allow/deny/path tests | 高：验证安全适配层 | 低到中 | P1 |
| 4 | 禁止删除当前活动 session，删除成功后验证所有目标文件状态 | 高：避免会话数据损坏 | 低 | P1 |
| 5 | 锁定 deepagents/langgraph/langchain 组合，消除私有 API/global monkey-patch | 高：提高可重复性和边界稳定性 | 中 | P1 |
| 6 | Release 运行一次无网络 fake-model graph，而不只是 import | 高：验证用户安装物核心路径 | 中 | P1 |
| 7 | 清理所有 ReAct/loop/crew/fallback 过期文档和注释 | 中：避免误导维护与审计 | 低 | P2 |
| 8 | 对 deep_loop/permission/TUI 设置分模块 coverage 最低线 | 中：防止总体覆盖掩盖核心盲区 | 低 | P2 |
| 9 | 将 TUI 的 runtime、session、dialog controller 从 `run_tui` 拆出 | 中：降低 36/24 复杂度热点 | 中到高 | P2 |
| 10 | 更新版本策略、清理冗余 `deepagent` extra、校验 tag/version | 中低：提升发布可信度 | 低 | P2 |

## 12. 建议下一轮最先补的测试

1. fake model 纯问答：不得调用工具，正确持久化最终文本；
2. fake model 写文件后返回失败 execute：VerifyGate 必须继续；
3. PermissionMiddleware：PLAN/FULL/AUTO_EDIT 对 execute/write_file 的真实 graph 行为；
4. 两轮 checkpoint：第二轮恢复 messages、todos、summarization state；
5. research subagent 最终工具列表只能包含只读白名单；
6. `create_deep_agent` 与 stream 异常后 SQLite、session、TUI 状态一致；
7. 删除当前活动 session 必须被拒绝或安全切换；
8. Release wheel 安装后的最短离线 graph turn。

## 13. 测试局限

- 没有调用真实大模型 API，没有使用 API Key。
- 没有安装新依赖；本机缺少 deepagents、DDGS 和 SQLite checkpoint 插件。
- 没有实际运行完整 `run_deep_agent` graph、research subagent 或 provider Live 测试。
- 三个 checkpoint 测试因本机环境缺包而 skip，不能写成通过；报告已明确列出。
- 临时探针只使用临时目录和内存 fake 对象，没有触碰真实用户 session。
- 没有本地构建 wheel，也没有核查远端 GitHub Actions/Release 历史结果。
- 本机 Ruff 0.15.22 低于项目声明的 0.16+，不能代替 CI Ruff 结果。
- Ruff `S` 全绿是在 per-file ignore 后的结果，不代表没有真实漏洞。
- 竞品对比是成熟度基线，不是同模型、同硬件、同任务性能基准。

---

## 最终结论

`34b8849` 是一次质量明显上升的更新：上一轮四个 P1 已闭环，死引擎被移除，复杂度、CI、Release 和 TUI 测试都有进步。当前项目已具备较好的 Alpha 工程基础，但 Bash 无沙箱仍是 P0，唯一 Deep Agent 主流程 20% 覆盖与第三方私有 API 依赖则是下一阶段最应解决的可靠性问题。综合评分为 **7.4/10**，较上一轮上升 **0.4 分**。
