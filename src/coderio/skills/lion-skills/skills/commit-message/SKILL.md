---
name: commit-message
description: Git 改动需编写规范提交信息时。
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
<type>(<scope>)<!>: <subject>

<body 可选：为什么改>

<footer 可选：BREAKING CHANGE: ... / Closes #123>
```

**破坏性变更**（不向后兼容的改动）两种写法，二选一或并用：
- **`!` 标记**：紧贴 type/scope 之后、冒号之前，如 `feat(api)!: 改用 token 鉴权替代 cookie`——一眼标红，提醒 reviewer 注意。
- **脚注 `BREAKING CHANGE:`**：放 footer，写清破坏了什么、怎么迁移，如 `BREAKING CHANGE: /api/login 改为返回 token，前端需改用 Authorization 头`——给受影响方具体的迁移指引。

破坏性变更必须二选一明确标注，别藏在普通提交里让下游踩坑。

**type 选一个**：`feat`（新功能）、`fix`（修 bug）、`docs`（文档）、`refactor`（重构不改行为）、`test`（测试）、`chore`（构建/杂务）、`perf`（性能）。带可追溯业务变更的迁移脚本（如新增用户表）可算 `feat`，纯基础设施改动算 `chore`。

### 从 diff 提炼三步

1. **归类**：主要是加功能（feat）、修 bug（fix）、还是重构（refactor）？
2. **定 scope**：影响哪个模块/文件？（如 `auth`、`api`、`ui`）可省略。
3. **写 subject**：一行，祈使句，说"做了什么"不说"怎么做的"，≤50 字符。

### 多处改动怎么办

- **相关改动** → 一条提交，body 列要点
- **不相关改动** → 拆成多条（用 `git add -p` 分块暂存）

判断"相不相关"：这些改动服务于同一个目的吗？是 → 一条；否 → 拆。

**灰色地带——"主线 + 顺带小修复"**：重构或加功能时顺手修了个同处的小 bug（例 1 就是这种），**可以合并**：主线作 subject，bug 修复在 body 一行带过。但若两件事跨文件、跨关注点、或各自都够大，就该拆。一个简单的尺子：**能不能用一句话（一个 subject）概括全部改动？能 → 合；不能 → 拆。**

### body 写什么

写**为什么**和**影响**，不写代码细节（代码自己会说话）：动机/背景、副作用/破坏性变更的迁移说明、关联 issue/PR（如 `Closes #123`）。破坏性变更的标注方式见上面「格式」节。subject 够清楚的小改动可以只写 subject，别硬凑 body。

### 语言

subject 跟随仓库现有风格（多数中文团队和本仓库用中文 subject；Conventional Commits 生态也常见英文）——没有仓库上下文时跟随团队语言；body 语言同 subject；用户明确偏好优先。

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
