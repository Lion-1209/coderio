# Lion-Skills Plugin 分发设计

- **日期**：2026-06-17
- **状态**：设计中（待评审）
- **范围**：把 Lion-Skills 做成可一键安装的 Claude Code plugin
- **前置**：5 个 skill 已实现并验证（见 `2026-06-16-lion-skills-design.md` 及后续 refactor 提交）

> **文档定位**：本 spec 已完整实现——`.claude-plugin/marketplace.json`、`.claude-plugin/plugin.json`、`LICENSE`、README「怎么安装」节均已落地，与设计无偏差。

---

## 1. 背景与目标

Lion-Skills 目前是个人本地工具（手动 clone + symlink 部署，存在"源仓库改了、部署副本不同步"的运维坑）。目标是做成 Claude Code plugin，让更多使用者能用一条命令安装并测试。

Lion-Skills 的 `skills/` 已在仓库根，结构天然兼容 plugin——只需加配置文件，无需动现有结构。

---

## 2. 研究基础

- **活样本**：obra/superpowers 5.1.0（本地已装），用 `.claude-plugin/marketplace.json` + `plugin.json` 做成自托管 marketplace。
- **机制确认**（经 claude-code-guide 查 Claude Code 官方文档）：
  - 安装命令：`/plugin marketplace add Lion-1209/Lion-Skills` → `/plugin install lion-skills@lion-skills`
  - marketplace name 与 plugin name **可同名**（命令 `plugin@marketplace` 不歧义）
  - 安装后 `skills/<name>/SKILL.md` **自动注册可用**，被 namespace 成 `lion-skills:<skill>`；靠 description 自动触发不受影响
  - 必填：`marketplace.json` 需 `name`+`owner.name`+`plugins`；`plugin.json` 需 `name`

---

## 3. 设计：4 个文件

### 3.1 `.claude-plugin/marketplace.json`

```json
{
  "name": "lion-skills",
  "description": "Lion-Skills：个人自建的通用开发工作流 skills 套件",
  "owner": {
    "name": "Lion"
  },
  "plugins": [
    {
      "name": "lion-skills",
      "description": "通用开发工作流 skills：commit-message、代码库上手、命名、错误处理、造 skill 的元方法",
      "source": "./"
    }
  ]
}
```

### 3.2 `.claude-plugin/plugin.json`

```json
{
  "name": "lion-skills",
  "description": "通用开发工作流 skills 套件：commit-message、onboarding-unknown-codebase、naming、error-handling、lion-writing-skills",
  "version": "0.1.0",
  "author": { "name": "Lion" },
  "homepage": "https://github.com/Lion-1209/Lion-Skills",
  "repository": "https://github.com/Lion-1209/Lion-Skills",
  "license": "MIT",
  "keywords": ["skills", "commit-message", "onboarding", "naming", "error-handling", "workflow", "best-practices"]
}
```

### 3.3 `LICENSE`（MIT，全文）

```
MIT License

Copyright (c) 2026 Lion

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 3.4 `README.md` 更新

把「## 怎么部署」整节替换为「## 怎么安装」，主推 plugin 安装，clone+symlink 降为备选：

````markdown
## 怎么安装

### 推荐：作为 Claude Code plugin 安装

在 Claude Code 里两步：

```
/plugin marketplace add Lion-1209/Lion-Skills
/plugin install lion-skills@lion-skills
```

装完即用——5 个 skill 会在合适的时机自动触发。skill 标识带 plugin 前缀（如 `lion-skills:commit-message`），但日常靠自然语言触发即可，无需关心前缀。

### 备选：手动 clone + 链接

不想用 plugin，clone 后把需要的 skill 软链到 `~/.claude/skills/`：

Linux/macOS：
```bash
git clone https://github.com/Lion-1209/Lion-Skills.git
ln -s /path/to/Lion-Skills/skills/commit-message ~/.claude/skills/commit-message
```

Windows（PowerShell，符号链接需管理员权限或开发者模式，或直接拷贝）：
```powershell
git clone https://github.com/Lion-1209/Lion-Skills.git
Copy-Item -Recurse E:\999-Git\Lion-Skills\skills\commit-message $HOME\.claude\skills\
```
````

> 其余 README 章节（标题、这是什么、包含的 skill、怎么加新 skill、设计依据）保持不变。

---

## 4. 默认决策（已与用户确认）

- marketplace name 和 plugin name **都叫 `lion-skills`**
- 版本 `0.1.0`（pre-1.0，表明早期演进中）
- author 只写 `Lion`，**不公开 email**（隐私）
- LICENSE 版权人 `Lion`、年份 `2026`

---

## 5. 不在范围

- CHANGELOG、版本管理约定、CI 校验 frontmatter（属方案 B，后续按需加）
- 发布到官方/第三方 marketplace（自托管已足够"方便测试"）

---

## 6. 验证

实现并推送后，用一个干净的 Claude Code 会话执行安装命令，确认：marketplace 能 add、plugin 能 install、5 个 skill 被识别（`/lion-skills:*` 可见）且能靠自然语言触发。

> 注意：之前手动拷贝到 `~/.claude/skills/` 的副本（`commit-message`、`error-handling`、`naming`、`onboarding-unknown-codebase`、`lion-writing-skills`）会和 plugin 版（`lion-skills:*`）并存，可能造成同一句话触发两个 skill。测试 plugin 安装前，建议先清理这些拷贝副本，单独验证 plugin 版的行为。
