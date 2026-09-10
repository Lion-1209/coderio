# coderio macOS 真机安全与可靠性审计

> **阶跃 Key 补测更新**：用户于同日提供 Key 后，已补跑真实 `step-3.7-flash`；最新结果见 §7。§1–6 的“无 Key / Live 未测”和 1310 测试数是首轮历史状态，不代表本轮结论。

- 日期：2026-09-10；审计者：Codex agent，运行在维护者之外的 macOS 真机环境。
- 基线：`Lion-1209/coderio main@b87aa06511dc3d51d7dafffc66ec3f7c55a4e534`；分支 `audit/macos-202609`。
- macOS 26.6.2 (25G83)，Darwin arm64，APFS；CPython **3.11.16 / 3.12.14**，uv **0.12.12**。
- [实测] 表示执行过的命令或真实文件/子进程实验；[代码审阅] 表示静态定位；[判断] 表示工程评估。行号除另有说明均指上述基线。
- 先读 CONTRIBUTING、架构、09-04 报告、CHANGELOG、ROADMAP；先查相关 `git log` / `git show` 再分析。现有工作目录有个人未跟踪文件，因此使用独立克隆；GitHub 认证恢复后复用了已存在的 `KiritoStar/coderio` fork。

## 1. 执行摘要

**[判断] 双版本基线全绿不能证明 macOS 运行时边界可靠。** 本次新增真实实验复现三项 P1：沙箱降级超时遗留子进程、损坏凭据备份权限变宽、web_fetch 大小限制晚于完整响应读取。前两项已在本 PR 分别修复，均有先红后绿的真实工具回归测试。未确认 P0；这不是“证明没有 P0”。

默认 `off` 的普通 POSIX 进程组超时杀有效；`job` / `write` 的同一实验在修复前留下 PPID=1 的存活子进程。macOS `write` 不提供文件隔离，明确提示降级，`auto_allow_if_sandboxed` 在 CONFIRM 下不生效。人工 TUI、真实 provider、Windows/Linux 的平台行为不冒充实测。

## 2. 实测结果与证据

完整命令输出在 [证据目录](docs/audit/2026-09-10/)。用户名、工作区绝对前缀和本机主机名做占位替换；PID、返回码、测试数保留。日志不含真实 key。实验脚本使用临时文件及短生命周期进程，可由仓库 venv 执行。

### 2.1 修改前后五项门禁

两套环境分别使用 `UV_PROJECT_ENVIRONMENT=.venv311/.venv312` 和 `--python 3.11/3.12` 执行 `uv sync --frozen --extra dev`，避免互相覆盖；运行命令额外加 `uv run --frozen` 保留原锁。

| 检查 | 修改前 3.11 | 修改前 3.12 | 修复后 3.11 | 修复后 3.12 |
|---|---|---|---|---|
| `uv sync --frozen --extra dev` | 成功 | 成功 | 使用同一冻结环境 | 使用同一冻结环境 |
| `uv run ruff check src tests` | 0 | 0 | 0 | 0 |
| `uv run ruff format --check src tests` | 0 | 0 | 0 | 0 |
| `uv run python -m pytest tests/ -q -rs` | 1306 passed / 22 skipped | 1306 passed / 22 skipped | 1310 passed / 22 skipped | 1310 passed / 22 skipped |
| `uv run python -m mypy src/coderio` | 0 | 0 | 0 | 0 |
| `uv build --wheel` | 0 | 0 | 0 | 0 |

`baseline{311,312}-status.txt` 与 `after{311,312}-status.txt` 记录每条退出码，各检查有独立日志。`uv lock --check` 当前成功（121 packages），不表示 CI 已检查新鲜度。独立 `uv export --frozen --no-emit-project --extra dev` + `uvx pip-audit --no-deps -r` 返回 **No known vulnerabilities found**（pip-audit.log）；只表示本次查询的冻结集合。全量测试保留上游现有 mock；**本次新增测试不 mock 工具、文件系统或进程**。

覆盖率补充运行第一次直接使用 `.venv312/bin/python`，导致三个 hooks 测试报 `/bin/sh: python: command not found`（coverage.log，3 failed / 1307 passed / 22 skipped）。现场 `shutil.which("python")` 为 None，查阅 hook 测试命令后确认其依赖 PATH。规范的 `uv run` 会加入 venv PATH；这是审计命令偏差，已另行按规范复跑，不将失败日志隐藏或归因于 provider。

规范复跑结果：**1310 passed / 22 skipped，覆盖率 82.60%，75% 门通过**（coverage-uv.log）。

### 2.2 skip 逐项分析

[22 项完整清单及 pytest 原因](docs/audit/2026-09-10/skips.md)。两版本清单相同：

- 19 项为 Windows 专属路径：shell 探测缓存、msvcrt 锁、Windows ACL、sandbox env seam、Restricted Token/Job/Windows fallback/taskkill。macOS 不执行它们合理，但不能把结果当作跨平台验证。
- 2 项真实 provider 性能测试要求 `CODERIO_PERF_TESTS=1` 和 key。本次缺少相关环境 key，未执行。
- 1 项 `test_python_fallback_used_when_no_rg` 因本机有 rg 跳过。这个分支也适用于 macOS；已将 PATH 限为 venv + `/usr/bin:/bin`，补测 **1 passed**（`grep-no-rg.log`）。
- 新发现的 POSIX fallback 超时泄漏不是靠这些 skip 解释的：原套件根本没有覆盖该存活性断言；新增 3 个生产 backend 参数化实例补齐。Windows CI 会明确跳过新 POSIX 测试，真实 bwrap 存在的 Linux 会跳过 fallback-write 实例。

### 2.3 macOS 清单

| 项目 | 结果与证据 |
|---|---|
| 冻结锁 / arm64 | 双版本 sync 成功，见 sync311/312.log；未改 uv.lock |
| wheel → 全新 venv | 修改前及修复后的 wheel 分别装入全新 venv；最终安装源码检查确认含两项修复（wheel-final-smoke.log）。独立目录、无源码 PYTHONPATH，安装成功；`--help`、`config`、`mcp list`、`skills list` 全部 exit 0。mcp list 仅证明管理 CLI，无服务器连接；skills list 列出 13 个 bundled skills。安装依赖按 wheel 的范围重新解析，区别于冻结测试环境 |
| TUI 自动启动 | 指定 `tests/cli/test_tui_startup.py -v`：14 passed（tui-startup.log） |
| TUI 人工体验 | **人工未测**：启动、输入、/help、/mode、Ctrl+O、Esc 均未收到人类逐项确认。自动 Textual 测试不代替人工体验 |
| write 沙箱降级 | 实际 TOML 配置解析 + build_gate：boundary=`none`、auto_allow=False、有 macOS 无 OS 沙箱警告；生产 execute 返回 `[sandbox unavailable: ... ran WITHOUT ...]`。见 mac_checks.py / mac-checks.log |
| POSIX 树杀 | 生产 backend 三模式执行 Python 子进程 + shell wait；timeout=1，ps 取 PID/PPID/PGID/stat。修复前 off 无残留，job/write 各残留一个 PPID=1 存活进程；脚本主动清理。修复后均约 1 秒返回 124，ps 无记录（probe-before/after.log）。普通同组后代覆盖，不宣称能杀主动 setsid 逃逸的恶意进程 |
| 凭据权限 | 普通写入 0600；虚假凭据另在 `~/.coderio` 一次性子目录真实写入并用 ls 检查，结束清理（home-credentials.log），未改用户已有 key。损坏备份修复前 0644 → 修复后 0600 |
| SSRF | 127.0.0.1、10.0.0.1、::1、fe80::1%lo0、100.100.200.200 和本机 `.local` 均被拒绝；本机 `.local` 解析到了 ::1。不能外推为所有 mDNS/NAT64/DNS 重绑定情况均安全 |
| hooks | 实际 `[[hooks]]` → HookRunner，真实 shell 读取 stdin JSON；字段含 session_id/cwd/permission_mode/hook_event_name/tool_name/tool_input，stderr=`audit-denied`、exit 2 → blocked=True。mac-checks.log |
| APFS checkpoint/undo | `/MixedCase.py` 创建、`/mixedcase.py` 编辑同一文件；首次 undo 恢复 original，第二次删除新建文件。实际大小写不敏感检测为 True。不覆盖多实例并发、跨卷 rename、Unicode 归一化和符号链接竞态 |
| Live scripts | `verify_harness_live.py`、`verify_deepagent_live.py` **未测**；ANTHROPIC_API_KEY/Z_API_KEY/STEP_KEY/STEPFUN_API_KEY 环境均缺失，未修改 provider/协议配置 |

## 3. 新发现（按优先级）

### P1-N1：job / degraded write 超时只杀 shell，子进程继续运行【实测，已修复】

基线 `src/coderio/tools/sandbox_runner.py:109-132` 使用 `start_new_session=True`，却交给 `subprocess.run(timeout=...)` 处理超时，异常分支只返回 124。`agent/deep_loop.py:234-272` 将 job/write 实际路由到这里；默认 off 的 Popen/kill_process_tree 不是同一路径。

复现：`uv run python docs/audit/2026-09-10/probe.py`。基线输出中 job 子进程 PID 66527 / PPID 1，write PID 66532 / PPID 1，状态 S；off 无记录。脚本只杀本次 PID，不碰其他进程。

历史：`git log -- tools/sandbox_runner.py` 及 `git show bcf1c8b` 表明 09-04 修复增加了可见降级标记，但未改变该 fallback 的 subprocess.run；这是未覆盖的 POSIX 路径，不归因于供应商或依赖变化。

修复：Popen 持有进程对象，异常时调用共享 kill_process_tree，再 kill/reap 直接子进程；超时仍返回 124，并保留 write 降级提示。回归 `tests/tools/test_posix_process_tree.py` 直接执行真实 backend，修复前 job/write 红、off 绿，修复后全绿。

### P1-N2：损坏凭据备份泄漏原权限保护【实测，已修复】

基线 `src/coderio/cli/credentials.py:78-81` 对 `.corrupt` 使用 `write_bytes`，绕开 `_restrict_permissions`。022 umask 下原文件 0600，备份 0644；备份可以包含尚可恢复的 key。[判断] 如果父目录可被其他本机账户遍历，文件会暴露 key；本次只测 mode bits，没有声称实际读取了其他账户的秘密。

历史：`git show 86a7957`，备份路径正是上一轮原子写修复新增。新的可恢复性功能没有延续保密性契约。

修复：`xb` 独占创建，写入字节前调用同一权限限制函数，保留首份备份。真实文件测试 `tests/cli/test_credentials_backup_permissions.py` 在 022 umask 下验证备份内容、0600、二次损坏不覆盖；修复前失败、修复后通过。Windows ACL 调用的真机结果交给远端 Windows，不冒充本机验证。

### P1-N3：web_fetch 1 MB 上限在完整下载后才生效【实测 + 代码审阅，待修复】

`src/coderio/tools/web_fetch.py:178` 为 `client.get()`；`:202-211` 才 iter_bytes 并截断。冻结 httpx 的 `_client.py:879` 附近 `send(stream=False)` 默认路径先 `response.read()`。因此文档的“stream up to 1 MB”并未实现：返回给模型的字符串有界，但传输/缓存不受此上限保护。

复现 `uv run python docs/audit/2026-09-10/fetch_buffer.py`：真实本地 HTTP 服务被**显式配置为代理**，不向公共网络发请求。服务先发送 1,100,000 字节然后暂停，客户端 timeout=1。实际得到 `Error fetching ...: timed out`，耗时 1.04s；已经收到超过上限的数据却仍等待剩余响应。此实验没有 mock HTTP 或 SSRF 函数，也不用于证明 proxy SSRF 绕过。

[判断] 大/慢响应可耗尽内存或长期占据 turn；本次没有做 OOM 压力破坏。建议另修为 `client.stream('GET', ...)`，在 context manager 内逐跳校验、检查类型并有界读取，覆盖大 body、chunked、压缩、重定向及异常资源释放的真实服务测试。本 PR 保留可执行复现，不把未完成的修复标为已解决。

### P2-N4：TUI gate 缺失确认接口时默认放行【代码审阅，待修复】

`src/coderio/cli/repl.py:46-59`：`TuiPermissionGate._ask` 在 `tui` 没有 `request_confirmation` 时返回 True，与 `tools/permission.py` 的 bare gate fail-closed 修复不一致。

[判断] 正常 CoderioTUI 实现该方法，未证实正常用户路径可触发；属于装配异常的防御缺口，不夸大为已利用的权限绕过。建议缺失接口返回 False，并测试真实 gate 对不具备确认能力对象的处理。

### P2-N5：架构“当前状态”仍残留已删除/已完成机制【代码审阅，待同步】

- `docs/coderio-architecture.md:253` 仍列 `on_truncated`；CHANGELOG 0.5.0 明确记录已删除，当前 StreamHandler 无此方法。
- 同文档 §9 第 5 项及 ROADMAP Next 仍称 exit_code 仅正则，当前 `agent/harness_middleware.py:155-169` 先取 `.exit_code`，再取 `ToolMessage.artifact['exit_code']`，然后才兼容文本。
- §7.2 记 12 个 skills，wheel CLI 实际列出 13 个。数字应移除或由打包校验生成。

建议将文档对齐当前实现，并区分“结构化已接入、文本回退仍存在”。不把整个旧报告“文档大面积失真”的评价直接复用到当前版本。

## 4. 复核已知问题与历史修复

| 已知项 | 当前结论与证据 |
|---|---|
| macOS 无 OS 沙箱 | 仍成立；repl.py:132 附近显式返回 none；见 §5 |
| pip-audit 扫 venv | `.github/workflows/ci.yml:81` 附近先 uv pip install pip-audit 再 uv run pip-audit，工具链与项目集合混合。建议 export frozen requirements + uvx pip-audit --no-deps -r；注意 CHANGELOG 记载 httpx2 经 langchain-openai → openai 进入**项目依赖**，不能把 09-09 那次具体 CVE 误称为仅审计器误报 |
| CI 无 concurrency / docs-only 过滤 | ci.yml 顶部 push/pull_request 无 paths / concurrency，仍成立；docs-only 仍触发矩阵 |
| frozen 不查锁新鲜度 | ci.yml 只有 sync --frozen，且注释错误声称验证新鲜度；当前额外运行 lock --check 成功，建议独立 CI 步骤 |
| mypy 12 豁免 | pyproject.toml:239 的 check_untyped_defs=false，override 12 个模块；mypy 绿受此范围限制 |
| C901 27 个 | ruff check src --select C901 实测 27，_shell_backend_cls 24，见 complexity.log；未进行无关复杂度重构 |
| __OPEN_PICKER__ | commands.py:217 ↔ tui_runtime.py:150 仍是字符串协议 |
| 真实 provider eval | workflows 只有 ci/release，ROADMAP Now 仍待实现，本次 Live 未测 |
| lion-skills vendored | src/coderio/skills/lion-skills 为普通跟踪文件，非 submodule；不擅自更换来源 |

历史修复抽查，不将“测试通过”泛化为完整对抗证明：

- **P0-1 黑名单**：[实测] `${HOME}` recursive rm、diskutil eraseDisk、Stop-Computer 均返回拒绝原因，echo safe 放行；只调用 policy，不执行破坏命令（mac-checks.log）。
- **P0-2 SSRF 共享段**：[实测] 100.100.200.200 被拒绝；[代码审阅] 显式 CIDR + is_global 已在。DNS 重绑定窗口仍由模块顶部承认，未解决。
- **P0-3 / exit_code**：[代码审阅 + 全量测试] harness 的可执行命令过滤已存在，structured exit_code 优先通路已接入，相关 harness 测试通过；不是对所有 shell 语义的证明。
- **P0-4 凭据原子写**：[代码审阅 + 全量测试] PID temp + os.replace 已存在；新增发现为 backup 权限。并发 read-merge-write 无跨进程锁，不能等同于并发更新不丢 key。
- **P0-5 Windows 会话锁**：只审阅固定偏移实现，Windows 互斥实测未做；不得引用本机 pytest 绿替代。
- **P0-6 shell 树杀**：off 路径本机通过，job/write 缺口见 N1；未验证 Windows 孙进程。
- **P1-10 PLAN 写 plan.md**：harness_middleware.py:189 加了 _plan_mode_blocks_writes，相关测试通过。
- **P1-11 降级/auto-allow**：macOS CONFIRM 配置实测有效；FULL 本来全放行，不应把 FULL 结果当自动沙箱审批。
- **P1-12 checkpoint**：最新快照保留、超限文件跳过并警告已实现；跨实例 lost-update 被记录而未解决。正常 APFS 大小写 undo 通过，不等于并发安全。
- **P1-14 信任门**：trust.py:101 附近 commands/agents 已纳入 discovery，内容指纹与相关测试存在；未做真实恶意仓库全链路攻防。
- **P1-15 sync-only**：Permission/Hooks 等继承 SyncOnlyMiddleware，选择显式 sync-only 而非实现 async；这是已声明边界。

## 5. macOS 专项结论与 sandbox-exec 评估

**[实测] 本机存在 `/usr/bin/sandbox-exec`，最小 profile 能允许临时目录普通写、拒绝指定子目录写。** 见 seatbelt_probe.py / seatbelt.log。这只证明一个规则的运行能力。

**[实测] 本机 `man sandbox-exec` 的 NAME/DESCRIPTION 明确写 DEPRECATED，并建议 App Sandbox。** 因而“工具能运行”不等于适合承诺长期支持。

**[判断] 建议维护者先作正式产品决策：默认继续明确“macOS 无 OS 级沙箱”，若要实现，作为 opt-in 独立后端评估。** 不在此次 PR 加入后端，不因配置为 write 就免确认。实施需覆盖：workspace realpath/APFS 大小写/符号链接、用户凭据与 trust-store deny、临时目录及构建工具可写例外、Python/Node 子进程继承、网络策略、错误后 fail-closed、超时与 Esc 生命周期、多个 macOS 版本。App Sandbox 也不能在未经验证时当作任意 CLI agent 的直接替代品。

方案已提交 [issue #16](https://github.com/Lion-1209/coderio/issues/16)，包含本次最小复现与验收矩阵；等待维护者决定接口与维护承诺。现阶段真正需要对抗性隔离的任务仍应使用独立 VM，和仓库自己的安全边界声明一致。

## 6. 未测项与局限

- 人工 TUI 未确认；Live provider 无 key 未测；真实 MCP server、Linux bubblewrap 隔离、Windows token/ACL/taskkill 均未在本机验证。
- wheel 四项 CLI 不证明真实 provider、MCP 会话或所有重新解析后的依赖组合兼容。
- 进程实测覆盖普通同组子进程；不涵盖主动 daemonize/setsid，未做 fork-bomb 或资源耗尽实验。
- 凭据测试全部是虚假 key；没有审计用户真实凭据内容、其他用户读取能力或电源中断/fsync 耐久性。
- 此次为有限时段的代码审阅和可复现实验，未穷尽全部输入。新问题 N3/N4/N5 尚未修复，应作为后续工作；没有因五项绿而撤销这些发现。


## 7. 阶跃真实 Key 补测（同日追加）

本轮基线 `a207c18`；Live 脚本修复提交 `6ffcbee`。凭据通过隐藏 stdin 输入，仅存运行进程内；模型构造后从环境删除，避免真实 shell 子进程继承。未写用户配置/凭据文件，所有日志经过 Key 精确脱敏。证据在 [live-rerun](docs/audit/2026-09-10/live-rerun/)。

### 7.1 模型与协议先验验证【实测】

用户的“3.7flash”对应规范 ID `step-3.7-flash`，已由服务响应确认。先发最小请求，两条官方接口均 HTTP 200、返回 OK：

- OpenAI 兼容：`https://api.stepfun.com/v1/chat/completions`，约 1.04s。
- Anthropic 兼容：`https://api.stepfun.com/v1/messages`，约 1.02s。SDK base_url 为 `https://api.stepfun.com`，由 SDK 拼 `/v1/messages`。

依据：[阶跃官方模型示例](https://github.com/stepfun-ai/Step-3.7-Flash/blob/main/README.md)、[官方 Messages 文档](https://platform.stepfun.com/docs/zh/api-reference/chat/messages-create)。本次是普通 API Key 通道，不是 `/step_plan` Coding Plan 额度验证；没有把两者混用。保存的 preflight JSON 仅保留状态、耗时、模型、usage 和可见文本，省略推理块及完整响应。

### 7.2 本轮新发现与修复

**P1-N6：两份 Live 脚本已脱离引擎接口【实测，已修复】**

修复前，两脚本均在首次 run_deep_agent 调用处报 `TypeError: ... unexpected keyword argument 'workdir'`，模型还未进入该引擎调用。定位 `scripts/verify_harness_live.py:77/99/112`、`scripts/verify_deepagent_live.py:54/67`。`git log -S 'spec: TurnSpec'` 定位 `e39b08d`（09-01）：引擎改为 `(user_input, spec, session, stream)`，常规测试/CLI 已迁移，Live 脚本遗漏。

本 PR 将五处调用迁移为 TurnSpec；未改协议默认值、模型默认值或脚本递归上限。新增 `tests/agent/test_live_script_contract.py`，仅替换模型、执行真实 graph/写盘/shell，四项修复前失败、修复后通过。这也修正上一轮“Live 脚本只是缺 key 未测”留下的证据空洞。

**P2-N7：阶跃 OpenAI 流式累计 usage 被适配器重复相加【实测，待修复】**

`stepfun_api` 实际模型工厂的性能测试：QA 通过；单文件分析在 `tests/agent/test_perf_baseline.py:154` 失败，聚合 input_tokens=574422 > 50000。未放宽断言。

进一步一次最小真实请求（`usage_probe.py/.json`）通过透明 httpx stream 包装器只记录 usage，不修改响应字节：13 条 SSE usage 均带 prompt_tokens=16，completion_tokens 从 0 累计至 28（末条重复 28）；最终供应商 usage 为 16/28/44，而 ChatOpenAI 聚合得到 208/180/388。冻结 `langchain_openai/chat_models/base.py` 的 `_convert_chunk_to_generation_chunk` 为各块生成 usage_metadata，合并后 coderio `agent/deep_loop.py:1215-1217` 将聚合结果交给 stream.add_usage。

[判断] 已证明当前流式统计不兼容，影响 UI/性能门的可信度；**不能把 574422 当作实际计费 token，也不能用这次最小对照还原另一个请求的真实用量**。未查询账单。建议在明确 provider 协议后规范化累计值→增量，覆盖 stream/non-stream、重复末块、工具轮次和重试；不要全局将所有 provider usage 改成取最后一项，也不要直接提高性能阈值。此项未在 PR 偷改模型适配器。

### 7.3 真实 Live 结果（Python 3.12）

| 运行 | 结果 | 证据边界 |
|---|---|---|
| 原脚本 | 两者均 TypeError | live-rerun 根目录 verify*.log |
| TurnSpec 修复后，未限速 | harness 三场景通过；deepagent HTTP 429 | after/；响应明确 current=11、limit=10 RPM，未归因猜测 |
| 请求间隔约 10 秒（6 RPM） | harness 三场景通过；deepagent 首场景 GraphRecursionError，30 步耗尽 | paced-12/；完整保留失败，不写“全部 Live 绿” |
| deepagent 生产默认 200 步**诊断对照** | 两场景通过，51.70s / 40.46s（含人工设置的限速等待） | production-budget-12/；仅运行时替换 spec.recursion_limit，脚本仍保留 30/40；模型随机性存在，不是严格因果实验 |
| 实际 stepfun_api 工厂的性能断言 | 1 passed / 1 failed | perf-final.log；读文件失败原因是上述 usage 上限，QA 成功。注入真实工厂配置，测试断言、引擎、工具均未替换 |

实际观察：

- harness 自然写文件场景：真实 write_file → VerifyGate 强制续跑 → `python hello.py` 输出 hello-harness、exit 0 → GroundingGate 要求读取 → read_file → 正常结束；用时 62.27s（含限速）。
- 显式写并运行场景：greet.py 输出 greetings、exit 0，无额外 harness_continue；19.49s（含限速）。
- harness disabled：写 skip.py 后结束，无 harness_continue/warn；19.99s（含限速）。
- deepagent 30 步失败：模型先用 shell `python /marker.py` 得 exit 2，之后 `python marker.py` 成功；GroundingGate 要求 read_file 时图步骤耗尽。工具错误确实反馈给模型，模型也能修正。
- 200 步对照：marker.py 与 calc.py 均实际执行，输出 deepagent-ok / 2、exit 0，观察到 VerifyGate 强制续跑后完成。**没有证明 CompletionGate 在这些简单任务中触发**；PlanGate 的 nudge 可见，不能等同于硬拦截。

审计驱动器自身的失败也保留：perf.log 为日志包装器缺少 isatty，尚未发模型请求；perf-after.log 为驱动器占位 Z_API_KEY 优先于 OPENAI_API_KEY，导致两次 401。修正后先断言工厂持有本次输入 Key，再得到 perf-final.log；这两项不是项目新缺陷，也不是用户 Key 无效。

### 7.4 双版本与真机复跑

| 检查 | 3.11.16 | 3.12.14 |
|---|---|---|
| 冻结 sync | 成功，101 packages checked | 成功，101 packages checked |
| 修改前五项 | 全绿，1310 passed / 22 skipped | 全绿，1310 passed / 22 skipped |
| 修复后五项 | 全绿，1314 passed / 22 skipped | 全绿，1314 passed / 22 skipped |
| TUI 启动自动测试 | 14 passed | 14 passed |
| POSIX off/job/write timeout + ps | 三模式无测试子进程残留 | 三模式无测试子进程残留 |
| 凭据、IPv4/IPv6/.local SSRF、hooks、APFS undo | 重测通过 | 重测通过 |

覆盖率重测 **82.63%**（1314 passed / 22 skipped，75% 门通过）。再次新建 wheel venv，四项 CLI 均 exit 0；再次独立冻结依赖 pip-audit：No known vulnerabilities found。完整套件仍默认禁用 Live 性能测试，22 skipped 不改写为 20；两项 Live 另行执行的 1 passed / 1 failed 单独报告。

本轮自动真机重测已经完成，但**仍不满足“所有真实测试通过”**：原 deepagent 低预算脚本和 OpenAI 统计性能门存在上述失败。人工 TUI 六项仍未收到人类确认；真实模型仅在 Python 3.12 执行，3.11 是冻结全量及系统行为重测；智谱、Coding Plan、真实 MCP、Windows/Linux 平台边界仍未补测。
