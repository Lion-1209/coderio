# Lion-Skills Plugin 分发 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Lion-Skills 做成可一键安装的 Claude Code plugin（`/plugin marketplace add` → `/plugin install`），方便更多使用者测试。

**Architecture:** 不动现有 `skills/` 结构，只在仓库根加 `.claude-plugin/`（marketplace.json + plugin.json）+ MIT LICENSE + 更新 README 安装说明。自托管 marketplace，整个仓库即一个 plugin。

**Tech Stack:** JSON / Markdown。验证 = JSON 语法校验 + 最终端到端 plugin 安装。

**Spec:** `docs/specs/2026-06-17-lion-skills-plugin-distribution-design.md`

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `.claude-plugin/marketplace.json` | 新建 | 自托管 marketplace 声明，含一个 plugin（source=./） |
| `.claude-plugin/plugin.json` | 新建 | plugin manifest（name/version/author/license 等） |
| `LICENSE` | 新建 | MIT 全文 |
| `README.md` | 修改 | 「怎么部署」节 → 「怎么安装」节（plugin 为主，clone 为辅） |

---

## Task 1: plugin 配置（marketplace.json + plugin.json）

**Files:**
- Create: `.claude-plugin/marketplace.json`
- Create: `.claude-plugin/plugin.json`

- [ ] **Step 1: 写 marketplace.json**

创建 `.claude-plugin/marketplace.json`：

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

- [ ] **Step 2: 写 plugin.json**

创建 `.claude-plugin/plugin.json`：

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

- [ ] **Step 3: 校验两个 JSON 语法合法**

Run:
```bash
python -m json.tool "e:/999-Git/Lion-Skills/.claude-plugin/marketplace.json" > /dev/null && echo "marketplace.json OK"
python -m json.tool "e:/999-Git/Lion-Skills/.claude-plugin/plugin.json" > /dev/null && echo "plugin.json OK"
```
Expected：两行 `... OK`，无报错。（若无 python，用 `node -e "JSON.parse(require('fs').readFileSync('文件路径','utf8')); console.log('OK')"`。）

- [ ] **Step 4: Commit**

```bash
git -C "e:/999-Git/Lion-Skills" add .claude-plugin
git -C "e:/999-Git/Lion-Skills" commit -m "feat(plugin): 添加 marketplace 与 plugin manifest 配置"```

---

## Task 2: MIT LICENSE

**Files:**
- Create: `LICENSE`

- [ ] **Step 1: 写 LICENSE**

创建 `LICENSE`：

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

- [ ] **Step 2: Commit**

```bash
git -C "e:/999-Git/Lion-Skills" add LICENSE
git -C "e:/999-Git/Lion-Skills" commit -m "docs: 添加 MIT LICENSE"```

---

## Task 3: README 安装说明

**Files:**
- Modify: `README.md`（替换「## 怎么部署」整节为「## 怎么安装」）

- [ ] **Step 1: 替换「怎么部署」节**

把 `README.md` 里从 `## 怎么部署` 到下一个 `## ` 之间的整节，替换为：

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

- [ ] **Step 2: 确认改动范围正确**

Run: `git -C "e:/999-Git/Lion-Skills" diff README.md`
Expected：只动了「怎么部署」→「怎么安装」这一节，其余章节（标题、这是什么、包含的 skill、怎么加新 skill、设计依据）未变。

- [ ] **Step 3: Commit**

```bash
git -C "e:/999-Git/Lion-Skills" add README.md
git -C "e:/999-Git/Lion-Skills" commit -m "docs: README 改为 plugin 安装说明（一键安装为主）"```

---

## Task 4: 推送 + 清理拷贝副本 + 端到端验证

**Files:** 无（验证性任务）

- [ ] **Step 1: 推送到远端**

Run:
```bash
git -C "e:/999-Git/Lion-Skills" push origin main
```
Expected：推送成功，`status -sb` 显示 `main...origin/main`（无 ahead/behind）。

- [ ] **Step 2: 清理之前拷贝部署的副本**

之前手动拷贝到 `~/.claude/skills/` 的 5 个 skill 会和 plugin 版并存（造成同一句话触发两个 skill）。清理它们，改为纯 plugin 方式：

Run:
```bash
rm -rf ~/.claude/skills/commit-message ~/.claude/skills/error-handling ~/.claude/skills/naming ~/.claude/skills/onboarding-unknown-codebase ~/.claude/skills/lion-writing-skills
```
Expected：命令无报错；`ls ~/.claude/skills/ | grep -E "commit-message|error-handling|naming|onboarding|lion-writing"` 无输出（已清理）。

- [ ] **Step 3: 端到端验证（用户在 Claude Code 里操作）**

这一步需要用户在 Claude Code 会话里执行（无法用脚本替代）：

1. `/plugin marketplace add Lion-1209/Lion-Skills` —— 期望：提示 marketplace 添加成功
2. `/plugin install lion-skills@lion-skills` —— 期望：plugin 安装成功
3. 新开会话或 `/reload-plugins`
4. 用自然语言试触发一个 skill，如「这段 `try{fetch(url)}catch(e){console.log(e)}` 怎么改进」—— 期望：触发 `lion-skills:error-handling`，产出符合 skill 设计

若任一步失败，记录现象，按 plugin.json/marketplace.json 字段或安装命令排查（参考 spec §2 确认的机制）。
