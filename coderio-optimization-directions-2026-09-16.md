# coderio 优化方向分析报告

> 分析日期：2026-09-16（Asia/Shanghai）
> 视角：第三方。coderio 侧结论经源码级核实（带 `file:line`）；竞品侧来自官方文档与仓库元数据。
> 证据分级：**[实测]** 本地验证过；**[资料]** 官方文档/仓库元数据；**[判断]** 综合推断。
> 本报告主体是**优化方向**。评分与竞品对比压缩为第 2 节的支撑证据，完整对比矩阵见同目录 `coderio-competitive-analysis-2026-09-16.md`。

---

## 0. 执行摘要

coderio 加权总分 **7.02/10，11 款产品里排第 6**。但排名不是本报告的重点——重点是它的能力分布形状：**16 个维度里 2 项第一、3 项垫底，方差全行业最大**。这是典型的"专才"profile，而不是全面平庸。

**优化方向可以归成一句话：把已经在四道门上证明过的工程能力，复制到当前空白的三个地方（真实 provider 证据、平台沙箱覆盖、扩展面），同时砍掉拖累单人维护的三处债务。**

具体地说，本次核实后我认为真正值得做的事按 ROI 排序是：

| 优先级 | 方向 | 成本 | 一句话理由 |
|---|---|---|---|
| **P0** | 确认 GLM Coding Plan 计费口径 | 1 封邮件 | 唯一可能改变项目存在理由的事 |
| **P0** | 真实 provider live eval（缩小版） | 2–3 天 | 1346 个 mock 测试是错的证据类型 |
| **P1** | `/resume` 后重建 harness ground-truth | 1 天 | 核心卖点在恢复会话时静默降级 |
| **P1** | 多模态加 provider 能力门控 | 1 天 | "一用就崩"级别的坑 |
| **P1** | 删死代码 + 缩依赖 | 1 天 | 单人项目最实际的续命动作 |
| **P2** | hook 事件从 5 个扩到 10–12 个 | 2 天 | 重度用 subagent 却没有 SubagentStop 钩子 |
| **P2** | Windows 真写隔离（WSL2/bwrap 路径） | 3–5 天 | 主打的一等公民恰恰没有边界 |
| **P3** | LSP-as-verification | 1 周 | 四道门的自然延伸，把 9 分推向 10 |

**两处必须更正**（我在初版分析里搞错了，已在第 3 节详细说明）：`auto_allow_if_sandboxed` 的 fail-open 已经关闭；harness 状态不受压缩影响。这两个原我以为最致命的点，实际都不是问题——真正的问题在别处，见 P1-3。

---

## 1. 优化方向详述

### P0-1　先确认 GLM Coding Plan 到底能不能用订阅额度

**问题**：README 主打"原生支持智谱 GLM Coding Plan……你的订阅额度跑本地 agent，不需要转发、不需要中间层"。但智谱官方文档 `docs.bigmodel.cn/cn/coding-plan/overview` 明确写了**"仅官方指定工具可消耗套餐额度"**，白名单是六个：Claude Code、Kilo Code、OpenClaw、OpenCode、TRAE、CodeBuddy。**coderio 不在列。** **[资料]**

**为什么这是 P0**：这是全项目唯一一个"如果答案是否，定位需要重新表述"的问题。其他所有优化都是在既有定位上做深，这一条决定定位本身成不成立。

**两种可能**：(a) 白名单只是支持范围声明，白名单外仍按 API 原价计费——那"订阅额度跑本地 agent"这句不成立；(b) 白名单不完整，或 Anthropic 协议直连有隐式支持。

**怎么做**：找智谱商务/开发者支持问一句"自建的 Anthropic 协议客户端能否消耗 Coding Plan 额度"。**不建议先改 README**——在得到答复前，两个方向都是猜测。若确认不能，定位叙事需要从"订阅额度"转向"协议直连 + StepFun 独占 + Windows 一等公民"（这三条都不依赖白名单，见下）。

**验收**：拿到明确答复，README 表述与之一致。

---

### P0-2　真实 provider live eval：把 1346 个 mock 测试换成对的证据类型

**问题**：**[实测]** 1346 个测试、19,999 行测试代码，但**几乎全部是 `fake BaseChatModel` + `stub run_deep_agent`**，零真实 provider 集成测试。ROADMAP.md:15-18 自己承认这是最大缺口。

**为什么重要**：这不是"测试不够多"，是**证据类型错误**。mock 测试证明的是"我的代码按我的预期工作"，不是"它在真实 provider 上工作"。而真实坑恰好全在这条边界上：流式 chunk 边界、tool_calls 分段传输、thinking block 格式差异、`water18-0910` 这类模型返回 `type: "thinking"` 的内容块、Provider 400 的形状。**1346 个绿灯会给阅读者虚假的安全感**——包括给你自己。

**怎么做（缩小版即可起步，别做全家桶）**：

1. 挑 10 个真实小任务（改一个函数 + 跑它的测试；修一个已知 bug；读三个文件回答问题），**必须自带可自动判定的成功判据**（测试通过 / 输出匹配）。
2. 用真实 key 跑，记录每步：模型返回的 tool_calls 是否完整、harness 是否拦截、最终 exit_code。
3. 断言重点放在**行为**而非 prose：参照 Gemini CLI 的 EDK——断言"调了哪些工具、调用顺序、是否避免了破坏性命令"，理由是"模型输出不确定，prose 断言脆弱" **[资料]**。Aider 的 225 题 Exercism benchmark 是另一个好参照：练习自带测试 = 自验证判据 **[资料]**。
4. **其中至少 3 个任务必须设计成"模型想骗过 VerifyGate"**（写完不跑测试就说完成、`echo pytest` 假验证、引用没读过的文件）——这正好同时测 P0-2 和 D1 卖点。

**成本**：2–3 天起步。ROADMAP 已列为 Now 第一条。

**验收**： nightly 能跑，红了有具体 diff 可查，而不是"又挂了"。

---

### P1-3　`/resume` 后重建 harness ground-truth（本报告最重要的新发现）

这条初版分析里没有，是本次核实 `HarnessState` 存储位置时发现的。

**问题**：**[实测]** `HarnessState` 是 **middleware 实例属性**，不是图状态的一部分：

```python
# src/coderio/agent/harness_middleware.py:85-86
self.harness = Harness(
    state=HarnessState(),      # ← Python 实例属性，不进 graph state
    ...
)
```

它记录 `read_files` / `content_read_files` / `has_wrote_this_turn`（`harness.py:487-498`）。`content_read_files` 是 GroundingGate 判"模型引用的文件它到底读过没有"的唯一依据（`harness.py:880`）。

**两个后果，一好一坏**：

- **好消息（更正我初版的判断）**：因为它不在 `messages` 里，deepagents 的 SummarizationMiddleware 压缩**动不到它**——压缩保留 `messages[cutoff_index:]` 尾部（`summarization.py:781`），而注入的强制续跑 HumanMessage 正是最后一条，结构上必然存活。我初版担心的"harness 注入消息过压缩后失效"**不成立**。
- **坏消息**：SqliteSaver checkpointer 只持久化图状态，**不含 HarnessState**。所以 `/resume` 一个会话、或 checkpointer 不可用而重放全历史时（`deep_loop.py:1101-1111` 有 warning 路径），**harness 状态是空的，而模型引用的文件是上一个会话里读的**。

**具体表现**：恢复会话后，模型写了代码、引用它上一轮读过的文件 → GroundingGate 查到 `content_read_files` 是空的 → 判定"没读过" → 强制续跑让它重读。**每引用一个文件就浪费一轮**，且对话里会出现莫名其妙的"请先读取该文件"。这不会造成安全问题（方向是保守的），但会让人觉得 harness 在抽风——恰恰损害"harness 很聪明"这个卖点的可信度。

**怎么做**：两条路，建议先做 (a)。

- **(a) 从会话历史预填（1 天）**：`harness.py:82` 的注释已经提到"cross-turn pre-fill normalizes when seeding content_read_files"——**说明设计上就预留了这个位置，但 `grep pre_fill|seed` 在源码里找不到任何实现**。这是没写完的功能。补上：`/resume` 时扫一遍历史消息里的 `read_file` 工具调用，归一化后 seed 进 `content_read_files`。归一化函数 `_norm_path` 已经存在（`harness.py:71-99`，处理大小写/斜杠/`./`），直接复用。
- **(b) 把 HarnessState 纳入 checkpointer（3 天）**：改成图状态的一部分。更彻底，但要动 schema 且要考虑向后兼容。

**验收**：`/resume` 一个写过代码的会话，让它引用之前读过的文件，**不应**出现强制重读。

---

### P1-4　多模态加 provider 能力门控

**问题**：**[实测]** `multimodal.py:75-100` 构造 Anthropic 格式 image block 后**对所有 provider 一律发出**，没有能力探测。`config/models.py` 的注册表里 `stepfun_api` / `openai` / `ollama` / `openai_custom` 都是 OpenAI 协议——它们收到 Anthropic 的 image block 会怎样，**未测**。

**为什么重要**：这是"一用就崩"级别而不是"偶发报错"级别。用户发一张截图，如果用的是 OpenAI 协议 profile，很可能直接 400，而且错误信息不会告诉用户"因为你选了不支持的 provider"。多模态是 README 的卖点之一，崩在这里很伤。

**怎么做**：在 `multimodal.py` 的出口处按 provider kind 分流——`kind == "anthropic"` 走 image block，否则转成 OpenAI 的 `image_url` 形状（`{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}`）。再加一个 profile 级开关，不支持就明确告知用户而不是静默发坏请求。顺带：路径含空格不支持（README 已声明）这个限制可以一起处理掉。

**成本**：1 天。**验收**：用 `stepfun_api`（OpenAI 协议）profile 发一张图，能正常返回。

---

### P1-5　砍死代码 + 缩依赖（单人项目最实际的续命动作）

**问题一：死代码。** **[实测]** `session/store.py:125-157` 的 `context_summary` 截断管道**没有写入者**——`grep context_summary` 只找到读取方（`store.py:144` 读它、`commands.py:304` 和 `tui_runtime.py:340` 跳过它、`deep_loop.py:1127` 跳过它），没有任何地方产出 `kind="context_summary"`。这是旧引擎遗留路径。留着它会让下一个读代码的人（包括三个月后的你）以为压缩逻辑在这里，进而改错地方。

**问题二：依赖面。** **[实测]** 14 个运行时依赖，且引擎钉死在 `deepagents>=0.7.6,<0.8`（`pyproject.toml:41-47` 注释说明 0.6 不行、0.8 可能破坏）。上游不稳定的实证已经出现：deepagents 0.7.6 从默认图里移除了 `write_todos`，coderio 得手动补回（`deep_loop.py:760-767`）。另外 Lion-Skills 是 **vendored 拷贝而非 submodule**，每次上游更新都要手工同步。

**怎么做**：
1. 删 `store.py` 的死 `context_summary` 分支（1 小时，注意同步删 `commands.py`/`tui_runtime.py` 里对应的 skip 逻辑）。
2. Lion-Skills 转 submodule（2 小时），消除手工同步。
3. 对 `deepagents <0.8` 上界做一次定向兼容验证：装 0.8 跑一遍全量测试，记录到底破了什么。**目的不是升级，是知道风险敞口有多大**——现在完全不知道 0.8 会不会让项目瞬间不可用。

**成本**：1 天。**验收**：`ruff`/`pytest` 全绿；对 0.8 的兼容状态有书面结论。

---

### P2-6　hook 事件从 5 个扩到 10–12 个

**问题**：**[实测]** 只有 5 个事件（SessionStart / UserPromptSubmit / PreToolUse / PostToolUse / Stop）。对标 Claude Code 的 **32 个事件 × 5 种 handler**（command/http/mcp_tool/prompt/agent）**[资料]**。

**为什么挑这几个扩**（按 coderio 自身的使用特征排序，不是照抄 Claude Code）：

1. **SubagentStop** —— **最该补的一个**。coderio 重度使用 subagent（只读 research/general-purpose），却完全没有"子代理结束"钩子。想统计成本、想审计子代理读了什么，现在都做不到。
2. **PostToolUseFailure** —— 工具失败时想跑修复逻辑（比如 lint 失败自动 format）接不上。Claude Code 有，Codex 也有（`PostToolUseFailure`）。
3. **PreCompact / PostCompact** —— 压缩前后想导出上下文、想验证 harness 状态，没有钩子。虽然 P1-3 已证明 harness 状态不受压缩影响，但用户层仍然想在压缩点做留存。
4. **Notification** —— TUI 已有通知通道，暴露成 hook 让外部脚本能推消息，成本极低。

**成本**：2 天（事件名常量 + 触发点 + 文档 + 测试）。**验收**：4 个新事件各有一个测试，且 `.coderio` 配置能配上。

---

### P2-7　Windows 真写隔离

**问题**：**[实测]** Windows 沙箱是 Job Object 资源限制 + **Restricted Token 是 NO-OP**（非管理员下 token 与原件同为 Medium 完整性，写权限无隔离，`win_sandbox.py:1-42` 诚实标注，789 行代码实际零隔离）。macOS 完全无沙箱（`sandbox_runner.py:8-12` 直接 plain subprocess）。

**先澄清一个好消息**：`auto_allow_if_sandboxed` 的 fail-open **已经关闭**，而且关闭得比我初版判断的更严谨——`repl.py:109-139` 的 `_sandbox_boundary()` 把边界分成三档（`filesystem` 真写边界 / `resource` 仅资源限制 / `none`），`repl.py:167` 只在 `filesystem` 时才 auto-approve，否则（`repl.py:169-176`）打黄条警告并回退逐条确认、黑名单仍然生效。注释里还写了审计出处（P1-11 + gpt5.6-sol review）。**这条不用动。**

**剩下的是**：Windows 主打"一等公民"，但恰好没有写边界。

**怎么做（推荐 b)）**：
- **(a) 承认现状**：README 明确写"Windows/macOS 无 OS 级沙箱，恶意代码请用 VM"。半天，零风险，但放弃卖点。
- **(b) WSL2 + bubblewrap**：Windows 10+ 自带 WSL2，bubblewrap 在 WSL2 里可用。coderio 已经在用 Git Bash，走 WSL2 的路径依赖不算新东西。**3–5 天，真正解决问题**，且能把 macOS 一并覆盖（macOS 上也可引导到 Docker/bwrap）。
- **(c) 重写 Restricted Token**：需要管理员权限才能有意义，对普通用户不可行，不建议。

**成本**：(a) 半天 / (b) 3–5 天。**验收**：Windows 上 `sandbox_mode=write` 时，沙箱内进程写工作目录之外的文件失败。

---

### P3-8　LSP-as-verification

**为什么现在不做但值得记着**：**[实测]** 四道门的判定依据目前只有 exit_code（结构化）+ marker 正则回退。LSP 诊断是**比 exit code 更早、更便宜、更细**的信号——写完文件 3 秒就知道类型错，不用等整轮测试。OpenCode 已经做了（~30 个 LSP server 按扩展名自动拉起，把 diagnostics 当 agent 反馈信号）**[资料]**，Claude Code 也有内置 LSP 工具（编辑后自动报类型错误让模型同 turn 自纠）**[资料]**。

**为什么排 P3**：成本 1 周，且它强化的是**已经领先的维度**（D1 已经 9 分）。P0/P1 修的是塌陷的维度，边际收益更高。

**怎么做（如果做）**：给 Python 项目接 basedpyright/pylsp，write 后查诊断，作为 VerifyGate 的**辅助**信号源——不替换 exit_code（exit_code 是 ground truth，LSP 是提前预警）。注意 OpenCode 官方自注 "it is not always a net benefit" **[资料]**，所以要做成可关的。

---

## 2. 支撑证据：你现在站在哪

完整 16 维 × 11 产品矩阵见 `coderio-competitive-analysis-2026-09-16.md`。这里只放结论性数字。

**加权排名**：Claude Code 7.93 > Codex 7.80 > OpenCode 7.25 > Cursor 7.15 > Qwen Code 7.12 > **coderio 7.02** > Cline 6.79 > OpenHands 6.73 > Gemini CLI 6.34 > Aider 5.22。

**coderio 的形状**（这才是优化方向的依据）：

| | 维度 | 分 | 解读 |
|---|---|---|---|
| 🥇 | 验证与结果纪律 | **9** | 10 款竞品无等价物。真正的护城河 |
| 🥇 | 国内 Coding Plan | **9\*** | \* 受 P0-1 制约 |
| 🥉 | 上下文管理 | 5 | 全委托 deepagents，无自有策略 |
| 🥉 | 项目健康度 | 4 | 单人维护（Lion 270/278 commits） |
| 🥉 | 生态与入口形态 | 4 | 只有 TUI + headless，无 SDK/ACP/IDE |

**两条赛道级事实，影响所有方向的优先级**：

1. **全行业都没有强制验证门**。本次覆盖的 10 款竞品，没有任何一款有等价于 VerifyGate 的 turn 级强制验证机制。最接近的三个替代品各自偏科：OpenHands Critic（事后 LLM 评审，experimental）、Gemini CLI EDK（开发者侧，不进运行时）、OpenCode LSP（被动诊断，默认关闭）。**这是至今未失效的差异化，也是最不该动的资产。**
2. **开源 CLI agent 死亡率 3/13**。Roo Code 已归档关闭（README 原话 "shut down on May 15th"）、iFlow CLI 关停、Aider 约 4 个月无提交。D13 给 coderio 打 4 分的现实背景——**P1-5（砍债务）的价值不只是清爽，是续命。**

**一个对定位有利的发现**：StepFun 至今没有任何第一方 CLI agent，Step Plan 文档也不成体系 **[资料，未查到]**。GLM 侧虽然有 ZCode 但那是 Electron 桌面应用不是 CLI，且白名单不含 coderio。所以即使 P0-1 的答复不利，"StepFun 独占 + Anthropic 协议直连 + Windows 一等公民"这三条依然成立且无人竞争——**其中 Windows 一等公民有实证支撑**（10 款里只有 Codex 做了真 Windows 沙箱，Claude Code 明确要求 WSL2）。

---

## 3. 两处更正（重要）

初版分析（口头汇报 + `coderio-competitive-analysis-2026-09-16.md`）里，我把这两条列为"最致命的差距 #1 和 #3"。本次核实源码后，**两条都不成立**，已在上文对应位置修正：

**更正 1：harness 注入消息过压缩后不会失效。**
初版依据是"压缩按 keep 策略保留近期消息，未有专门测试证明其存活"。实际读 `deepagents/middleware/summarization.py:781` 后确认：压缩保留 `messages[cutoff_index:]` 尾部，而 harness 的强制续跑消息是 `after_model` 注入的**最后一条**消息，结构上必然在保留窗口内。更进一步，`HarnessState` 是 middleware 实例属性（`harness_middleware.py:86`），**根本不在 messages 里**，压缩碰不到它。

→ 真正的风险不在压缩，而在 `/resume`（HarnessState 不随 checkpointer 持久化），已升级为 P1-3。

**更正 2：`auto_allow_if_sandboxed` 的 fail-open 已经关闭。**
初版依据是子 agent 报告的"代码里有警告但仍是 fail-open 配置"。实际读 `cli/repl.py:109-185` 后确认：`_sandbox_boundary()` 把平台边界分成 `filesystem` / `resource` / `none` 三档，Windows 明确返回 `resource`，`auto_exec = boundary_kind == "filesystem" and ...` 因此为 False，同时打印黄条警告并回退逐条确认，黑名单仍生效。注释还写了审计出处（P1-11 + gpt5.6-sol review）。

→ 这条不需要任何改动。Windows 剩下的问题是**压根没有写边界**（Restricted Token 是 NO-OP），已重写为 P2-7，性质从"关掉 fail-open"变成"提供真隔离"。

**教训**：两条错误都源于把子 agent 的次级汇报当成了核实过的结论。安全相关的判断必须读 `file:line` 原文——尤其当结论是"这里有个洞"的时候，因为洞可能上周刚补上。

---

## 4. 建议执行顺序

```
第 1 周    P0-1 发邮件问智谱（阻塞其他定位相关决策）
          P1-3 /resume 预填（1 天，核心卖点可信度）
          P1-4 多模态门控（1 天，防一用就崩）
第 2 周    P0-2 live eval 缩小版（2-3 天，本季度最大杠杆）
          P1-5 删死代码 + submodule + 0.8 兼容摸底（1 天）
第 3-4 周  P2-6 hook 扩事件（2 天）
          P2-7 Windows 真隔离（3-5 天，若决定做 b) 路线）
后续       P3-8 LSP-as-verification（可选，锦上添花）
```

**明确不建议做**（违反 ROADMAP 非目标"单人可维护"）：IDE 插件矩阵、60+ provider、browser/computer use、Web UI、60 个 hook 事件、benchmark 全家桶。OpenCode（207k stars / 950 contributors）和 Cursor 是公司体量的玩法，硬追只会拖垮维护。

**一条元建议**：这个项目的独特资产不是代码，是**"每个安全层都能被单独读完"这个性质**。任何优化方向如果破坏了它（比如引入抽象层、引入大量配置项、引入需要跨文件推理才能理解的机制），即使功能上更强也不该做。P1-3/P1-4/P1-5 都符合这条；P2-7 的 (b) 路线要留意别让沙箱逻辑变成第二个 789 行的黑盒。

---

## 5. 本报告的局限

1. **竞品侧是文档级与仓库元数据级，非源码级**（除 Codex 读了 Rust 源码枚举）。若竞品实际能力强于文档自述，其分数应上调，coderio 的相对优势会缩小。
2. **权重是判断而非共识**。把"国内 Coding Plan"权重从 7% 提到 15%，coderio 升到第 4；把"生态入口"提到 12%，coderio 掉到第 8。**逐项分数比排名更有信息量。**
3. **P0-1 的白名单事实未经智谱侧确认**，仅据官方文档页面。这是最大的单点不确定性。
4. **未实测任何竞品的实际行为**——全部静态事实采集。报告中没有任何 benchmark 数字是编造的（多数竞品官方无可核验分数，故一律不写）。
5. **P1-3 的表现是推断而非实测**：我确认了 HarnessState 不随 checkpointer 持久化、且无预填实现，但**没有实际跑一次 `/resume` 后引用旧文件**来观察是否真的触发强制重读。建议动手前先复现一次。
