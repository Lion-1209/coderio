# Lion-Skills 设计文档

- **日期**：2026-06-16
- **状态**：设计中（待评审）
- **范围**：MVP 第一轮
- **仓库**：`e:\999-Git\Lion-Skills`（全新空目录，从零自建）

> **文档定位**：这是 MVP 的**初始设计快照**，记录决策依据与原始意图。5 个 skill 已实现并通过轻量验证，实现过程中对正文做过多次迭代（如 description 触发覆盖、决策模型细化、常见错误补充），**实现以 `skills/*/SKILL.md` 为准**——若本 spec 的某条要点与现行 SKILL.md 不一致，以 SKILL.md 为真相，并视情况回填本 spec。spec 与实现的差异审计见 §9。

---

## 1. 背景与目标

用户要在 `Lion-Skills` 全新空目录中，从零自建一套**通用开发工作流**的个人 skills 套件。定位类似 superpowers，但**完全独立、更轻量、更贴合个人中文习惯**——不依赖本地已装的 superpowers 5.1.0，独立维护。

**目标**：
1. 交付一批高频、可立即使用、且经过验证的业务 skill。
2. 建立一套可持续的"造 skill 流水线"（模板 + 元 skill + 验证方法），让后续添加新 skill 有章可循，保证质量不烂尾。

---

## 2. 研究基础

正式设计前，研究了以下高 star 生态的设计实践：

| 来源 | star | 提炼的价值 |
|------|------|-----------|
| [anthropics/skills](https://github.com/anthropics/skills)（官方） | ~86.5k | 标准目录布局、skill-creator 的"draft→test→review→iterate"创作与评估流程 |
| [obra/superpowers](https://github.com/obra/superpowers)（社区最大，本地 5.1.0） | ~73k | writing-skills 的 TDD 式方法论、CSO（Claude Search Optimization） |
| [agentskills.io/specification](https://agentskills.io/specification) | — | SKILL.md frontmatter 字段与上限规范 |

提炼出三条黄金原则，作为本仓库**所有** skill 的设计准则：

1. **Progressive Disclosure（渐进式加载）**：metadata（`name`+`description`）始终在每次对话的 context；正文触发时才加载（建议 < 500 行）；大段参考按需读取（脚本可不加载就执行）。
2. **description 只写"何时触发"，绝不写工作流**：官方测试发现，若 description 里总结了流程，Claude 会偷懒只读 description、跳过正文。正确做法是只列触发条件与症状。
3. **轻量 TDD 验证**：每个 skill 配 2–3 个真实测试 prompt，用 subagent 带着它跑，人工评审输出对不对（不做完整 baseline 量化 benchmark，那是后续可选项）。

---

## 3. 范围（MVP）

用户需求覆盖代码交付 / 代码理解 / 质量与设计 / 工程流程 四大类。但"完全独立自建 + 4 类全做"对单轮太大、易烂尾。MVP 聚焦最高频的三类，每类挑 1 个核心 skill，加 1 个元 skill：

| Skill | 类别 | 解决的痛点 |
|-------|------|-----------|
| `commit-message` | 代码交付 | 把一团 diff 提炼成规范、可追溯的提交信息 |
| `onboarding-unknown-codebase` | 代码理解 | 用分层流程把陌生代码库变成结构化"项目地图" |
| `naming` | 质量与设计 | 命名决策（变量/函数/文件/API） |
| `error-handling` | 质量与设计 | 错误处理与日志设计 |
| `lion-writing-skills` | 元 skill（造 skill 用，不计入四大业务类别） | 教以后怎么按本规范加新 skill |

**不在本轮范围**（下一轮扩展）：工程流程类的 `task-breakdown`（任务拆解估算）、`writing-readme`、技术选型等。

**验证策略**：轻量 TDD（见 §7）。
**语言**：中文为主，工具名/技术术语保留英文（如 `git`、`Conventional Commits`、`stack trace`）。

---

## 4. 仓库结构

参考 anthropics/skills 的标准布局：

```
Lion-Skills/
├── README.md                      # 这是什么、有哪些 skill、怎么部署
├── docs/
│   └── specs/                     # 设计文档
│       └── 2026-06-16-lion-skills-design.md   ← 本文件
├── template/
│   └── SKILL.md                   # 新建 skill 的模板，复制即用
└── skills/
    ├── commit-message/
    │   └── SKILL.md
    ├── onboarding-unknown-codebase/
    │   └── SKILL.md
    ├── naming/
    │   └── SKILL.md
    ├── error-handling/
    │   └── SKILL.md
    └── lion-writing-skills/       # 元 skill
        └── SKILL.md
```

**部署方式**（实现时定）：Lion-Skills 是源码仓库；使用时把所需 `skills/<name>/` 软链或拷贝到 `~/.claude/skills/`（用户级）或项目级 `.claude/skills/`。是否做成 plugin（加 `.claude-plugin/plugin.json`）后续再议。

---

## 5. SKILL.md 模板规范（Lion-Skills 的"宪法"）

### 5.1 Frontmatter（仅这两个必填，上限 1024 字符）

```yaml
---
name: <动词前置、连字符、全小写，如 commit-message>
description: <只写"何时触发"，第三人称，中文，覆盖用户可能说的各种说法；不写工作流>
---
```

### 5.2 正文骨架

```markdown
# <标题>

## 概述        # 一两句：这是什么、核心原则
## 何时使用    # 症状/场景列表 + 何时不该用
## 核心内容    # 步骤 / 决策模型 / 模式+例子（按 skill 类型）
## 常见错误    # 什么会出错 + 怎么修
## 参考（可选）# 大段参考才拆出去，否则全内联
```

### 5.3 写作约定

- **description 只写触发条件，绝不写工作流**（否则 Claude 偷懒只读 description）。
- description 要**覆盖关键词**：把用户可能说的中文说法 + 常见英文术语都埋进去，防漏触发。
- **解释 why，少用全大写 MUST**——讲道理比下命令管用（LLM 有 theory of mind）。
- **一个优秀例子 > 多语言版本**（擅长移植，一份就够）。
- 正文 **< 500 行**；超了拆 `references/`。
- 语言：中文为主，工具名/术语保留英文。

---

## 6. 各 Skill 设计

### 6.1 `commit-message`（代码交付类）

```yaml
name: commit-message
description: 当用户需要为 Git 改动编写或优化提交信息（commit message）时使用。典型信号：用户说"帮我写 commit message""这次改动怎么提交""提交说明怎么写""git commit 写啥"、刚看完 diff 准备提交、一次改了多处不知怎么概括、或现有提交信息太笼统（如"update""改了下"）需要改成规范写法。涉及关键词：commit message、提交信息、提交说明、git commit、Conventional Commits、feat/fix、commitizen、提交规范、changelog。
```

**正文要点**
- 概述：把一团 diff 提炼成一条（或多条）规范、可追溯的提交信息。
- 核心内容：
  - 采用 Conventional Commits：`type(scope): subject`（feat/fix/docs/refactor/test/chore/perf）
  - 从 diff 提炼三步：①归类改动类型 → ②定影响范围 scope → ③写一行简明 subject（祈使句、≤50 字符、说"做了什么"不说"怎么做的"）
  - 多处改动：能合并就一条 + body 列要点；不相关就拆成多条
  - body 写法：写**为什么**改、**怎么**影响的（动机比代码细节重要）
  - 语言约定：subject 默认英文（生态惯例），body 可中文；用户偏好优先
- 常见错误：太笼统（"update"/"改了点"）、一个 commit 塞多个无关改动、subject 写成"怎么做"而非"做了什么"、只说现象不说动机

**验证用例**
1. "我把一个 React 组件的状态从 useState 换成了 useReducer，还修了个内存泄漏。写个 commit message。"（测：多改动归类 + 拆分判断）
2. "上一条提交是 'update'，根据当前 diff 帮我重写成规范的。"（测：重写场景）
3. "改了登录接口、数据库迁移脚本、README 三个地方，怎么提交？"（测：多文件是否该拆）

---

### 6.2 `onboarding-unknown-codebase`（代码理解类）

```yaml
name: onboarding-unknown-codebase
description: 当用户需要快速上手或理解一个陌生的代码库/项目时使用。典型信号：用户说"帮我看懂这个项目""我刚接手 xx""这个代码库是干啥的""怎么快速熟悉这个项目""这个项目的结构/架构""入口在哪"、clone 了新 repo 不知从哪读起、要给同事讲清一个项目、接手离职同事留下的代码。涉及关键词：上手、熟悉、理解、读代码、看懂项目、项目架构、入口、目录结构、代码导航、onboarding、codebase。
```

> 与 Claude Code 自带 Explore agent 区分：本 skill 是"系统地把陌生代码库**梳理成结构化全貌给用户**"，而非单纯搜文件。

**正文要点**
- 概述：用一套分层流程把陌生代码库变成一张"项目地图"，而不是一头扎进某个文件。
- 核心内容（三遍法）：
  - 第一遍·宏观：读 README、`package.json`/`go.mod`/依赖清单、CI 配置 → 搞清"这是什么、技术栈、怎么跑起来"
  - 第二遍·结构：看目录布局和分层（前后端/模块划分/入口位置），画依赖关系
  - 第三遍·主线：从入口（main/index/app）顺一条核心调用链读到底，不求全
- 产出约定：结构化地图 = `是什么 / 技术栈 / 目录结构 / 核心流程 / 关键文件 / 怎么跑起来`
- 常见错误：一上来深挖某个文件细节、不看配置就乱猜、只口头散讲不产出地图、试图读完所有文件

**验证用例**（需指定一个真实代码库作输入）
1. "我刚 clone 了 vite，帮我看懂它是干啥的、结构怎样、入口在哪。"（测：标准上手，产出地图）
2. "同事离职留下这个项目，我不知道怎么跑、也看不懂整体逻辑。"（测：接手场景）
3. "讲讲这个代码库从 HTTP 请求到响应经过哪些层。"（测：主线追踪）

---

### 6.3 `naming`（质量与设计类）

```yaml
name: naming
description: 当用户需要为代码标识符决定名字时使用——变量、函数、方法、类、文件、模块、API、数据库字段、配置项等。典型信号：用户说"这个变量/函数叫啥名好""这个名字合适吗""帮我起个名""命名规范""这个 API 怎么命名"、写代码时卡在取名、觉得现有命名不准或误导想改、想统一一个模块的命名风格。涉及关键词：命名、取名、起名、变量名、函数名、类名、文件名、API 命名、identifier、naming convention、命名约定、命名风格。
```

**正文要点**
- 概述：名字要回答"它是什么/它做什么"，而不是"它在哪/什么类型"。
- 核心内容（决策模型 + 快速规则）：
  - 决策四问：①它代表什么 ②它做什么 ③谁会用 ④有没有歧义
  - 规则：函数用动词、变量/属性用名词、布尔用 `is/has/can/should`、避免否定（`isNotEmpty`→`isEmpty`）、避免无意义词（`data/info/helper/manager`）、少缩写（除领域通用：`id/url/http`）、范围越窄越具体
  - before/after 对比表（核心价值）
- 常见错误：缩写成谜、命名不一致（同概念多名字）、匈牙利记法（`strName`）、过度抽象（`Manager/Handler/Util`）

**验证用例**
1. "函数叫 `processData`，其实是把 CSV 转 JSON 并校验。起个更好的名。"（测：模糊→具体）
2. "变量叫 `flag`，表示用户是否登录，叫啥好？"（测：无意义→有意义）
3. "设计一个 REST API，资源是'用户订单里的商品'，URL 和字段怎么命名？"（测：API 命名）

---

### 6.4 `error-handling`（质量与设计类）

```yaml
name: error-handling
description: 当用户需要设计或改进错误处理与日志时使用。典型信号：用户说"这里怎么处理错误""异常怎么处理""要不要 try/catch""日志怎么打""错误信息怎么写""错误码怎么设计""这个错误该抛还是该吞"、遇到边界情况/失败场景/空值/超时/重试不知怎么处理、现有代码错误处理粗糙（裸 catch、吞异常、日志没用）想改进。涉及关键词：错误处理、异常处理、try/catch、catch、抛异常、错误信息、错误码、日志、logging、重试、retry、边界情况、防御性编程、defensive。
```

**正文要点**
- 概述：错误要**可观测**（知道发生了什么）、**可区分**（能区分类型）、**不吞**（不静默 catch）。
- 核心内容：
  - 三态决策：**抛**（底层/不可恢复，往上传）／**处理**（能恢复/降级）／**重试**（瞬时故障，指数退避）
  - 日志：记上下文（输入、状态、错误对象），**不记敏感信息**（密码/token），分级（debug/info/warn/error）
  - 错误信息面向**开发者**：写可能原因 + 怎么排查，不是给最终用户看的
  - 模式：自定义错误类型/错误码、fail fast、在系统边界统一兜底
- 常见错误：裸 `catch(e){}`、catch 了又原样 throw、日志只记 `message` 不记上下文、把异常堆栈直接吐给终端用户

**验证用例**
1. "这段 `try { await fetch(url) } catch(e) { console.log(e) }` 太糙了，怎么改进？"（测：粗糙→规范）
2. "调第三方支付 API，可能超时/限流/余额不足，分别怎么处理？"（测：分类处理）
3. "写用户注册：邮箱格式错/已注册/数据库连不上，这三种错误怎么设计类型和返回？"（测：错误类型设计）

---

### 6.5 `lion-writing-skills`（元 skill）

```yaml
name: lion-writing-skills
description: 当用户想在 Lion-Skills 仓库里创建新 skill、修改现有 skill、或检查 skill 是否符合本仓库规范时使用。典型信号：用户说"帮我加个新 skill""写个 skill""创建一个 skill""这个 skill 怎么改/优化""检查这个 skill 写得对不对"、想把某个重复的工作流沉淀成 skill、想给 Lion-Skills 套件加成员。涉及关键词：创建 skill、写 skill、新 skill、lion-skills、SKILL.md、skill 模板、skill 规范、验证 skill。
```

> 是 Lion-Skills 版的 writing-skills，但**轻量版**——只带本仓库模板规范 + 轻量验证步骤，不搬 superpowers 的完整 baseline benchmark。

**正文要点**
- 概述：Lion-Skills 的"造 skill 流水线"——按本仓库规范，从需求到验证可复制的 skill。
- 核心内容（五步）：
  1. 判断值不值得做：高频？跨项目？有判断空间（非纯自动化）？否则别做
  2. 复制模板：`template/SKILL.md` → 填 `name` + `description`（严格按 §5 规范）
  3. 写正文：概述/何时使用/核心内容/常见错误，遵循写作约定
  4. 轻量验证：写 2–3 个真实 prompt，subagent 带着新 skill 跑，看输出对不对
  5. 迭代：根据效果改，堵漏洞
- 附：本仓库目录约定、description 写法、写作约定（引用 §5，不重复抄）
- 常见错误：description 写了工作流（→ Claude 偷懒）、正文超 500 行没拆 `references/`、堆 MUST 不讲 why、一个例子写多语言

**验证用例**
1. "我想加个 skill，每次写完代码帮我自查边界情况。帮我创建。"（测：需求→skill）
2. "我的 description 是'用于代码审查，会检查风格/安全/性能'，帮我看看写得对不对。"（测：审规范，尤其"别写工作流"）
3. "我写了这个 SKILL.md，帮我验证它真好用。"（测：验证流程）

---

## 7. 验证策略

每个 skill 落地后，执行**轻量 TDD 验证**（不做完整 baseline benchmark，那是后续可选项）：

1. 把 §6 中该 skill 的 2–3 个验证用例写成 subagent 任务。
2. subagent 带着（或指向）该 skill 执行任务，保存输出。
3. 人工评审输出：是否解决了用户问题？有没有偏离 skill 的设计意图？
4. 发现问题 → 修改 skill 正文 → 重跑该用例，直到满意。

**通过标准**（每个 skill）：全部验证用例的输出，人工判断"可用且符合设计意图"。

> 完整 baseline（with-skill vs 无-skill 量化对比、description 触发率优化）列为后续可选增强，不在本轮 MVP 强制范围。

---

## 8. 后续步骤

1. 本 spec 经用户评审确认。
2. 进入实现阶段（writing-plans）：拆分实现任务，逐个产出 5 个 SKILL.md + 模板 + README。
3. 每个 skill 写完后执行 §7 验证。
4. 验证通过后，下一轮扩展工程流程类 skill（`task-breakdown`、`writing-readme` 等）。

---

## 9. spec 与实现差异审计（2026-06-22 复盘）

实现与验证过程中，多处正文迭代超越了本 spec 的原始要点。下表列出**有意义的差异**（纯措辞润色不计）。后续若回填 spec，按此表逐条处理。

| Skill | spec 原文要点 | 实际 SKILL.md | 差异性质 |
|-------|--------------|--------------|---------|
| `commit-message` | §6.1「subject 默认英文（生态惯例）」 | 正文改为「跟随仓库现有风格（多数中文团队和本仓库用中文 subject）」 | **改正**：本仓库自身提交即中文 subject，原 spec 与实践矛盾 |
| `commit-message` | 未提及「主线 + 顺带小修复」的灰色地带 | 正文新增一节，给「能不能一句话概括全部改动」的判断尺子 | 增量：补真实高频困惑 |
| `naming` | §6.3 只有「决策四问」 | 正文新增「先搞清你要命名的是什么」前置节（对象 vs 动作、同名异义、信息不足先反问） | 增量：解决「在错的对象上取名」 |
| `onboarding-unknown-codebase` | 三遍法假设已拿到目标代码库 | 正文新增「先确认对象（前置）」，明确「不默认当前目录、不凭空臆造项目」 | 增量：堵 AI 上手陌生库的跑题模式 |
| `error-handling` | §6.4 重试只说「指数退避别死循环」 | 正文扩展幂等前提、读超时陷阱、HTTP 429/503 遵守 `Retry-After` | 增量：补真坑 |
| `error-handling` | 未区分业务错误与系统错误的日志级别 | 正文新增「业务错误打 info/warn，不打 error 污染告警」 | 增量：补真实困惑 |
| `lion-writing-skills` | §6.5 第 4 步只说「写 2–3 个真实 prompt 跑」 | 正文扩展「真实 prompt 标准」「subagent 范式」「通过标准」「跑偏长这样」「evals.json 时序」 | 增量：真跑过才写得出的方法论 |

**处理策略**：采用「声明真相在 SKILL.md」而非逐条回填——保留设计演进史，回填成本高且易再次滞后。本节即回填时的清单。
