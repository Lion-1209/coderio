---
name: naming
description: 为代码标识符（变量/函数/类/文件/API/数据库字段）决定名字时。
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

### 取名前先搞清四件事

套快速规则前先回答这四个问题，否则会在错的对象上取名。前两个是**定性**（决定名字的形态），后两个是**收口**（决定用哪个词）：

1. **它是对象还是动作？** 数据/状态/实体 → 名词；行为/操作 → 动词。（这是最根本的一刀，定错了后面全错。）
2. **一个职责还是两个？** 像"转换 + 校验"这种复合，先问该不该拆成两个名字——能独立复用/测试就拆，再各自取名。别把两件事塞进一个名字（如 `parseAndValidateCsv` 不如 `parseCsv` + `validateRecords`）。
3. **信息够不够？** 用户只甩"flag 叫啥"不给语义时，先反问"它具体表示什么状态"，别凭猜取名。
4. **有歧义吗？** 同名异义/异名同义（项目里既有 `login` 又有 `auth`），先定以谁为基准——通常以领域模型/核心资源名为准，全项目统一。

问题 1 的答案同时决定了下面「快速规则」第一条该走名词还是动词那一支。

### 快速规则

- **函数/方法用动词**：`fetchUser`、`validateEmail`
- **变量/属性用名词**：`user`、`emailAddress`
- **布尔用 is/has/can/should**：`isLoggedIn`、`hasPermission`
- **避免否定**：`isEmpty` 优于 `isNotEmpty`
- **避免无意义词**：名词 `data`/`info`/`helper`/`manager`/`util`，动词 `process`/`handle`/`do`——都说不清做啥
- **少缩写**：除非领域通用（`id`、`url`、`http`，以及 `csv`、`json`、`xml` 这类格式名）；`usr`、`cfg`、`tmp` 别用
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

### 转换类动词按数据流向选

"把 A 变成 B"这类操作最容易被塞进万能的 `process`/`handle`，丢掉数据流向信息。按**输入→输出的形态变化**选最贴切的动词：

| 动词 | 数据流向 | 例子 |
|------|---------|------|
| `parse` | 文本 → 结构化对象 | `parseCsv(text)` → `Record[]` |
| `serialize` | 结构 → 文本/字节 | `serializeOrder(order)` → JSON 字符串 |
| `convert` / `transform` | 结构 → 结构（同层级换形态） | `convertMsToSeconds`、`transformUserToDto` |
| `format` | 结构 → 展示用文本 | `formatDate(date)` → `"2026-06-22"` |
| `map` / `reduce` | 集合 → 集合/标量 | `mapToIds(users)`、`reduceToTotal(items)` |

选名时问自己：**读者看到动词，能不能猜出输入和输出分别是什么形态？**猜不出（如 `processData`）就换。

### API 命名

- 资源用复数名词：`/users`、`/orders`
- 嵌套表达从属：`/users/{id}/orders/{oid}/items`
- 代码内字段 case 跟语言：JS camelCase、Python/DB snake_case。但 API 响应字段是跨语言契约——case 由 API 规范定（通常跟主力前端或团队约定），**不跟服务端语言**（例：后端 Python 用 snake_case，对外 JSON 仍可 camelCase）
- 动作用 HTTP 方法表达，别塞进 URL：`DELETE /users/123` 而非 `/users/123/delete`

## 常见错误

| 问题 | 修法 |
|------|------|
| 缩写成谜（`usrCfg`） | 写全，除非领域通用缩写 |
| 同概念多名（user/account/member） | 全项目统一一个 |
| 匈牙利记法（`strName`、`intAge`） | 去掉类型前缀 |
| 过度抽象（Manager/Handler/Util） | 用具体名词 |
| 否定布尔（isNotValid） | 改肯定（isValid） |
