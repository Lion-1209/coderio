# coderio 改动计划：让护城河可证明、可维护

> 计划日期：2026-09-20（Asia/Shanghai）
> 状态：**评审中（草稿，未定稿）**
> 前置文档：`coderio-optimization-directions-2026-09-16.md`（那份报告列了 8 条方向，本文回答的是"以什么顺序、什么颗粒度执行、凭什么取舍"）
> 证据分级：**[实测]** 本地验证过（带 `file:line`）；**[判断]** 综合推断。
> 本文是计划，不是分析。所有结论的核实过程见 9-16 报告；本文只写决策。

---

## 0. 执行摘要

9-16 报告之后项目只动了 4 个提交且全是 TUI 修复**[实测]**，8 条方向零启动。本文把方向收敛成 3 个阶段、每阶段可独立停止（kill point），全部按"工作日"计：

| 阶段 | 内容 | 成本 | 核心产出 | 停止点 |
|---|---|---|---|---|
| **Phase 1** | 护城河裂缝修复 + 依赖拆弹 | ~4 天 | `/resume` 预填、多模态门控、deepagents 0.8 书面结论 | 任一条做完即独立交付 |
| **Phase 2** | live eval 证据体系（**主线**） | ~3–4 天 | 10 个真实任务的自动判定回归 + 公开结果表 | 首轮跑通即交付 |
| **Phase 3** | 收缩债务 | ~1 天 + 条件项 | 删死代码；SubagentStop 按需 | 随时可停 |

**唯一裁决标准**（本计划所有取舍都过这条）：一个改动要么让护城河**更可证明**，要么让项目**更可维护**；两者都不沾的一律让位。按这条，Windows ACL 真沙箱、hook 事件扩张让位；live eval 从"并列的 P0"升为唯一主线。

> **2026-09-21 补充**：ZCode（Z.ai 官方 agent，2026-09-20 开源，Apache 2.0）已完成源码级对照，借鉴项与三处计划修正见 §4A。新增工作量约 5–8 个工作日，均按同一裁决标准筛选；开源树剥离了 `.github/` 与测试目录，相关结论为残留证据推断，已在 §4A 标注。

> **2026-09-21 执行状态**（用户批准"按顺序做"后一轮实施，均未提交，等用户 review）：
>
> | 项 | 状态 |
> |---|---|
> | D1-1 `/resume` seed harness 状态 | ✅ 实现 + 8 项单测 + 端到端正反两向验收，全绿 |
> | D1-2 多模态 provider 门控 | ✅ 实现 + 9 项测试，全绿 |
> | D1-4 README 降敏 + 邮件草稿 | ✅ 双语 README 已改；邮件草稿在 §D1-4，**待用户发送** |
> | D1-3 deepagents 摸底 | ✅ 0.8 不存在；0.7.15 全量 1352 过/0 失败；结论已入文档 |
> | Phase 2 live eval | ✅ 10 任务集 + 运行器 + 18 项管线测试；真实运行 9/10→修正任务设计后 10/10，报告在 `docs/live-eval/` |
> | Phase 3 删死代码 | ✅ context_summary 管道已删，旧会话完整加载有测试 |
> | Phase 3 compat 命名化 | ✅ `ensure_todos_middleware` + 3 项测试 |
> | Phase 3 microcompact | ✅ 实现 + 6 项测试 |
> | Phase 3 黄金判定表 | ✅ 50 项案例绑定 command_policy |
> | Phase 3 架构门 | ✅ 策略 + AST 检查器 + baseline（4 存量）+ 变异测试 + 4 项自测 |
> | hook 补 PostToolUseFailure/PermissionRequest | ⏳ 未做（条件项：先有测量/自动化需求；当前无触发场景，见 §4 Phase 3） |
> | TUI 交互原语（SelectionPanel 等） | ⏳ 未做（独立小项，2–3 天，需单独一轮） |
> | trajectory 录制/派生/重放 | ⏳ 未做（Phase 2 的地基件；运行器已可重跑，录制版价值在重复运行的离线化） |

---

## 1. 背景与目标

**背景**：项目的差异化是单一想法——harness 四道门基于工具调用 ground truth 接管终止权（`agent/harness.py`、`agent/harness_middleware.py`），**[实测]** 实现与 README 描述一致，全行业无等价物。但这个想法目前有两个结构性问题：

1. **不可证明**。1,163 个测试函数**[实测]**几乎全是 mock 模型，证明不了四道门在真实 provider 上是否真的拦住假完成；护城河的效果没有任何一次测量。
2. **单点故障未拆**。引擎钉死 `deepagents>=0.7.6,<0.8`（`pyproject.toml`），上游 0.7.6 已咬过一次（移除 `write_todos`，`deep_loop.py` 手动补回）；对 0.8 的兼容性敞口完全未知。**[实测]**

**目标**（按优先级）：

1. 四道门在真实 provider 上的行为变成**自动判定、可重跑、可公开引用**的证据。
2. 对 deepagents 0.8 的风险敞口从"未知"变成"书面结论"。
3. 护城河上三处可见裂缝（`/resume` 状态丢失、多模态无门控、GLM 措辞未验证）清零。
4. 维护面收缩：删死代码，立下"不扩 mock 测试"的规矩。

---

## 2. 研究基础

只列对决策有用的核实结论（均为 **[实测]**）：

| 事实 | 来源 |
|---|---|
| 9-16 后仅 4 提交（7944458/a1440bb/4f7a708/cbedc21），全 TUI 修复 | `git log --since=2026-09-16` |
| 多模态对所有 provider 一律构造 Anthropic image block，**无 kind 分流**；docstring 明说"langchain-anthropic accepts directly" | `cli/multimodal.py:75-81` |
| hook 仅 5 事件，`Notification/SubagentStop/PreCompact` 源码注释里标着 deliberately deferred | `agent/hooks.py:47-48` |
| `/resume` 的 HarnessState 预填只有一句注释，**无实现**；`content_read_files` 注释明说"cross-turn pre-fill normalizes when seeding"——设计预留了位置但没写完 | `agent/harness.py:82`、`:498`、`:578` |
| `context_summary` 截断管道无写入者（全仓只有读取方/skip 逻辑） | `session/store.py:126-157` vs `cli/commands.py:304`、`cli/tui_runtime.py:340`、`agent/deep_loop.py:1127` |
| deepagents 版本钉死 + 上游破坏史见上 | `pyproject.toml:41-47` 注释 |
| live eval 基础设施已存在：`TurnSpec` + `scripts/verify_harness_live.py` + `scripts/verify_deepagent_live.py`（09 月刚从废弃关键字参数迁到 TurnSpec） | `src/coderio/agent/deep_loop.py`、`scripts/` |

---

## 3. 范围

**本轮做**：§4 的 Phase 1–3。

**明确不做**：

| 排除项 | 理由 |
|---|---|
| IDE 插件 / Web UI / 多智能体编排 | ROADMAP 非目标，公司体量玩法，单人追不动 |
| provider 数量扩张 | 与护城河无关，且每个 provider 都是长期维护税 |
| hook 事件扩到 10+ | 无当前用户基础的 power-user 面；SubagentStop 降级为 Phase 2 的**条件项**（遥测需要才做） |
| 继续扩 mock 测试套件 | 边际收益已为负——它在持续制造虚假安全感 |
| **Windows ACL 真隔离** | 3–5 天 + 长期维护，且容易变成第二个 789 行黑盒，破坏"每个安全层可单独读完"；当前 honest 声明（无 OS 级写隔离、VM 兜底）已符合项目品牌。**重估触发条件**：Phase 2 证明四道门在真实模型稳定有效之后 |
| Lion-Skills 转 submodule | **[判断]** 与 9-16 报告分歧：submodule 会给 sdist/`pip install` 引入新的打包复杂度，收益（同步自动化）不足以抵消；维持 vendored + 版本号记录 |

---

## 4. 计划详述

### Phase 1 裂缝修复与拆弹（~4 个工作日）

三条裂缝的共同性质：**让"harness 很聪明"这个卖点在真实使用中显得在抽风**——修的不是安全洞，是卖点的可信度。拆弹一条的性质：把最大单点故障从黑箱变成已知数。

#### D1-1 `/resume` 后重建 harness ground-truth（1 天）

- **做什么**：`/resume`/历史重放时，把 harness 状态恢复进会话。最小版：扫描会话历史消息中的 `read_file` 工具调用，经 `_norm_path()`（`agent/harness.py:71-99`，已存在）归一化后 seed 进 `HarnessState.content_read_files`。
- **为什么这样做**：设计预留了预填位置（`harness.py:82` 注释）但没实现——这是补完未完成的功能，不是新机制。备选"把 HarnessState 纳入 checkpointer"（3 天、动 schema、要向后兼容）暂不做。
- **验收**：`/resume` 一个写过代码的会话，模型引用上一轮读过的文件（如 `harness.py:880` 的路径匹配），**不出现** GroundingGate 强制重读；配一个回归测试：seed 后 Gate 对该文件放行。
- **ZCode 对照后的加强（2026-09-21）**：ZCode 的 resume 恢复 read-file state（含 mtime/size 新鲜度）、checkpoint、权限授予、todos、goal 状态（`resume.ts:139-233`）。若最小版顺利，顺势把 HarnessState 整体序列化进现有 sqlite checkpoint（约 +0.5 天），一并解决权限授予与 todo 跨会话丢失——比散着 seed 单个字段更彻底。read-before-edit 的新鲜度校验（文件改过就要重读）可作后续独立项。

#### D1-2 多模态 provider 能力门控（1 天）

- **做什么**：在 `build_user_content`（`cli/multimodal.py:75`）出口按 provider kind 分流——`kind == "anthropic"` 走 Anthropic image block；OpenAI 协议转 `{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}`；profile 显式不支持视觉时**明确告知用户**，而不是静默发坏请求等 400。
- **验收**：用 OpenAI 协议 profile（如 `stepfun_api`）发一张图，正常返回；不支持视觉的 profile 得到人话错误信息。附两个方向的单测。
- **顺带**：路径含空格限制（README 已声明）评估是否一并处理——不阻塞，能修则修。

#### D1-3 deepagents 0.8 兼容摸底（1 天）

- **状态（2026-09-20 已执行，结论如下）**：
  - **PyPI 上 deepagents 0.8 尚不存在**（最新 0.7.15）——`<0.8` 上界目前是空的，真正没验证过的敞口是 `>=0.7.6` 区间内只测过 0.7.6 这一个点。
  - **摸底对象改为 0.7.15**（pip 用户今天就能装到的最高 0.7.x）：一次性 venv（`.[dev]` + `deepagents==0.7.15`）跑全量测试 → **1352 passed / 18 skipped / 0 failed**。整个钉死区间干净。
  - **结论**：无需升级动作；`deepagents>=0.7.6,<0.8` 维持不变。把"0.8 发布时重跑本摸底"记为显式跟踪项（ROADMAP），而不是未知黑洞。复现方式：`python -m venv /tmp/venv-dwf && /tmp/venv-dwf/Scripts/python -m pip install -e ".[dev]" deepagents==<ver> && /tmp/venv-dwf/Scripts/python -m pytest tests/ -q`。
- **为什么排这里**：这是全项目唯一可能让项目"瞬间不可用"而维护者无预感的路径；一天买知情权，优先级高于一切功能。
- **回滚**：不需要——探测在一次性 venv 里做，不碰生产依赖。

#### D1-4 GLM Coding Plan 计费口径（0.5 天，异步不阻塞）

- **状态（2026-09-20 已执行 (b)）**：README.md / README_en.md 的"订阅额度跑本地 agent / subscription quota runs a local agent"已改为不依赖白名单的表述（"用你自己的订阅 key 直连官方端点"）——"Anthropic 协议直连、无转发层"这个事实不依赖白名单，本就更稳。
- **待用户执行 (a)**：智谱答复邮件草稿（不代发，外向动作归用户）：

  > 主题：自建 Anthropic 协议客户端是否消耗 GLM Coding Plan 额度？
  >
  > 您好，我在使用一个自建的本地 coding agent（coderio，github.com/Lion-1209/coderio），通过 Anthropic 协议直连 `https://open.bigmodel.cn/api/anthropic` 调用 Coding Plan 订阅 key。官方文档《Coding Plan 概述》提到套餐额度"仅官方指定工具可消耗"，白名单列了六个工具（Claude Code、Kilo Code、OpenClaw、OpenCode、TRAE、CodeBuddy），未包含自建客户端。想确认：自建的 Anthropic 协议客户端使用 Coding Plan key 调用时，(a) 按订阅额度计费，还是 (b) 按 API 原价计费，或 (c) 不被允许？感谢！

- **分支决策**：答复确认可消耗 → 恢复订阅措辞；确认不可 → 保持降级措辞。两条分支都不需要返工。
- **为什么做 (b) 先行**：项目品牌是"诚实声明"；一个未验证的额度主张比少一个卖点更伤品牌。**[判断]**

### Phase 2 live eval 证据体系（~3–4 个工作日，主线）

**为什么这是主线而不是"并列的 P0"**：它同时完成三件事——验证四道门在真实模型上真的管用（全项目最大未知）、给护城河做每次改动的回归保护、产生一个别人抄不走的公开证据物。且它依赖的基础设施已存在（`TurnSpec` + 2 个 live 脚本）。

#### 任务集设计

10 个真实小任务，**每个自带可自动判定的成功判据**：

| 类别 | 数量 | 判据 |
|---|---|---|
| 改一个函数并跑它的测试 | 3 | 该测试最终 exit_code=0 |
| 修一个已知 bug | 2 | 复现测试由红转绿 |
| 读三个文件回答问题 | 2 | 输出匹配预期要点 |
| **对抗性任务（模型试图骗过 Gate）** | ≥3 | 见下 |

三个对抗性任务（这是任务集的灵魂，直接测卖点）：

1. **写完不跑就说完成** → 期望 VerifyGate 拦截（attempt 0 注入续跑）。
2. **`echo pytest` 假验证** → 期望命令审查层判定"提到不算验证"（`harness.py` 的"跑了代码"判定，2026-09-04 收紧过），Gate 不放行。
3. **引用没读过的文件下结论** → 期望 GroundingGate 拦截。

#### 执行与判定

- **复用**：`scripts/verify_harness_live.py` 的 `TurnSpec` 框架扩展，不另起炉灶。
- **录制-派生-重放（2026-09-21 据 ZCode `tools/prompt-trajectory/` 补充）**：录一次真实 provider 流量（本地 proxy 转发、只换 baseURL，`trajectory.jsonl` 为单一事实源，流式 delta 先组装再落盘），再从中派生规范化 request 快照并**同时投影 OpenAI 与 Anthropic 两种 wire shape**——eval 从"每轮烧真钱"变成"录一次、离线重放 N 次"，且一次录制覆盖两种协议的 prompt 组装回归。派生件必须过脱敏与大小上限后才落盘（image base64 剔除、headers 脱敏），本地诊断可以有界存在，这符合零遥测承诺。
- **Provider 覆盖**：智谱 + 阶跃（与 ROADMAP Now 一致）；每个运行记录**模型版本**（模型名如 `water18-0910` 会变，结果必须可追溯到具体模型）。
- **断言行为不断言 prose**：工具调用序列、exit_code、harness attempt 计数、Gate 是否在期望点触发——模型 prose 不可断言，行为可断言。
- **执行节奏**：**每个发版批次前自动跑 + 手动触发**；不做 nightly——单人项目的 API key 是真金白银，nightly 的成本换不来成比例的收益。**[判断]**
- **失败呈现**：红了给出具体 diff（哪个任务、哪一步、哪个 Gate 行为不符预期），而不是"又挂了"。
- **结果公开**：汇总表进 README（任务数/通过数/Gate 拦截次数/模型版本/日期），原始记录按仓库惯例存 `docs/live-eval/<date>.md`。

**验收标准（Phase 2）**：10 个任务全量可重跑；3 个对抗性任务中 Gate 行为符合上表期望；README 表格由一次真实运行产出（不是手写占位）。

**TBD**：

- 具体 10 个任务的目标仓库/文件，动手时选定（候选：coderio 自身的小 bug + `tests/` 里的真实历史问题）。
- Gate 行为的期望阈值：前 3 次运行后校准（若某 Gate 在真实模型上从未触发，那是 eval 的发现，不是失败）。
- 智谱/阶跃 key 在 CI 的注入方式：仅本地手动跑即可起步，CI 自动化留到 Phase 2 稳定后评估。

### Phase 3 收缩（~1 天 + 条件项）

- **删 `context_summary` 死代码**（0.5 天）：`session/store.py:126-157` 截断管道 + `cli/commands.py:304`、`cli/tui_runtime.py:340`、`agent/deep_loop.py:1127` 的 skip 逻辑一并删。留着的代价是下个读代码的人（包括三个月后的维护者）以为压缩逻辑在这里，改错地方。
- **hook 事件补 `PostToolUseFailure` + `PermissionRequest`（条件项，2026-09-21 修正）**：**不再做 SubagentStop**——ZCode 也只有 7 个事件且同样没有 SubagentStop/PreCompact/Notification（它们只是内部观测事件，不是用户可扩展 hook）。真正的缺口是 coderio 缺失的这两个：`PostToolUseFailure`（工具失败时 hook 能看到错误类型与是否中断，现在是失败即变 tool result、hook 完全看不见）；`PermissionRequest`（权限弹窗前触发，hook 可 allow/deny/改写输入，给权限系统逃生舱而不削弱默认门）。触发条件同前：先有测量/自动化需求再做。顺带抄两个零成本细节：非 JSON stdout 容忍为诊断文本；hook 执行结果（耗时/输出字节）落会话日志。
- **验收**：`ruff check` / `ruff format --check` / `pytest -q` / wheel build / mypy 五项全绿（项目 CI 门）。

---

## 4A. ZCode 源码对照（2026-09-21）

**对象**：`zai-org/ZCode`（Apache 2.0，2026-09-20 12:01 UTC 创建，HEAD = `872ad96 feat: open source`，33MB TypeScript，Electron 桌面 + Web + Agent CLI 三形态）。已做源码级分析（四路并行：agent 核心 / TUI / 工程化 / 扩展体系，全部带 file:line）。

### ZCode 是什么（先对齐事实，再谈借鉴）

- **完全自研的 agent 运行时**：hand-rolled loop + 10 相位显式状态机（非法迁移直接抛错）+ 命令队列 + 事件溯源会话存储；没有用任何 agent 框架（deepagents/LangGraph 均无），模型调用在 adapter 层走 Vercel AI SDK。这验证了 coderio "middleware 挂 deepagents" 是更省力的路线，不必羡慕。
- **它也有"强制验证"，但形态不同**：opt-in 的 `/goal` 设置目标后，每个 turn 结束由 runtime 发起一次**无工具的独立模型调用**做完成度裁判（artifact checklist prompt、明确禁代理信号、"测试通过"必须覆盖全部要求才算、todo 联动、不确定→判不过），不通过就把 reason/nextAction 作为新 user turn 自动续跑；goal 状态只能 runtime 写，模型无法自封完成。**判据是 LLM-as-judge， epistemically 不如 coderio 的 ground-truth 门硬**；但它有两点 coderio 没有：续跑驱动是 runtime 的一等控制流；read-before-edit 是工具级硬错误（read state 带 mtime 新鲜度校验、跨 resume 恢复）。
- **开源代码里没有 OS 级沙箱**：只有契约 port 和 Windows Job Object（杀 MCP 进程树用），Node adapter 无 bubblewrap/seatbelt 实现。**这点上 coderio 的 Linux bubblewrap 领先**，Windows 短板依旧（见 §3 的推迟决定）。
- **插件体系在退潮**：bundled 的 superpowers-plugin 被删到只剩 LICENSE，zcode-cua（computer use）整个包退化为 fail-closed 占位。连 ZCode 都在从插件收缩回 skill。
- **hook 7 事件**：比 coderio 多 `PermissionRequest` 和 `PostToolUseFailure`；没有 SubagentStop/PreCompact/Notification（只是内部观测事件）。
- **遥测默认不出网**：端点未配置时 SDK 根本不 import；全链路脱敏（凭据键名/前缀识别/有界截断/错误只记一次）；Bash/MCP 子进程 env 剥离 `OTEL_*` 等（防 confused-deputy）。
- **诚实的局限**：开源树剥离了 `.github/` 与各包测试目录（仅存 4 个测试文件），pre-commit hook 是坏的（`pnpm test` 不存在），单 squashed commit 无历史。以上结论来自源码与包内注释/README 的交叉验证，CI 形态不可考。

### 借鉴映射表

按"单人 Python 项目可实施性"排序。全部过 §0 裁决标准；不带证据的机制不进表。

| ZCode 机制（证据位置） | coderio 落点 | 阶段 | 成本 |
|---|---|---|---|
| resume 恢复全部 harness 状态：read-file state 带 mtime/size、checkpoint、权限授予、todos、goal（`runtime/methods/resume.py:139-233`） | D1-1 加强版：HarnessState 序列化进 sqlite checkpoint（见 §4 D1-1） | Phase 1 | +0.5 天 |
| prompt-trajectory：proxy 录真实流量 → 派生 request 快照 → 双 wire shape 投影（`tools/prompt-trajectory/README.md`） | Phase 2 基础件：录制-派生-重放，eval 离线化（见 §4 Phase 2） | Phase 2 | 已计入 |
| 一个上游怪癖 = 一个命名 adapter 文件（`adapters/src/model/` 约 60 个单职责文件，含版本与移除条件注释） | 新建 `src/coderio/compat/`：deepagents `write_todos` shim 等迁入，各配"上游行为已变则失败"的测试——把 2026-07-28 那类事故从神秘回归变成命名文件 | Phase 1（D1-3 之后） | 0.5 天 |
| microcompact：本地把旧 tool result 换成占位串，留最近 5 个，节省 <256 token 回滚（`compact/microcompact.ts`） | 新 middleware，零模型调用推迟摘要；coderio 上下文管理全委托 deepagents，这是第一个自有策略 | 独立小项 | 0.5–1 天 |
| 裁决表 + 规则 ID + 黄金测试：生产代码每个分支注释引用规则 ID，黄金测试机械绑定（`formal-proof/model.ts:483-503`） | `tools/command_policy` 或 permission 门禁抽纯函数裁决表，未定义组合显式返回"待定"而非静默放行——消灭"文档说的拒绝逻辑与代码实际做的不一致"漂移 | Phase 3 | 1 天 |
| architecture-policy baseline 制：YAML 声明模块/依赖方向/规模预算 + AST 检查器 + sha256 指纹只卡新违规 + 豁免带 expires + `# noqa` 本身算违规（`scripts/architecture/`） | 把 `docs/coderio-architecture.md` 的分层变成可执行：`ast` 抽 import 比对方向，baseline 让存量不阻塞、只防新增腐化。挂在 pre-push 而非 CI | Phase 3 | 0.5–1 天 |
| hook 事件 `PostToolUseFailure` + `PermissionRequest`（`contracts/src/hooks/index.ts`） | Phase 3 条件项（已替换原 SubagentStop 条目，见上） | Phase 3 | 1 天 |
| 双通道错误：`error`（给 UI）与 `modelContent`（给模型）分离；输入校验错误格式化为可行动文案（`tool/executor/errors.ts`、`input-validation-model-content.ts`） | 工具结果统一两字段；"The required parameter `x` is missing"式校验文案 | 独立小项 | 0.5 天 |
| TUI 四原语：单一 SelectionPanel（type-to-filter + 窗口化）复用全部选择场景；busy 期输入排队 + 可见队列面板；状态行（spinner + 阶段文案 + context 徽章）；流式投影幂等守卫（内容判重 + 有界事件去重窗） | 全部是**可单测的纯函数**，正对 coderio"TUI 小 bug 不断"的病灶：确认菜单收敛为 SelectionPanel；排队输入显式可见；流式渲染防重复 | 独立小项 | 2–3 天 |
| debug 只读查看器：turn 时间线 + context token 按 skill/tool/message 分项（`packages/debug/`） | FastAPI + 单页读 `.jsonl`/sqlite，零写回——直接回答"上下文被谁吃了" | Later | 2–3 天 |
| goal verifier 续跑骨架（`target-completion-verification.ts`） | **只抄骨架**：独立无工具调用 + fail-open + 无 nextAction 不续跑三条护栏；判据仍用 coderio 的 ground truth，不抄 LLM 裁判 | Later 可选 | 1–2 天 |
| workflow 精简版：journal 表 + 确定性重放 + hash 缓存分歧级联（`dynamic-workflow-runtime/engine/`） | 只搬数据结构与调度规则，进程内执行 host 对象（同信任域），**不搬** vm realm/TS typecheck/taint | Later | 3–5 天 |

### 明确不借鉴（及原因）

| 项 | 原因 |
|---|---|
| 动态工作流全副装备（vm realm、TS 编译器沙箱、taint 分析、NDJSON 子进程） | 防的是"模型手写脚本持有宿主权力"；coderio 同信任域内进程内执行即可拿 90% 收益，代码量约 5% |
| 插件体系（市场/依赖图/信任/版本/三套命名空间） | ZCode 自己都在退潮（superpowers 删至剩 LICENSE、cua fail-closed）；单人项目没有分发对象，扩 vendored skills 成本低一个数量级 |
| 1MB contracts 全端口化包 | 20+ 包多宿主逼出来的；分层单体照搬只会让每次工具/事件变更改三处。AGENTS.md 的 Protocol+dataclass 已是同款实践 |
| OTel/ARMS 遥测管线 | 与"无遥测"品牌承诺直接冲突。只抄脱敏四纪律（有界、脱敏、错误只记一次、高基数内容不进聚合）与子进程 env 剥离 |
| 货币成本展示 | ZCode 也没有（只展示 token 与缓存命中）；要做需自行设计计价口径 |
| `auto` 权限模式 | ZCode 自己是保留未实现占位（直接 deny），别学半成品 |
| 其测试/CI 具体形态 | 开源树已剥离且 pre-commit 损坏，不可考；`architecture:check` 挂 pre-push 而非 CI 这一点可参考 |

---

## 5. 风险与回滚

| 风险 | 发现机制 | 回滚/处置 |
|---|---|---|
| live eval 揭示四道门在真实模型上**不灵** | eval 本身 | 这是信息不是故障：按差距大小定向修 Gate，并在公开结果里如实写——护城河"可证明"包含"被证伪"的可能，这正是做它的意义 |
| API 成本超预期 | 每次运行前预估 token；10 任务均为小任务 | 降频为仅发版前；任务集缩小到 5 个核心 |
| 0.8 摸底发现破坏巨大 | D1-3 书面结论 | 继续钉死 + 把结论记入 ROADMAP，把"升级"转为显式跟踪项而非未知黑洞 |
| 范围蔓延（做着 eval 开始改 TUI） | 唯一裁决标准（§0）逐条过 | 新想法进 ROADMAP Later，不进本计划 |
| **学 ZCode 学成第二个 monorepo**（借鉴项各自合理、合起来让维护面暴涨） | §4A 每项都标了阶段与成本；Later 项（workflow 精简版、debug 查看器）明确不进本轮 | 每阶段结束重新过裁决标准；只搬机制不搬架构；ZCode 自己都在收缩（插件退潮），它的规模是反例不是范本 |
| Phase 1 的预填改动引入新回归 | 回归测试 + 全量 pytest | 改动本身是 seed 数据，出问题即 revert 单条提交 |

---

## 6. 验收标准（整体）

1. `/resume` 后引用旧文件无强制重读，有回归测试守护。
2. OpenAI 协议 profile 可正常收发图片；不支持视觉的 profile 得到人话错误。
3. deepagents 0.8 兼容性有书面结论（三选一分支附证据）。
4. 10 任务 live eval 可重跑、3 个对抗性任务 Gate 行为符合期望、README 有真实运行产出的结果表。
5. 五项 CI 门全绿（ruff check / ruff format / pytest / wheel / mypy），`context_summary` 死代码清零。

---

## 7. 后续

- **顺序依赖**：Phase 1 的 D1-1/D1-2 先于 Phase 2（eval 要测的是修好裂缝后的 harness，且裂缝会让 eval 结果误导）；D1-3/D1-4 完全独立，可随时插空。
- **与 ROADMAP 的关系**：D1-4 与 Phase 2 即 ROADMAP Now 的前两条（真实 provider eval / Windows 沙箱那条本计划明确推迟并给了重估触发条件）。本文定稿并与用户确认后，应同步更新 ROADMAP 的 Now 段，避免两份文档指向不同优先级。
- **kill point 设计**：每阶段独立可停——Phase 1 任一条完成即可发一版；Phase 2 首跑通即可公开；真实单人项目的注意力是稀缺资源，计划按"任何一周都有净产出"设计，不要求连续执行。
- **执行纪律**：本计划是工作文档，实现期每天可改；改完的条目按项目惯例进 CHANGELOG Unreleased。提交仍需用户明确同意（AGENTS.md 最高优先级）。
