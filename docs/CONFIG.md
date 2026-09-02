# coderio 配置手册

本页是 coderio 全部配置的唯一参考：`config.toml` 全字段、`[[hooks]]` 语法、
`.mcp.json` 字段、自定义 subagent / slash 命令、凭据文件、仓库信任机制。
架构设计背景见 [coderio-architecture.md](coderio-architecture.md)。

## 配置的分层与查找

优先级从低到高（后者覆盖前者，按 key 浅合并）：

1. **内置默认值**
2. **用户层**：`~/.coderio/config.toml`
3. **项目层**：`<project>/.coderio/config.toml`（从启动目录**向上查找**项目根，
   子目录启动也能找到；不会再向上越过家目录）
4. **环境变量**：`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `Z_API_KEY`（只影响
   key 回退，不覆盖你的 provider 选择）

skills、agents、commands 同样分**用户层**（`~/.coderio/…`）与**项目层**
（`<project>/.coderio/…`）。

---

## ~/.coderio/config.toml 全字段

### [model]

| 字段 | 类型 / 默认值 | 说明 |
|---|---|---|
| `default` | str，`"glm-4.5"` | 模型名 |
| `provider` | str，`"openai_compatible"` | 协议族：`openai_compatible` / `anthropic` 等（自定义 provider 时使用） |
| `base_url` | str | API 端点。注意不同协议的路径拼接规则不同 |
| `provider_id` | str，`""` | 内置 provider 注册表的 id（如 `bigmodel_coding_plan`、`stepfun_api`、`openai`、`anthropic`、`ollama`）；设置后 base_url/协议由注册表决定 |
| `max_output_tokens` | int，`16384` | 单次回复输出上限 |
| `context_limit` | int，`0` | 该模型的上下文窗口（tokens）；0 = 未探测，回退内置默认。onboarding 会自动探测 |

### 多 profile（[[profiles]]）

named profile 是一份自包含的模型配置，`/profile` 一键切换，不用重跑 onboarding：

```toml
active_profile = "work"          # 当前生效的 profile 名

[[profiles]]
name = "work"
provider_id = "bigmodel_coding_plan"
model = "glm-5.2"
kind = "anthropic"               # openai_compatible | anthropic
base_url = ""                    # 空 = 用注册表端点
context_limit = 200000           # 0 = 未探测
```

API key 不写在 config 里，按 `provider_id` 存凭据文件（见下文）。
没有 profiles 时走 `[model]` 的单配置路径，互不影响。

### [tools]

| 字段 | 类型 / 默认值 | 说明 |
|---|---|---|
| `permission_mode` | str，`"confirm"` | `plan`（只读）/ `confirm`（逐项确认）/ `auto_edit`（写免确认）/ `full`（全放行，headless 需显式 `--dangerously-skip-permissions`） |
| `bash_shell` | str，`""` | 显式 bash 路径；空 = 自动探测。**Windows 强烈建议装 Git Bash**——cmd.exe 会弄坏单引号参数，导致命令静默失败 |
| `workspace_root` | str，`""` | shell 的工作目录；空 = 启动目录。文件路径隔离由引擎的 virtual backend 负责，此项只影响 shell 命令在哪跑 |
| `blocked_commands` | list[str]，`[]` | **追加**到内置黑名单的正则（`rm -rf /`、`mkfs` 等内置项永远生效）。如 `["git push --force", "npm publish"]` |
| `network_allowed` | bool，`true` | `false` = 离线模式（web_fetch / web_search 直接拒绝） |
| `whitelist_mode` | bool，`false` | `true` = 白名单模式：首个 token 不在允许集合里的命令进入确认流程（full 模式仍放行），不是硬阻断 |
| `allowed_commands` | list[str]，`[]` | 白名单追加（仅 `whitelist_mode = true` 时生效）。如 `["docker", "kubectl"]` |
| `sandbox_mode` | str，`"off"` | `off` / `job`（进程树资源限制 + 可靠杀死）/ `write`（Linux bubblewrap 真写隔离）。**Windows 上 `write` 目前等价于 `job`**，无文件写隔离——启动时会有显式警告 |
| `auto_allow_if_sandboxed` | bool，`false` | 沙箱开启时 execute 免确认。**Windows 上 `write` 档不隔离写盘**，此组合等于移除唯一的门——启动会警告。黑名单仍然生效 |
| `sandbox_fs` | 表（见下） | Linux bubblewrap 文件系统隔离四元组；Windows 忽略 |

### [tools.sandbox_fs]（四元组，Linux bwrap only）

工作区**永远可写**（内置，无需声明）。路径支持 `~/`、`./`、相对、绝对：

```toml
[tools.sandbox_fs]
allow_write = ["/tmp/build", "~/.cache/pip"]   # 额外可写挂载
deny_write  = [".git/hooks"]                   # 只读覆盖（后挂载赢）
deny_read   = ["secrets/"]                     # tmpfs 黑洞（存在但看不见）
allow_read  = ["secrets/public.pem"]           # 从 deny_read 里凿开只读通道
```

默认值含 `deny_write = ["~/.coderio"]`——防止沙箱里的 agent 改写自己的信任
库/凭据。显式 `deny_write = []` 可关闭该保护（自担风险）。

### [skills] / [session] / [cli]

| 段 | 字段 | 默认 | 说明 |
|---|---|---|---|
| `[skills]` | `auto_load` | `true` | 三层 skills 自动加载 |
| `[skills]` | `harness` | `true` | 四道验证门开关（`false` = agent 想说完成就说完成） |
| `[skills]` | `repo_url` | Lion-Skills | `coderio skills install` 的默认仓库 |
| `[session]` | `save_dir` | `"~/.coderio/sessions"` | 会话 jsonl 与 sqlite 检查点的存放目录 |
| `[cli]` | `theme` | `"dark"` | TUI 主题 |
| `[cli]` | `show_tool_output` | `true` | TUI 里显示工具输出摘要行 |

---

## [[hooks]]（生命周期 hooks，Claude Code 兼容契约）

```toml
[[hooks]]
event = "PreToolUse"            # 见下表
matcher = "write_file|edit_file" # 正则匹配工具名；"" = 匹配全部（仅工具类事件用）
command = "python .hooks/protect.py"
timeout = 30                     # 秒；超时按失败处理（fail-open）
```

| 事件 | 触发时机 | stdin 输入 |
|---|---|---|
| `SessionStart` | 会话首轮开始 | `{"source", "model"}`；stdout 原文注入该轮 user 消息 |
| `UserPromptSubmit` | 用户消息入库前 | `{"prompt"}`；**exit 2 = 阻断该消息**；stdout 原文注入 |
| `PreToolUse` | 每次工具调用前 | 工具名 + 参数 JSON；**exit 2 = 拒绝该调用** |
| `PostToolUse` | 工具调用后 | 工具名 + 结果摘要 |
| `Stop` | agent 回合结束 | `{"last_assistant_message"}`；仅通知，不能延长回合 |

其余约定：

- 所有 hook 失败一律 **fail-open**（只有显式 exit 2 阻断）
- 仅 `SessionStart` / `UserPromptSubmit` 消费 stdout：**stdout 原文整段作为上下文
  追加到模型输入**——不做 JSON 解析，直接 echo 你想注入的文本即可（与 Claude Code
  的 prompt 类 hooks 行为一致）。超过 10,000 字符的输出会被整体丢弃并记日志；
  多个 hook 的输出按序拼接
- `PreToolUse` / `PostToolUse` / `Stop` 的 stdout 被忽略

---

## .mcp.json（MCP 外部工具）

Claude Code 兼容格式；放项目根（project scope）或 `~/.coderio/mcp.json`
（user scope）。需要 extra：`pip install "coderio[mcp]"`（未安装时启动会有提示，
MCP 工具不启用）。

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": { "SOME_VAR": "x" }
    },
    "github": {
      "type": "http",
      "url": "https://api.githubcopilot.com/mcp/"
    }
  }
}
```

- `type`：`stdio`（默认，按 `command` 推断）或 `http`；省略时按 `command`/`url`
  存在与否推断；`"remote"` 是 `http` 的兼容别名；`environment` 是 `env` 的兼容别名
- MCP 工具名带服务器前缀（如 `filesystem_read_file`），经权限门与命令审查管理
- 管理命令：`coderio mcp add|list|remove`

## .coderio/agents/*.md（自定义 subagent）

**文件名即 `task(subagent_type=...)` 的名字**。frontmatter 只有一项：

```markdown
---
description: 一句话说明（给主模型看，决定何时委派给这个 agent）
---

你的人格设定 system prompt 写在这里……
```

安全边界：自定义 agent 只定制"是谁"，能力恒为**只读栈**（hooks → PLAN 权限门
→ 命令审查），`full` 模式的调用方也无法给它升权；与内置 trusted agent 重名的
文件会被静默丢弃。

## .coderio/commands/*.md（自定义 slash 命令）

**文件名即 `/命令名`**。内置命令不可被遮蔽（`/help`、`/exit` 等内置名永远走
内置分发，同名自定义文件不生效）：

```markdown
---
description: 代码审查（/help 里显示）
---

请审查 $ARGUMENTS 涉及的改动，按严重度分级输出。
```

`$ARGUMENTS` 占位符替换用户参数；输入 `/review src/app.py` 时替换为
`src/app.py`。展开后的正文直接发给引擎，**不会**再进入内置命令分发。

## 凭据文件（~/.coderio/credentials）

TOML，按 provider_id 存 key（onboarding / `/setup` 自动写入）：

```toml
[bigmodel_coding_plan]
key = "xxxx"

[openai]
key = "sk-xxxx"
```

也接受环境变量 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `Z_API_KEY`。

## 仓库配置信任机制

coderio 会读取**项目里**的 `.coderio/config.toml`、`.mcp.json`、agents、
commands、hooks——克隆来的恶意仓库可以借这些改权限、改 API 端点、启动本地
进程。因此：

- **TUI**：首次在含仓库配置的目录启动时，列出将要加载的配置并要求 y/N 确认
- **headless（`coderio run`）**：不会交互确认——未信任的仓库配置直接报错退出；
  先交互式跑一次确认
- 信任按**文件内容**记录（配置内容变了要重新确认）；信任库存在 `~/.coderio/`
  下（默认沙箱 `deny_write` 保护它）

## 环境变量速查

| 变量 | 作用 |
|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `Z_API_KEY` | 无凭据文件时的 key 回退；存在任一即跳过 onboarding |
| `CODERIO_*` | 预留 |
