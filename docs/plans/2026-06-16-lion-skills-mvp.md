# Lion-Skills MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 Lion-Skills 的 MVP——1 个模板 + README + 5 个经验证的 skill（commit-message、onboarding-unknown-codebase、naming、error-handling、lion-writing-skills）。

**Architecture:** 纯文档型 skill 仓库，无运行时依赖。每个 skill 一个目录，含 `SKILL.md`（frontmatter + 正文）+ `evals/evals.json`（验证用例）。遵循 spec（`docs/specs/2026-06-16-lion-skills-design.md`）的模板规范与轻量 TDD 验证。

**Tech Stack:** Markdown / YAML frontmatter。验证用 Claude Code 的 Agent 工具派 subagent 跑。

---

## File Structure

| 文件 | 职责 |
|------|------|
| `template/SKILL.md` | 新建 skill 的模板，复制即用 |
| `README.md` | 仓库说明、skill 清单、部署方式 |
| `skills/lion-writing-skills/SKILL.md` | 元 skill：教怎么加新 skill |
| `skills/lion-writing-skills/evals/evals.json` | 元 skill 验证用例 |
| `skills/commit-message/SKILL.md` | 提交信息生成 |
| `skills/commit-message/evals/evals.json` | 验证用例 |
| `skills/onboarding-unknown-codebase/SKILL.md` | 上手陌生代码库 |
| `skills/onboarding-unknown-codebase/evals/evals.json` | 验证用例 |
| `skills/naming/SKILL.md` | 命名决策 |
| `skills/naming/evals/evals.json` | 验证用例 |
| `skills/error-handling/SKILL.md` | 错误处理与日志 |
| `skills/error-handling/evals/evals.json` | 验证用例 |

**顺序依据**：先做模板和元 skill（定义规范），再做 4 个业务 skill（套用规范）。

**通用验证方式**（每个 skill task 的第 3-4 步）：用 Agent 工具派一个 general-purpose subagent，prompt 里要求它"读取 `<skill>/SKILL.md`，然后按其指引处理任务：`<测试 prompt>`"，看输出是否解决问题、是否符合设计意图。不对就改 SKILL.md 重跑。

**提交约定**：所有 commit 用 Conventional Commits 格式（type 英文 + 冒号 + 中文描述，如 `feat: 添加 X skill`），并在 message 尾部追加 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`（用第二个 `-m` 参数传入）。下文各 task 的 commit 命令为简写，执行时按此约定补全 Co-Authored-By。

---

## Task 1: 模板 `template/SKILL.md`

**Files:**
- Create: `template/SKILL.md`

- [ ] **Step 1: 写模板文件**

创建 `template/SKILL.md`，内容：

````markdown
---
name: <skill-name>
description: <只写"何时触发"——第三人称、中文、覆盖用户可能说的各种说法；不写工作流。上限 1024 字符>
---

# <Skill 标题>

## 概述

<一两句：这个 skill 是什么、核心原则。>

## 何时使用

<症状/场景列表，用户什么时候需要它；以及何时不该用。>

## 核心内容

<步骤 / 决策模型 / 模式+例子，按 skill 类型组织。>

## 常见错误

<什么会出错 + 怎么修。>

## 参考（可选）

<大段参考才拆到这里，否则全内联。>
````

- [ ] **Step 2: Commit**

```bash
git -C "e:/999-Git/Lion-Skills" add template/SKILL.md
git -C "e:/999-Git/Lion-Skills" commit -m "chore: 添加 SKILL.md 模板"
```

---

## Task 2: `README.md`

**Files:**
- Create: `README.md`

- [ ] **Step 1: 写 README**

创建 `README.md`，内容：

````markdown
# Lion-Skills

一套个人自建的通用开发工作流 skills 套件，面向 Claude Code（也兼容其他支持 Agent Skills 的工具）。

## 这是什么

Lion-Skills 把高频的软件开发工作流沉淀成可复用的 skill：每个 skill 是一份"操作手册"，Claude 在合适的时机触发它，照着做出更靠谱的结果。

## 包含的 skill

| Skill | 用途 |
|-------|------|
| `commit-message` | 把 diff 提炼成规范的提交信息 |
| `onboarding-unknown-codebase` | 系统化上手陌生代码库 |
| `naming` | 命名决策 |
| `error-handling` | 错误处理与日志设计 |
| `lion-writing-skills` | 教你怎么加新 skill（元 skill） |

## 怎么部署

把需要的 skill 软链或拷贝到 `~/.claude/skills/`（用户级）或项目的 `.claude/skills/`。

Linux/macOS：
```bash
ln -s /path/to/Lion-Skills/skills/commit-message ~/.claude/skills/commit-message
```

Windows（PowerShell，符号链接需管理员权限，或直接拷贝）：
```powershell
Copy-Item -Recurse E:\999-Git\Lion-Skills\skills\commit-message $HOME\.claude\skills\
```

## 怎么加新 skill

见 `skills/lion-writing-skills/SKILL.md`，或复制 `template/SKILL.md` 开始。

## 设计依据

见 `docs/specs/2026-06-16-lion-skills-design.md`。
````

- [ ] **Step 2: Commit**

```bash
git -C "e:/999-Git/Lion-Skills" add README.md
git -C "e:/999-Git/Lion-Skills" commit -m "docs: 添加 README"
```

---

## Task 3: 元 skill `lion-writing-skills`

**Files:**
- Create: `skills/lion-writing-skills/SKILL.md`
- Create: `skills/lion-writing-skills/evals/evals.json`

- [ ] **Step 1: 写验证用例**（先想清楚"什么算成功"）

创建 `skills/lion-writing-skills/evals/evals.json`：

```json
{
  "skill_name": "lion-writing-skills",
  "evals": [
    {
      "id": 1,
      "prompt": "我想加个 skill，每次写完代码帮我自查有没有边界情况没处理。帮我按 Lion-Skills 的规范创建。",
      "expected": "走五步流程，产出一个符合规范的 SKILL.md 草稿（含 frontmatter、正文骨架），而非直接堆内容"
    },
    {
      "id": 2,
      "prompt": "我的 skill 的 description 是'用于代码审查，会检查风格、安全、性能'，帮我看看写得对不对、怎么优化。",
      "expected": "指出这违反'别写工作流'规则（写了会做什么），改成只写触发条件"
    },
    {
      "id": 3,
      "prompt": "我写了这个 SKILL.md，帮我验证它真好用。",
      "expected": "引导用 subagent 带着新 skill 跑 2-3 个真实 prompt 并评审输出"
    }
  ]
}
```

- [ ] **Step 2: 写 SKILL.md**

创建 `skills/lion-writing-skills/SKILL.md`，内容：

````markdown
---
name: lion-writing-skills
description: 当用户想在 Lion-Skills 仓库里创建新 skill、修改现有 skill、或检查 skill 是否符合本仓库规范时使用。典型信号：用户说"帮我加个新 skill""写个 skill""创建一个 skill""这个 skill 怎么改/优化""检查这个 skill 写得对不对"、想把某个重复的工作流沉淀成 skill、想给 Lion-Skills 套件加成员。涉及关键词：创建 skill、写 skill、新 skill、lion-skills、SKILL.md、skill 模板、skill 规范、验证 skill。
---

# Lion Writing Skills

## 概述

Lion-Skills 的"造 skill 流水线"：按本仓库规范，把一个重复的工作流沉淀成可复制、经验证的 skill。

核心原则：skill 是给未来 Claude 读的"操作手册"——它的价值不在于你写了什么，而在于未来的 Claude 在触发它时，能照着做出正确的事。

## 何时使用

- 用户想新增一个 skill（"帮我加个 X 的 skill"）
- 用户想修改/优化现有 skill
- 用户想检查某个 skill 写得是否符合规范
- 想把对话里反复出现的工作流沉淀成 skill

**不该做成 skill 的情况**：一次性的、项目特定的约定（放 CLAUDE.md）、能用简单校验自动化的（写成校验脚本而不是文档）。

## 核心内容

### 第 1 步：判断值不值得做

回答四个问题（都"是"才值得做）：
1. 高频吗？（反复出现，不是一次性的）
2. 跨项目吗？（不止一个代码库用得到）
3. 有判断空间吗？（需要决策/经验，不是机械操作——机械操作写成脚本）
4. 未来 Claude 不读这个 skill，会做错或做差吗？

### 第 2 步：复制模板，填 frontmatter

复制 `template/SKILL.md`。填两个字段：
- `name`：动词前置、全小写、连字符（如 `commit-message`，不是 `commit_message` 或 `CommitMessage`）
- `description`：**只写"何时触发"**，第三人称，中文，把用户可能说的各种说法都列进去

> 关键：description 绝不能写工作流（"先做 A 再做 B"）。原因——测试发现 Claude 会偷懒只读 description 跳过正文。把流程留给正文。

### 第 3 步：写正文

按这个骨架：概述 / 何时使用 / 核心内容 / 常见错误（参考 `template/SKILL.md`）。

写作约定：
- **解释 why**，少用全大写 MUST（讲道理比下命令管用）
- **一个优秀例子** > 多语言版本
- 正文 < 500 行；超了拆 `references/`
- 中文为主，工具名/术语保留英文

### 第 4 步：轻量验证

写 2–3 个真实测试 prompt（像真实用户会说的，带具体细节），用 subagent 带着新 skill 跑：在 Claude Code 里用 Agent 工具，prompt 里要求它先读 `<skill>/SKILL.md`，再按指引处理任务。看输出是否解决了问题、是否符合设计意图。

### 第 5 步：迭代

输出不对就改正文，重跑。重点改：
- 触发不准 → 改 description（补关键词、加症状）
- 做错了 → 正文讲清 why 或加常见错误
- 啰嗦/跑偏 → 删冗余

## 常见错误

| 问题 | 后果 | 修法 |
|------|------|------|
| description 写了工作流 | Claude 只读 description 跳过正文 | description 只留触发条件 |
| 正文超 500 行没拆 | 加载慢、难维护 | 大段参考拆到 `references/` |
| 堆 MUST 不讲 why | 死板、难泛化 | 解释原因 |
| 一个例子写多语言 | 平庸、难维护 | 一个优秀例子 |
| name 用下划线/驼峰 | 不规范 | 全小写连字符 |
````

- [ ] **Step 3: 跑验证用例 1**

用 Agent 工具派 subagent，prompt：「读取 `skills/lion-writing-skills/SKILL.md`，然后按它的规范处理这个需求：『我想加个 skill，每次写完代码帮我自查边界情况。』产出符合规范的 SKILL.md 草稿。」

**Expected**：subagent 走五步流程（尤其先判断值不值得做），产出含合规 frontmatter（description 只写触发）+ 正文骨架的草稿，而非直接堆内容。

- [ ] **Step 4: 评审，按需迭代**

检查 subagent 输出是否走流程、frontmatter 是否合规。若 description 误写工作流或漏判断步骤，改 SKILL.md 重跑。

- [ ] **Step 5: Commit**

```bash
git -C "e:/999-Git/Lion-Skills" add skills/lion-writing-skills
git -C "e:/999-Git/Lion-Skills" commit -m "feat: 添加 lion-writing-skills 元 skill"
```

---

## Task 4: `commit-message`

**Files:**
- Create: `skills/commit-message/SKILL.md`
- Create: `skills/commit-message/evals/evals.json`

- [ ] **Step 1: 写验证用例**

创建 `skills/commit-message/evals/evals.json`：

```json
{
  "skill_name": "commit-message",
  "evals": [
    {
      "id": 1,
      "prompt": "我把一个 React 组件的状态从 useState 换成了 useReducer，还顺手修了个内存泄漏 bug。写个 commit message。",
      "expected": "归类为 refactor，subject 说做了什么（不写怎么实现），body 解释为什么，提及附加的 bug 修复"
    },
    {
      "id": 2,
      "prompt": "上一条提交信息是 'update'，太笼统了。假设当前 diff 是给登录接口加了密码哈希校验，帮我重写成规范的。",
      "expected": "重写为 feat/fix 类，scope=auth，subject 具体，符合 Conventional Commits"
    },
    {
      "id": 3,
      "prompt": "我改了登录接口、数据库迁移脚本、README 三个地方，怎么提交？",
      "expected": "识别这是不相关改动，建议拆成 3 条提交而非塞一条"
    }
  ]
}
```

- [ ] **Step 2: 写 SKILL.md**

创建 `skills/commit-message/SKILL.md`，内容：

````markdown
---
name: commit-message
description: 当用户需要为 Git 改动编写或优化提交信息（commit message）时使用。典型信号：用户说"帮我写 commit message""这次改动怎么提交""提交说明怎么写""git commit 写啥"、刚看完 diff 准备提交、一次改了多处不知怎么概括、或现有提交信息太笼统（如"update""改了下"）需要改成规范写法。涉及关键词：commit message、提交信息、提交说明、git commit、Conventional Commits、feat/fix、commitizen、提交规范、changelog。
---

# Commit Message

## 概述

把一团 diff 提炼成规范、可追溯的提交信息。核心：一条提交说清"做了什么"和"为什么"，让未来的自己（和同事）能从提交历史里读懂项目演进。

## 何时使用

- 写完代码要提交，需要写 commit message
- 改了多处，不知怎么概括
- 现有提交信息太笼统（"update""fix bug"），要重写
- 想统一团队提交规范

**不该用**：还没写完代码（先写代码）；只想看 diff（用 `git diff`）。

## 核心内容

### 格式：Conventional Commits

```
<type>(<scope>): <subject>

<body 可选：为什么改>
```

**type 选一个**：`feat`（新功能）、`fix`（修 bug）、`docs`（文档）、`refactor`（重构不改行为）、`test`（测试）、`chore`（构建/杂务）、`perf`（性能）。

### 从 diff 提炼三步

1. **归类**：主要是加功能（feat）、修 bug（fix）、还是重构（refactor）？
2. **定 scope**：影响哪个模块/文件？（如 `auth`、`api`、`ui`）可省略。
3. **写 subject**：一行，祈使句，说"做了什么"不说"怎么做的"，≤50 字符。

### 多处改动怎么办

- **相关改动** → 一条提交，body 列要点
- **不相关改动** → 拆成多条（用 `git add -p` 分块暂存）

判断"相不相关"：这些改动服务于同一个目的吗？是 → 一条；否 → 拆。

### body 写什么

写**为什么**和**影响**，不写代码细节（代码自己会说话）：动机/背景、副作用/破坏性变更、关联 issue/PR。

### 语言

subject 默认英文（Conventional Commits 生态惯例），中文团队也可中文——跟随仓库现有风格；body 可中文；用户明确偏好优先。

### 例子

**例 1（单改动）** — 把 useState 换成 useReducer：
```
refactor(auth): 用 useReducer 替换登录表单的状态管理

复杂的状态转换用 reducer 更清晰，也为后续多步校验做准备。
顺带修了 useEffect 未清理订阅导致的内存泄漏。
```

**例 2（多相关改动）** — 加登录接口 + 测试：
```
feat(auth): 实现邮箱密码登录接口

- 新增 /api/login 端点
- 加密码哈希校验
- 补单元测试覆盖成功/失败场景
```

**例 3（该拆的）** — 登录接口、迁移脚本、README 三件不相关事，拆三条：
```
feat(auth): 实现登录接口
chore(db): 添加用户表迁移脚本
docs: 更新 README 部署说明
```

## 常见错误

| 问题 | 修法 |
|------|------|
| 太笼统（"update""改了点"） | 写清 type + 具体做了什么 |
| 一条提交塞多个无关改动 | 拆成多条 |
| subject 写"怎么做"而非"做了什么" | "用 useReducer 替换"而非"改了 state 逻辑" |
| 只说现象不说动机 | body 补上为什么 |
````

- [ ] **Step 3: 跑验证用例 3**（最能区分好坏的多文件场景）

用 Agent 工具派 subagent，prompt：「读取 `skills/commit-message/SKILL.md`，然后按其指引处理：『我改了登录接口、数据库迁移脚本、README 三个地方，怎么提交？』」

**Expected**：subagent 建议拆成 3 条提交（feat/chore/docs），而非塞一条；每条 subject 符合 Conventional Commits。

- [ ] **Step 4: 评审，按需迭代**

确认输出会拆分、subject 合规。否则改 SKILL.md。

- [ ] **Step 5: Commit**

```bash
git -C "e:/999-Git/Lion-Skills" add skills/commit-message
git -C "e:/999-Git/Lion-Skills" commit -m "feat: 添加 commit-message skill"
```

---

## Task 5: `onboarding-unknown-codebase`

**Files:**
- Create: `skills/onboarding-unknown-codebase/SKILL.md`
- Create: `skills/onboarding-unknown-codebase/evals/evals.json`

- [ ] **Step 1: 写验证用例**

创建 `skills/onboarding-unknown-codebase/evals/evals.json`：

```json
{
  "skill_name": "onboarding-unknown-codebase",
  "evals": [
    {
      "id": 1,
      "prompt": "我刚 clone 了 vite 这个项目到本地，帮我看懂它是干啥的、结构怎样、入口在哪。把它当成陌生代码库来梳理。",
      "expected": "走三遍法，产出一个结构化项目地图（是什么/技术栈/目录结构/核心流程/关键文件/怎么跑），而不是一头扎进某个文件"
    },
    {
      "id": 2,
      "prompt": "同事离职留下一个项目，我接手了，不知道怎么跑起来也看不懂整体逻辑。帮我梳理。",
      "expected": "强调先读 README/配置搞清怎么跑，再理结构，产出地图"
    },
    {
      "id": 3,
      "prompt": "讲讲这个代码库从 HTTP 请求到响应经过哪些层。",
      "expected": "走第三遍主线追踪，从入口顺核心调用链讲清楚"
    }
  ]
}
```

- [ ] **Step 2: 写 SKILL.md**

创建 `skills/onboarding-unknown-codebase/SKILL.md`，内容：

````markdown
---
name: onboarding-unknown-codebase
description: 当用户需要快速上手或理解一个陌生的代码库/项目时使用。典型信号：用户说"帮我看懂这个项目""我刚接手 xx""这个代码库是干啥的""怎么快速熟悉这个项目""这个项目的结构/架构""入口在哪"、clone 了新 repo 不知从哪读起、要给同事讲清一个项目、接手离职同事留下的代码。涉及关键词：上手、熟悉、理解、读代码、看懂项目、项目架构、入口、目录结构、代码导航、onboarding、codebase。
---

# Onboarding Unknown Codebase

## 概述

用一套分层流程把陌生代码库变成一张"项目地图"，而不是一头扎进某个文件读细节。核心：先建立全局心智模型，再按需深入。

## 何时使用

- clone 了新项目，不知从哪读起
- 接手别人的代码（同事离职、新入职）
- 要给别人讲清楚一个项目
- 想搞清某个项目的整体架构/数据流

**不该用**：只想找某个具体函数/bug（直接搜）；项目你已经熟了。

## 核心内容

### 三遍法

**第一遍·宏观（5-10 分钟，别读代码）**
读"元信息"文件，搞清"这是什么、技术栈、怎么跑"：
- `README.md` —— 项目用途、怎么跑起来
- 包管理清单：`package.json` / `go.mod` / `Cargo.toml` / `requirements.txt` —— 技术栈、依赖
- `Dockerfile` / `docker-compose.yml` —— 怎么部署
- CI 配置：`.github/workflows/` —— 怎么构建测试
- 配置：`.env.example`、`config/` —— 运行时配置

**第二遍·结构（10-15 分钟）**
看目录布局和分层，建立"地图骨架"：
- 顶层目录各自负责什么？（`src/` 源码、`tests/` 测试、`docs/` 文档）
- 分层模式：前后端分离？MVC？分层架构？
- 入口在哪？（`main`、`index`、`app`、`server`）
- 模块之间怎么依赖（谁调用谁）

**第三遍·主线（15-30 分钟）**
从入口顺一条**核心调用链**读到底，不求全：
- 入口启动后，处理一个典型请求/操作，经过哪些模块？
- 只读主干，遇到分支先标记跳过
- 这条链路走通，就理解了项目的"骨架动力"

### 产出：项目地图

给用户一份结构化地图（不是流水账）：

```
## 这是什么        <一两句：项目用途>
## 技术栈          <语言、框架、关键依赖>
## 目录结构        <顶层目录职责 + 入口位置>
## 核心流程        <从入口出发的主调用链，逐步>
## 关键文件        <最重要的 3-5 个文件及职责>
## 怎么跑起来      <安装、配置、启动命令>
```

### 边界

- **先地图后细节**：别一上来读某个函数实现
- **不求全**：80/20，先懂主干
- **产出要结构化**：口头散讲不如一份地图

## 常见错误

| 问题 | 修法 |
|------|------|
| 一上来深挖某文件 | 先宏观再结构，最后才深入 |
| 不看配置就猜架构 | 第一遍先读 README 和包清单 |
| 只口头讲不产出 | 给用户一份结构化地图 |
| 试图读完所有文件 | 只读主干，标记跳过分支 |
````

- [ ] **Step 3: 跑验证用例 1**

用 Agent 工具派 subagent，prompt：「读取 `skills/onboarding-unknown-codebase/SKILL.md`，然后按其指引梳理：『我刚 clone 了 vite，帮我看懂它是干啥的、结构怎样、入口在哪。』」（让 subagent 用 Explore 能力读 vite 仓库或本地副本）

**Expected**：产出结构化项目地图（六部分齐全），走三遍法，而非散读。

- [ ] **Step 4: 评审，按需迭代**

确认输出是结构化地图、先宏观后深入。否则改 SKILL.md。

- [ ] **Step 5: Commit**

```bash
git -C "e:/999-Git/Lion-Skills" add skills/onboarding-unknown-codebase
git -C "e:/999-Git/Lion-Skills" commit -m "feat: 添加 onboarding-unknown-codebase skill"
```

---

## Task 6: `naming`

**Files:**
- Create: `skills/naming/SKILL.md`
- Create: `skills/naming/evals/evals.json`

- [ ] **Step 1: 写验证用例**

创建 `skills/naming/evals/evals.json`：

```json
{
  "skill_name": "naming",
  "evals": [
    {
      "id": 1,
      "prompt": "我有个函数叫 processData，它其实是把 CSV 文本转成 JSON 并校验数据合法性。起个更好的名字。",
      "expected": "给出具体动词前缀的名字如 parseCsvToRecords / validateAndConvertCsv，解释为何 processData 不好（无意义动词）"
    },
    {
      "id": 2,
      "prompt": "这个变量叫 flag，根据上下文它表示用户是否已登录。叫啥好？",
      "expected": "建议 isLoggedIn，说明布尔用 is 前缀、flag 是无意义词"
    },
    {
      "id": 3,
      "prompt": "我在设计一个 REST API，资源是'用户订单里的商品'。URL 路径和返回字段怎么命名？",
      "expected": "URL 用复数名词嵌套如 /users/{id}/orders/{oid}/items，字段遵循语言 case 约定，避免动词进 URL"
    }
  ]
}
```

- [ ] **Step 2: 写 SKILL.md**

创建 `skills/naming/SKILL.md`，内容：

````markdown
---
name: naming
description: 当用户需要为代码标识符决定名字时使用——变量、函数、方法、类、文件、模块、API、数据库字段、配置项等。典型信号：用户说"这个变量/函数叫啥名好""这个名字合适吗""帮我起个名""命名规范""这个 API 怎么命名"、写代码时卡在取名、觉得现有命名不准或误导想改、想统一一个模块的命名风格。涉及关键词：命名、取名、起名、变量名、函数名、类名、文件名、API 命名、identifier、naming convention、命名约定、命名风格。
---

# Naming

## 概述

名字要回答"它是什么/它做什么"，而不是"它在哪/什么类型"。好名字让代码自解释，坏名字逼读者来回跳转确认含义。

## 何时使用

- 写代码时卡在取某个名字上
- 觉得现有名字不准、误导，想改
- 设计 API/接口，定资源和字段名
- 想统一一个模块的命名风格

**不该用**：已在用团队既定规范（遵循即可）；纯格式问题（用 linter）。

## 核心内容

### 决策四问

取名前问：
1. **它代表什么？**（数据/实体）→ 名词
2. **它做什么？**（动作）→ 动词
3. **谁会用它？**（读者上下文）→ 用领域熟悉的词
4. **有歧义吗？**（同名异义/异名同义）→ 消除

### 快速规则

- **函数/方法用动词**：`fetchUser`、`validateEmail`
- **变量/属性用名词**：`user`、`emailAddress`
- **布尔用 is/has/can/should**：`isLoggedIn`、`hasPermission`
- **避免否定**：`isEmpty` 优于 `isNotEmpty`
- **避免无意义词**：`data`、`info`、`helper`、`manager`、`util`（说不清是啥）
- **少缩写**：除非领域通用（`id`、`url`、`http`）；`usr`、`cfg`、`tmp` 别用
- **范围越窄越具体**：模块内可简短，全局/API 要自解释
- **一致**：同一概念全项目用同一个词（别 `user`/`account`/`member` 混用）

### before / after

| 不好 | 好 | 原因 |
|------|-----|------|
| `processData(d)` | `parseCsvToRecords(text)` | 说清做什么、入参是啥 |
| `flag` | `isLoggedIn` | 布尔用 is，表意明确 |
| `userList` | `users` | 复数即集合，去冗余类型后缀 |
| `strName` | `name` | 去掉匈牙利记法前缀 |
| `handleStuff()` | `sendInvoice()` | 动词具体，不抽象 |
| `UserInfoManager` | `UserProfile` | 去掉无意义 Manager |

### API 命名

- 资源用复数名词：`/users`、`/orders`
- 嵌套表达从属：`/users/{id}/orders/{oid}/items`
- 字段 case 跟随语言：JS camelCase、Python/DB snake_case
- 动作用 HTTP 方法表达，别塞进 URL：`DELETE /users/123` 而非 `/users/123/delete`

## 常见错误

| 问题 | 修法 |
|------|------|
| 缩写成谜（`usrCfg`） | 写全，除非领域通用缩写 |
| 同概念多名（user/account/member） | 全项目统一一个 |
| 匈牙利记法（`strName`、`intAge`） | 去掉类型前缀 |
| 过度抽象（Manager/Handler/Util） | 用具体名词 |
| 否定布尔（isNotValid） | 改肯定（isValid） |
````

- [ ] **Step 3: 跑验证用例 1**

用 Agent 工具派 subagent，prompt：「读取 `skills/naming/SKILL.md`，然后按其指引处理：『函数叫 processData，其实是把 CSV 转 JSON 并校验。起个更好的名。』」

**Expected**：给出具体动词名（如 `parseCsvToRecords`），解释 `processData` 为何不好，套用决策四问。

- [ ] **Step 4: 评审，按需迭代**

确认输出套规则且给具体名。否则改 SKILL.md。

- [ ] **Step 5: Commit**

```bash
git -C "e:/999-Git/Lion-Skills" add skills/naming
git -C "e:/999-Git/Lion-Skills" commit -m "feat: 添加 naming skill"
```

---

## Task 7: `error-handling`

**Files:**
- Create: `skills/error-handling/SKILL.md`
- Create: `skills/error-handling/evals/evals.json`

- [ ] **Step 1: 写验证用例**

创建 `skills/error-handling/evals/evals.json`：

```json
{
  "skill_name": "error-handling",
  "evals": [
    {
      "id": 1,
      "prompt": "这段代码 try { const r = await fetch(url); return r.json(); } catch(e) { console.log(e); } 太糙了，怎么改进？",
      "expected": "指出裸 catch 静默吞错、日志缺上下文；按三态决策（网络瞬时→可重试），改进为分级日志+上下文+针对性处理"
    },
    {
      "id": 2,
      "prompt": "我要调用一个第三方支付 API，可能超时、被限流（429）、余额不足。这些错误分别该怎么处理？",
      "expected": "超时/限流→指数退避重试；余额不足→不重试、抛业务错误给上层；区分系统错误与业务错误"
    },
    {
      "id": 3,
      "prompt": "写一个用户注册函数：邮箱格式错、邮箱已注册、数据库连不上。这三种错误怎么设计错误类型和返回？",
      "expected": "自定义错误类型区分业务错误（格式/已注册）和系统错误（DB），错误信息面向开发者含上下文，给用户的提示单独做"
    }
  ]
}
```

- [ ] **Step 2: 写 SKILL.md**

创建 `skills/error-handling/SKILL.md`，内容：

````markdown
---
name: error-handling
description: 当用户需要设计或改进错误处理与日志时使用。典型信号：用户说"这里怎么处理错误""异常怎么处理""要不要 try/catch""日志怎么打""错误信息怎么写""错误码怎么设计""这个错误该抛还是该吞"、遇到边界情况/失败场景/空值/超时/重试不知怎么处理、现有代码错误处理粗糙（裸 catch、吞异常、日志没用）想改进。涉及关键词：错误处理、异常处理、try/catch、catch、抛异常、错误信息、错误码、日志、logging、重试、retry、边界情况、防御性编程、defensive。
---

# Error Handling

## 概述

错误要**可观测**（知道发生了什么）、**可区分**（能区分类型）、**不吞**（不静默 catch）。好的错误处理让你在出问题时能快速定位，而不是对着空白或 `undefined` 发呆。

## 何时使用

- 写代码遇到边界/失败场景（空值、超时、外部调用失败），不知怎么处理
- 现有错误处理粗糙（裸 catch、吞异常），要改进
- 设计错误类型/错误码体系
- 决定"这个错该抛还是该处理、要不要重试"

**不该用**：确定性逻辑（没失败可能，别过度防御）。

## 核心内容

### 三态决策：抛 / 处理 / 重试

遇到一个可能的错误，先判断属于哪种：

- **抛（throw / propagate）**：你处理不了，或属于底层职责。往上传，让上层决策。例：数据库连不上，service 层抛，让调用方决定降级还是报错。
- **处理（handle）**：你能恢复或降级。在原地处理掉。例：配置缺失用默认值、可选字段为空用 fallback。
- **重试（retry）**：瞬时故障（网络抖动、限流）。指数退避重试，别死循环。例：调外部 API 超时，重试 3 次，间隔翻倍。

**原则**：越靠近错误的层越了解错误含义；但只有能负责任的层才该处理。不知道怎么办就抛上去。

### 日志

- **记什么**：上下文——输入、相关状态、错误对象（含堆栈）
- **不记什么**：敏感信息——密码、token、个人隐私
- **分级**：`debug`（排查细节）、`info`（关键业务节点）、`warn`（可疑但可继续）、`error`（出错了需关注）

错误日志要让你光看日志就重现问题现场，而不是只看到 `Error: something went wrong`。

### 错误信息面向开发者

错误消息给**排查的开发者**看，不是给终端用户看：写清**发生了什么** + **可能原因** + **怎么排查**，并带上下文。

```
// 差
throw new Error("failed")

// 好
throw new Error(`用户注册失败：邮箱 ${email} 已被注册。检查是否应走登录流程。`)
```

给用户的提示要另外做（友好、不泄露技术细节），别直接把异常 message 吐给用户。

### 模式

- **自定义错误类型/错误码**：区分业务错误（邮箱已注册）和系统错误（DB 连不上），让上层针对性处理
- **fail fast**：启动期检查前置条件，不满足直接报错，别带病运行
- **边界统一兜底**：在系统边界（HTTP 中间件、API 网关）统一捕获并转换错误，别让内部异常直接漏到外部
- **别 catch 了又原样 throw**：要么加上下文再抛，要么处理掉

## 常见错误

| 问题 | 修法 |
|------|------|
| 裸 `catch(e){}` 静默吞错 | 至少记日志 + 决定抛/处理 |
| catch 了又 `throw e`（无添加） | 加上下文再抛，或处理掉 |
| 日志只记 `e.message` | 记上下文 + 完整错误对象 |
| 把堆栈直接吐给终端用户 | 内部日志详细，给用户友好提示 |
| 不区分错误类型 | 用自定义错误类型/错误码 |
| 无限重试/无退避 | 指数退避 + 最大次数 |
````

- [ ] **Step 3: 跑验证用例 2**

用 Agent 工具派 subagent，prompt：「读取 `skills/error-handling/SKILL.md`，然后按其指引处理：『调第三方支付 API，可能超时、限流(429)、余额不足，分别怎么处理？』」

**Expected**：超时/限流→指数退避重试；余额不足→不重试抛业务错误；区分系统错误 vs 业务错误。

- [ ] **Step 4: 评审，按需迭代**

确认输出套三态决策、区分错误类型。否则改 SKILL.md。

- [ ] **Step 5: Commit**

```bash
git -C "e:/999-Git/Lion-Skills" add skills/error-handling
git -C "e:/999-Git/Lion-Skills" commit -m "feat: 添加 error-handling skill"
```

---

## Task 8: 收尾验证

- [ ] **Step 1: 全量结构检查**

```bash
ls -R "e:/999-Git/Lion-Skills/skills" "e:/999-Git/Lion-Skills/template"
```

Expected：每个 skill 目录含 `SKILL.md` + `evals/evals.json`；`template/SKILL.md` 存在。

- [ ] **Step 2: 确认工作区干净**

```bash
git -C "e:/999-Git/Lion-Skills" status --short
```

Expected：无输出（全部已提交）。

- [ ] **Step 3:（可选）部署一个 skill 试用**

软链或拷贝 `skills/commit-message` 到 `~/.claude/skills/`，开新会话验证 Claude 能在"帮我写 commit message"时触发它。
