# coderio

> A skill-driven coding agent with a structural harness, foldable-thinking TUI, and crew orchestration. Built on langchain + langgraph + Lion-Skills. Windows-first.

**coderio** 是一个技能驱动的编程 agent。它的"骨架"是 [Lion-Skills](https://github.com/Lion-1209/Lion-Skills) 套件（clarify→spec→task→execute→verify→commit 工作流），coderio 给它配上真正能干活的工具、一个强制遵循工作流的 **harness 状态控制层**，以及交互式 Textual TUI。参照对象是 claude code / codex / zcode。

核心理念：**skill 是操作手册，harness 是执行纪律，工具是手**。三者分层，互不替代。

---

## 特性

- **harness 四道门硬约束**：agent 写了代码但没运行验证就想说"完成"时，harness 拦截终止、强制续跑——不是提示词软规则，是系统级结构控制（基于工具调用 ground truth）
- **意图分类**：自动区分 CODE / QA / ANALYZE 三种意图，编码任务走工作流，问答直接答
- **渐进式披露**：skill 正文按需加载，系统提示词 ~2K tokens 而非全量堆砌
- **交互式 TUI**：Textual 终端 UI，思考折叠（Ctrl+O）、流式输出、工具调用状态栏（动画 spinner + 步骤 + 计时器）、slash 命令自动补全、会话恢复选择器
- **两种模式**：交互式单 agent（日常）+ 6-agent crew 流水线（大需求，LangGraph 编排）
- **工具错误韧性**：工具调用失败变成 tool result 回灌给模型自我修正，不中断 turn
- **多 provider**：智谱 GLM / 阶跃 StepFun 的 coding plan（Anthropic 协议）+ OpenAI 兼容

---

## 快速开始

### 安装

```bash
git clone <repo-url> coderio
cd coderio
python -m venv .venv

# Windows (Git Bash)
.venv/Scripts/python.exe -m pip install -e ".[dev]"

# Linux / macOS
.venv/bin/python -m pip install -e ".[dev]"
```

要求：Python 3.11+，Windows 上需安装 Git Bash（bash 工具依赖）。

### 配置

首次运行会触发 onboarding 向导（选 provider、选模型、填 API key），配置自动写入 `~/.coderio/config.toml` 和 `~/.coderio/credentials`。也可手动配置：

```bash
# ~/.coderio/config.toml
[model]
provider_id = "bigmodel_coding_plan"   # 智谱/阶跃/OpenAI/Anthropic/Ollama/自定义
default = "glm-5.2"

[tools]
permission_mode = "auto"                # confirm | plan | auto
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

# 6-agent crew 流水线（大需求）
coderio crew "实现一个待办事项命令行工具" --auto

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
| `/mode <confirm\|plan\|auto>` | 切换权限模式 |
| `/model <name>` | 运行时切模型 |
| `/skills` | 列出 skill（★ = 已激活） |
| `/cost` | 查看本次会话 token 用量 |
| `/clear` | 重置上下文（新会话） |
| `/sessions` | 列出最近会话 |
| `/resume` | 恢复历史会话（↑↓ 选择、Enter 恢复、输入过滤） |

直接输入自然语言即可对话或下达编码任务。

---

## 架构

分层单体，依赖单向向下：

```
CLI 层 (cli/)          Typer app + Textual TUI + slash 命令
  │
Agent 层 (agent/)      ReAct 循环 + harness 状态控制 + 提示词构建
  │
编排层 (crew/)          6-agent LangGraph StateGraph（可选高级模式）
  │
能力层                  tools/ · skills/ · llm/ · session/ · config/
```

### 两种运行模式

| | 单 agent（TUI） | crew（流水线） |
|---|---|---|
| 适用 | 日常交互、问答、编码任务 | 大需求、完整功能开发 |
| agent 数 | 1 个（全工具） | 6 个（每阶段工具物理隔离） |
| harness | 硬约束生效 | 不生效（crew 自有 verify→修复循环） |
| 编排 | ReAct 循环 | LangGraph StateGraph + interrupt |

### harness 四道门（核心）

| 门 | 强度 | 机制 |
|----|------|------|
| **VerifyGate** | 硬，逐级升级 | 写了代码没跑 bash 就声明"完成"→ 拦截、注入强制续跑；2 次后放行 + 红色警告 |
| **CompletionGate** | 硬 | 有未完成 todo 就声明"完成"→ 拦截 |
| **GroundingGate** | 硬 | 引用了从未读取的代码位置就声明"完成"→ 拦截（防止分析建立在臆测上） |
| **PlanGate** | 软提醒 | 没 todo 就写代码 → 工具结果追加 nudge |

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
| langchain >=0.3 | ReAct agent 基础 |
| langgraph >=0.2 | crew 流水线状态图编排 |
| langchain-anthropic >=0.2 | 智谱/阶跃端点接入（Anthropic 协议） |
| textual >=0.40 | 交互式 TUI |
| rich >=13 | 终端渲染 |
| typer >=0.12 | CLI 框架 |
| deepagents >=0.6 | 实验性 engine（可选：`pip install -e ".[deepagent]"`） |

---

## 项目结构

```
src/coderio/
├── agent/          # ReAct 循环、harness、提示词、流式协议
├── cli/            # Typer app、Textual TUI、slash 命令、凭证/onboarding
├── crew/           # 6-agent LangGraph 流水线（orchestrator/agents/state）
├── tools/          # 12 个工具 + 权限门 + langchain 适配
├── skills/         # SkillStore 三层加载 + Lion-Skills 0.3.0（bundled）
├── config/         # 三层 TOML 配置合并
├── session/        # jsonl 会话存储 + resume
└── llm/            # 模型工厂（provider 注册表）
```

Lion-Skills 作为 bundled skill 随包分发（`src/coderio/skills/lion-skills/`），无需单独安装。

---

## 已知限制

- **deepagents engine 是实验性的**：harness 作为 middleware 可用，但默认仍是 ReAct engine。deepagents 已改为可选依赖
- **Windows 编码**：shell 输出在 GBK locale 下有内置兼容方案
- **crew 持久化**：当前用内存 MemorySaver（会话内），sqlite 持久化待后续

---

## 贡献

欢迎贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## License

MIT（见 [LICENSE](LICENSE)）。Bundled Lion-Skills 同为 MIT（见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)）。
