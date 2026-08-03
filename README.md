# coderio

**中文** | [English](README_en.md)

> 一个技能驱动的编程 agent——结构化 harness 约束、可折叠思考的 TUI、deepagents 引擎。基于 langchain + langgraph + deepagents + Lion-Skills，Windows 优先，跨平台。

## 为什么做这个项目

一开始只是想花掉阶跃送的 token，顺便走一遍 langchain 全技术栈搭 agent 的流程。框架搭出来之后觉得单纯的 REPL + CLI 不太酷，就开始折腾 TUI 了——目前 TUI 的效果我自己还挺满意的。

不过整体框架并没有细细调优，所以目前只是一个工作之余搓出来的 demo。开源的目的不是做一个产品，而是希望给那些用 langchain 技术栈的人做一点小参考：deepagents 引擎怎么接、harness 怎么做结构约束、TUI 怎么做流式渲染——这些代码都在，能跑，欢迎拿去玩。

> 关于名字：**coderio = code + rio**（不是 coder + io 哦）。我的英文名是 Lion，本来想叫 codelion，但感觉怪怪的，所以就叫 coderio 了。

---

**coderio** 是一个技能驱动的编程 agent。它的"骨架"是 [Lion-Skills](https://github.com/Lion-1209/Lion-Skills) 套件（clarify→spec→task→execute→verify→commit 工作流），coderio 给它配上真正能干活的工具、一个强制遵循工作流的 **harness 状态控制层**，以及交互式 Textual TUI。参照对象是 claude code / codex / zcode。

核心理念：**skill 是操作手册，harness 是执行纪律，工具是手**。三者分层，互不替代。

---

## 特性

- **harness 四道门硬约束**：agent 写了代码但没运行验证就想说"完成"时，harness 拦截终止、强制续跑——不是提示词软规则，是系统级结构控制（基于工具调用 ground truth）。VerifyGate 解析 bash exit_code，测试失败不再当"验证通过"；**智能跳过非代码文件**（写 .md/.json/.yaml 文档不强制 pytest）；GroundingGate 只在 CODE 模式生效（分析/问答场景不拦截文件名提及）；CompletionGate 检查未完成 todo
- **显式状态机**：实时推导执行阶段（探索→规划→实现→验证→完成），状态栏显示任务阶段 + 模型活动双轴；每轮的 phase 时间线持久化到 session，可回放调试
- **deepagents 引擎**：基于 [deepagents](https://github.com/langchain-ai/deepagents) 的生产引擎，内置上下文管理（offload + 摘要）、子 agent（task 工具，含只读 research 子 agent）、文件系统后端、持久化检查点（SqliteSaver）；**完全替换 deepagents 默认 prompt**，coderio 的 system prompt 独占，无冲突
- **持久化检查点**：graph state 跨 turn 持久化到 sqlite，只需传新消息（不重传完整历史）；SummarizationMiddleware 的累积状态正确保持
- **上下文自动压缩**：deepagents 的 SummarizationMiddleware 在接近上下文窗口 85% 时自动触发——旧消息 offload 到文件 + LLM 摘要，保留近期上下文
- **意图分类**：自动区分 CODE / QA / ANALYZE 三种意图，编码任务走工作流，问答直接答（中英双语信号词）
- **渐进式披露**：skill 正文按需加载，系统提示词 ~2K tokens 而非全量堆砌
- **交互式 TUI**：Textual 终端 UI，思考折叠（Ctrl+O）、流式输出、工具调用状态栏（动画 spinner + 步骤 + 任务阶段 + 计时器 + **turn token 计数**）、slash 命令自动补全、**可折叠 TODO 面板**（实时进度 ✓/→/○）、**纵向权限确认菜单**（↑↓ + Enter，zcode/codex 风格）、**会话管理**（`/resume` 恢复 + Del 删除）、**权限/配置可视化选择器**（`/mode` `/profile`）、**文件修改可视化**、**任务中断**（Esc / ⏹ 按钮）、**错误恢复**
- **deepagents 引擎**：基于 [deepagents](https://github.com/langchain-ai/deepagents) 的生产引擎，内置上下文管理（offload + 摘要）、子 agent（task 工具）、文件系统后端；coderio 的 harness 四道门 + 四级权限作为 middleware 保留
- **工具错误韧性**：工具调用失败变成 tool result 回灌给模型自我修正，不中断 turn；bash 工具超时杀整个进程树（Windows Job Object）、**自动激活项目 .venv**（bash 里的 python 自动指向虚拟环境）
- **工作区路径策略（读写分离）**：写工具（write_file/edit_file/multi_edit/bash cwd）路径必须 resolve 在工作区根目录内，超出即硬拒绝；读工具（read_file/grep/glob/list_dir）不受限，agent 可读工作区外的依赖/配置。`--auto` 模式也执行路径策略——跳过交互确认，不跳过安全边界
- **多 provider + 命名 profile**：智谱 GLM / 阶跃 StepFun 的 coding plan（Anthropic 协议）+ OpenAI 兼容；支持多套配置 profile，`/profile` 运行时切换

---

## 快速开始

### 安装

**方式一：pip 一行安装（推荐，非开发者）**

```bash
pip install "coderio @ git+https://github.com/Lion-1209/coderio.git"
```

装完直接 `coderio` 启动。

**方式二：下载 Release wheel（离线/内网）**

到 [Releases 页面](https://github.com/Lion-1209/coderio/releases) 下载最新的 `coderio-*.whl`，然后：

```bash
pip install coderio-0.1.0-py3-none-any.whl
```

**方式三：从源码安装（开发者）**

```bash
git clone https://github.com/Lion-1209/coderio.git
cd coderio
python -m venv .venv

# Windows (Git Bash)
.venv/Scripts/python.exe -m pip install -e ".[dev]"

# Linux / macOS
.venv/bin/python -m pip install -e ".[dev]"
```

要求：Python 3.11+，Windows 上需安装 Git Bash（bash 工具依赖）。

### 配置

首次运行会触发 onboarding 向导（选 provider、选模型、填 API key），配置自动写入 `~/.coderio/config.toml` 和 `~/.coderio/credentials`。向导验证 key 时会自动探测模型的上下文窗口大小并持久化，压缩阈值精确匹配实际模型。也可手动配置：

```bash
# ~/.coderio/config.toml
[model]
provider_id = "bigmodel_coding_plan"   # 智谱/阶跃/OpenAI/Anthropic/Ollama/自定义
default = "glm-5.2"
context_limit = 128000                  # （可选）onboarding 自动探测写入，0 = 用下面的默认值

[tools]
permission_mode = "auto"                # confirm | plan | auto
workspace_root = ""                     # 受信工作区根目录（空=用启动目录）；写工具路径必须在此目录内

[context]
enabled = true                          # 长会话自动压缩（默认开）
trigger_ratio = 0.6                     # 达到上下文窗口 60% 时触发
keep_recent = 8                         # 保留最近 N 条消息不压缩
model_context_limit = 200000            # fallback：当 profile 未探测到 context_limit 时用
```

支持的 provider：
| provider_id | 说明 | 协议 |
|---|---|---|
| `bigmodel_coding_plan` | 智谱 GLM Coding Plan | Anthropic |
| `stepfun_coding_plan` | 阶跃 StepFun Step Plan | Anthropic |
| `bigmodel_api` / `stepfun_api` | 智谱/阶跃 API Key 直连 | Anthropic / OpenAI |
| `openai` | OpenAI 直连 | OpenAI |
| `anthropic` | Anthropic Claude 直连 | Anthropic |
| `ollama` | 本地 Ollama（无需 key） | OpenAI |
| `openai_custom` | 任意 OpenAI 兼容端点 | OpenAI |

API key 存在 `~/.coderio/credentials`（POSIX 0600 / Windows icacls 保护）。

### 运行

```bash
# 交互式 TUI（Ctrl+O 展开思考、可滚动历史、/ 命令自动补全）
coderio
# 或直接（Windows）
.venv/Scripts/python.exe -m coderio.cli.app
# （Linux / macOS）
.venv/bin/python -m coderio.cli.app

# 指定 provider/model
coderio --provider bigmodel_coding_plan --model glm-5.2

# 管理 skill
coderio skills list
coderio skills install
```

---

## TUI 命令

进入 TUI 后，输入 `/` 触发命令自动补全：

| 命令 | 作用 |
|------|------|
| `/help` | 显示所有命令 |
| `/exit` `/quit` | 退出 |
| `/config` | 查看当前配置（provider/model/mode） |
| `/mode` | 切换权限模式（无参数弹出可视化选择器：confirm/plan/auto） |
| `/model <name>` | 运行时切模型 |
| `/setup` | 重新配置 provider/model（onboarding 向导，自动探测 context window） |
| `/profile` | 切换已保存的配置 profile（可视化选择器） |
| `/skills` | 列出 skill（★ = 已激活） |
| `/cost` | 查看本次会话 token 用量 |
| `/clear` | 重置上下文（新会话） |
| `/sessions` | 列出最近会话 |
| `/resume` | 恢复历史会话（↑↓ 选择、Enter 恢复、输入过滤） |
| `/think` | 展开最近一轮的思考内容 |

**快捷键**：

| 按键 | 作用 |
|------|------|
| `Ctrl+O` | 展开/收起最近一轮的思考 |
| `Esc` / `⏹ 中断` | 中断当前正在执行的 agent 任务（不退出 TUI） |
| `↑↓` + `Enter` | 命令菜单导航（输入 `/` 时弹出） |

直接输入自然语言即可对话或下达编码任务。

---

## 架构

分层单体，依赖单向向下：

```
CLI 层 (cli/)          Typer app + Textual TUI + slash 命令
  │
Agent 层 (agent/)      deepagents 引擎 + harness/permission middleware + 提示词构建
  │
能力层                  tools/ · skills/ · llm/ · session/ · config/
```

### 引擎：deepagents + coderio middleware

coderio 用 deepagents 作为主引擎（上下文管理、子 agent、文件系统后端），在其上叠加两个 middleware：

| middleware | 作用 |
|---|---|
| **HarnessMiddleware** | coderio 的四道门硬约束（验证/完成/grounding/plan），deepagents 本身不强制验证 |
| **PermissionMiddleware** | 四级权限（plan/confirm/auto_edit/full）+ 工作区路径边界 |

deepagents 的默认 BASE_AGENT_PROMPT 被清空——coderio 的 system prompt 独占，避免两套指令冲突。

**子 agent**：内置 research 子 agent（只读，物理隔离不能写不能执行）+ general-purpose（全工具）。主 agent 通过 task 工具按需委派，上下文隔离。

旧的 ReAct 引擎（`agent/loop.py`）保留为 fallback。

### harness 四道门（核心）

| 门 | 强度 | 机制 |
|----|------|------|
| **VerifyGate** | 硬，逐级升级 | 写了代码没跑 bash 就声明"完成"→ 拦截、注入强制续跑；解析 bash exit_code，**测试失败（非 0）不算验证通过**；**写文档/配置文件（.md/.json/.yaml）不触发验证**；2 次后放行 + 红色警告 |
| **CompletionGate** | 硬 | 有未完成 todo 就声明"完成"→ 拦截 |
| **GroundingGate** | 硬（仅 CODE 模式） | 写代码后引用了从未 read_file 的文件就声明"完成"→ 拦截；**ANALYZE 模式（纯读）跳过**——分析里提到文件名是正常行为（基于 105 session 审计：98.2% 误判率，从未拦住真正的虚假引用） |
| **PlanGate** | 软提醒 | 没 todo 就写代码 → 工具结果追加 nudge |

### 上下文治理

deepagents 的 SummarizationMiddleware 自动管理上下文：

| 机制 | 触发 | 行为 |
|------|------|------|
| **offload** | 工具输入/输出 >2万 token | 大块内容自动存盘 + 留指针，不占上下文 |
| **summarize** | token 数达到窗口的 85% | 旧消息 LLM 摘要 + 原文 offload 到 `/conversation_history/` |
| **checkpoint** | 每次 turn 结束 | graph state 持久化到 sqlite，下次只传新消息 |

旧 ReAct 引擎的自研压缩（`compact.py`）保留为 fallback。

### 显式状态机

agent 执行阶段实时推导并显示在状态栏（`步骤3 · [实现] 思考中 · 12.4s`）：

```
探索（read_file/grep）→ 规划（首次 write 无 todo）→ 实现（write + todo）
  → 验证（bash pytest）→ 完成
```

每轮的 phase 时间线持久化到 session jsonl（`kind="phase_timeline"`），可回放调试，但对模型不可见（不会污染上下文）。

详细架构设计见 [`docs/coderio-architecture.md`](docs/coderio-architecture.md)。

---

## 测试

```bash
# 全量单元测试（~15s）
# Windows (Git Bash):
.venv/Scripts/python.exe -m pytest -q
# Linux / macOS:
.venv/bin/python -m pytest -q

# 按模块
.venv/Scripts/python.exe -m pytest tests/agent/ -v    # Windows
.venv/bin/python -m pytest tests/agent/ -v            # Linux / macOS

# Live 验证（连真实模型端点，需设置 ANTHROPIC_API_KEY）
ANTHROPIC_API_KEY=<key> .venv/Scripts/python.exe scripts/verify_harness_live.py   # Windows
ANTHROPIC_API_KEY=<key> .venv/bin/python scripts/verify_harness_live.py           # Linux / macOS
```

三层测试设计：单元测试（逻辑）+ Live 验证（真实集成）+ 手动体验测试。

---

## 技术栈

| 依赖 | 用途 |
|------|------|
| langchain >=0.3 | agent 基础 |
| langgraph >=0.2 | 状态图编排 |
| langchain-anthropic >=0.2 | 智谱/阶跃端点接入（Anthropic 协议） |
| textual >=0.40 | 交互式 TUI |
| rich >=13 | 终端渲染 |
| typer >=0.12 | CLI 框架 |
| deepagents >=0.6 | 生产引擎（上下文管理、子 agent、文件系统后端） |

---

## 项目结构

```
src/coderio/
├── agent/          # deepagents 引擎、harness/permission middleware、提示词、流式协议
├── cli/            # Typer app、Textual TUI、slash 命令、凭证/onboarding
├── tools/          # 工具集 + 权限门 + langchain 适配
├── skills/         # SkillStore 三层加载 + Lion-Skills 0.3.0（bundled）
├── config/         # 三层 TOML 配置合并
├── session/        # jsonl 会话存储 + resume
└── llm/            # 模型工厂（provider 注册表）
```

Lion-Skills 作为 bundled skill 随包分发（`src/coderio/skills/lion-skills/`），无需单独安装。

---

## 已知限制

- **Windows 编码**：shell 输出在 GBK locale 下有内置兼容方案（`_WinLocalShellBackend` 用 bytes + errors='replace' 解码）

---

## 贡献

欢迎贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## License

MIT（见 [LICENSE](LICENSE)）。Bundled Lion-Skills 同为 MIT（见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)）。
