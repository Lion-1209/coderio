# coderio 项目架构说明

## 1. 项目定位
coderio 是一个**技能驱动的编程 agent**。它把“该怎么做”沉淀为 Lion-Skills 工作流，把“必须这么做”做成系统级结构约束，再给模型配备真正可用的工具，从而在单进程内完成从需求理解到代码提交的端到端编码能力。

## 2. 技术栈
- 语言：Python 3.11+
- 模型接入：langchain / langgraph / langchain-openai / langchain-anthropic
- 终端 UI：Rich + Textual
- CLI：Typer
- 配置：TOML
- 存储：jsonl 追加式会话文件
- 测试：pytest

## 3. 目录结构
```
src/coderio/
├── cli/            # CLI 入口、REPL、TUI、slash 命令、凭证与 onboarding
├── agent/          # ReAct 循环、harness 状态控制、提示词构建、流式协议
├── crew/           # 6-agent LangGraph 流水线
├── tools/          # 12 个工具 + 权限门 + langchain 适配
├── skills/         # SkillStore 三层加载 + Lion-Skills
├── config/         # 三层 TOML 配置合并
├── session/        # jsonl 会话存储 + resume
└── llm/            # 模型工厂
```

## 4. 分层架构
```
CLI 层 (cli/)
  │
Agent 层 (agent/)         ReAct 循环 + harness
  │
编排层 (crew/)            6-agent 流水线（可选）
  │
能力层                    tools / skills / llm / session / config
```

依赖**严格单向向下**。crew 复用 agent 循环时显式关闭 harness，不反向依赖 CLI。

## 5. 核心流程：单次用户输入的典型路径
1. 用户在 TUI/REPL 输入一条消息。
2. 运行前先做意图分类：CODE / QA / ANALYZE。
3. CODE 模式进入 `run_agent`：
   - 按需激活对应 skill
   - 构建 system prompt
   - 进入 `_execute_turn` ReAct 循环
4. 每轮模型可能调用工具；工具结果回灌给模型。
5. 模型返回最终文本时，harness 决定是否允许结束。
6. 允许结束后写回 session，UI 渲染最终回复。

## 6. 两种运行模式
| | 单 agent（REPL/TUI） | crew |
|---|---|---|
| 适用 | 日常交互、问答、小编码任务 | 大需求、完整功能开发 |
| agent 数 | 1 | 6 |
| 工具范围 | 全量工具 | 按角色物理隔离 |
| harness | 开启 | 关闭，使用自有 verify→fix 循环 |

crew 流程：clarify → spec → task → execute → verify → commit，中间包含 HITL 暂停点与失败回退循环。

## 7. Harness 四道门
- PlanGate：无 todo 时写代码，软提醒。
- VerifyGate：写过文件但未运行验证，强制续跑；最多 2 次后放行并告警。
- CompletionGate：存在未完成 todo 时不允许直接结束。
- GroundingGate：文本显式引用代码位置，但本轮未读取该文件，强制要求先读源码。

核心思想：**基于工具调用 ground truth 决策，不信任模型自述。**

## 8. 工具层
共 12 个工具，覆盖读、写、执行、检索、计划、外部信息、记忆。所有工具统一为 `name + description + args_schema + run()` 接口，经 `to_langchain_tool` 适配后交给模型调用。工具错误不回传异常打断循环，而是变为结构化结果让模型自我修正。

## 9. Skill 层
- 三层加载：bundled < user < project
- 元数据缓存、body 懒加载
- 阶段触发映射：用户输入里的阶段信号会自动预激活对应 skill
- 提示词中只按描述分组展示，full playbook 按需加载

## 10. 会话与配置
- 会话以 jsonl 追加写入，支持 resume 与会话列表。
- 配置按 defaults < user < project < env 合并，关键字段包括 model、tools、skills、session、cli。

## 11. 关键设计原则
1. skill 是手册，harness 是纪律，工具是手。
2. 硬约束靠 ground truth，软路由靠提示词。
3. 工具错误是信号，不是致命错误。
4. 物理隔离优于提示词约束。
5. 逐级升级，永不无限循环、永不静默放行。
