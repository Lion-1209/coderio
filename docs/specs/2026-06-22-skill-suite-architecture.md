# Lion-Skills 套件架构

- **日期**：2026-06-22（2026-06-26 更新：新增 verify-and-fix；2026-06-27 更新：新增 testing/debugging/code-review，补全执行段）
- **状态**：已实现 + 已验证
- **范围**：记录 12 个 skill 的流水线关系、协作机制、已验证的协作证据
- **目的**：让后续读者（包括未来的 Claude）一眼看清 skill 之间如何协作，避免把每个 skill 当孤立工具用

> **文档定位**：这是套件级架构索引，不是单个 skill 的设计文档。各 skill 的内部设计见各自的 `SKILL.md`，初始设计意图见 `2026-06-16-lion-skills-design.md`。

---

## 1. 流水线：需求到交付的主链

主链由"需求 → 设计 → 拆解 → 执行 → 验证 → 提交"构成，执行段有三个 skill 协作：

```
clarifying → spec-writing → task-breakdown → [执行段] → verify-and-fix → commit-message
 (澄清)        (设计)          (拆任务)      testing/      (验证完成)      (提交)
                                              debugging/     ↑
                                              code-review    修复接力:debugging定位→verify-and-fix修
```

| 环节 | skill | 产出 | 何时进入下一环 |
|------|-------|------|---------------|
| 澄清 | `clarifying-questions` | 双方对齐的需求理解（含已明决策 + 默认假设 + 未答未知） | 核心方向已明、未答未知不再阻塞方向 |
| 设计 | `spec-writing` | 可评审的 spec（决策 + 范围 + 验收标准 + TBD） | spec 定稿（阻塞澄清已答、关键 spike 有结论） |
| 拆解 | `task-breakdown` | 可执行任务清单（垂直切片 + 完成定义 + 研究/执行分离） | 任务可分配/可执行 |
| 执行 | `testing` / `debugging` / `code-review` | 经测试的代码、定位的根因、审查过的改动 | 代码写完且自测过 |
| 验证 | `verify-and-fix` | 经验证的完成（实际跑过的证据、修病因而非症状） | 每个任务的"完成定义"被实际验证通过 |

**执行段三个 skill 的分工**（都在 task-breakdown 下游、verify-and-fix 周围）：

- `testing`：把 task 的"完成定义"落地成**可重复运行的测试**（测行为不测实现、mock 纪律、金字塔）。
- `debugging`：bug 出现时**定位根因**（科学方法、复现、二分、读堆栈）——定位完交给 verify-and-fix 修。
- `code-review`：提交前**审查**（正确性优先、严重度分层、自我 review）——发现问题需要修的进入 verify-and-fix。

**关键衔接契约**（上游产出必须满足的、下游消费的）：

- clarifying → spec：clarifying 的"硬问题"被 spec 继承为阻塞澄清项；clarifying 的"默认假设"被 spec 作为候选决策；clarifying 的"未答未知"被 spec 显式列为 TBD。
- spec → task：spec 的"验收标准"被 task 一一映射到各切片的"完成定义"；spec 的 TBD 对应到 task 的 spike；spec 的范围决定 task 拆什么不拆什么。
- task → testing：task 的"完成定义"是 testing 的目标——testing 把它落地成可重复运行的测试。
- testing → verify-and-fix：testing 产出的测试是 verify-and-fix 的验证工具——verify-and-fix 跑它验证完成，不通过则修。
- debugging → verify-and-fix：debugging 定位的"根因"是 verify-and-fix 的修复起点（debugging 找"问题是什么"，verify-and-fix 管"怎么改对"）。
- code-review → verify-and-fix：review 发现的需要修的问题进入 verify-and-fix 流程。

---

## 2. 横切 skill：贯穿开发全程

四个 skill 不在主链上，而是在开发全程按需触发：

| skill | 触发时机 | 与主链的关系 |
|-------|---------|-------------|
| `commit-message` | 任何提交前 | 把 verify-and-fix 验证通过的改动，提炼成规范提交信息 |
| `naming` | 任何命名决策 | 写 spec / 拆任务 / 写代码时随时可能触发 |
| `error-handling` | 设计错误处理时 | spec 的"设计"节或实现期，决定错误三态（抛/处理/重试） |
| `onboarding-unknown-codebase` | 接手陌生代码库时 | 与主链解耦，是独立的"上手"流程 |

---

## 3. 元 skill：自我繁衍

`lion-writing-skills` 是**元 skill**——它教怎么造新 skill、怎么验证。本仓库所有 skill 都是按它的规范产出的。它不在任何流水线上，但**约束所有其它 skill 的形态**（frontmatter 规范、正文骨架、轻量验证流程）。

---

## 4. 共同的方法论 DNA

8 个 skill 各自独立，但共享几条贯穿的设计原则（这是设计决策，不是巧合）：

- **决策重于知识**：每个 skill 都把篇幅让给"为什么这么定"，而不是堆背景（spec-writing 显式提出，其余 skill 遵循）。
- **显式标注不确定性**：TBD（spec-writing）、spike（task-breakdown / spec-writing）、默认假设推进（clarifying-questions）——不同 skill 用不同术语，本质都是"别把没定的包装成已定"。
- **少而准**：澄清少问关键问题、spec 精简不膨胀、task 不拆过细——共同反对"多而全"。
- **每步可验证**：spec 有验收标准、task 有完成定义、skill 自身有 evals——形成多层验证。

---

## 5. 已验证的协作证据

跨 skill 协作经过两类验证：

**A. 一致性验证**（提交 `04726c1`）：修复了 4 处跨 skill 问题——流水线相邻 skill 加上下游 mention、spike 术语自包含定义、澄清方法论互补引用、术语对齐。

**B. 流水线串跑验证**：

- **两环**（spec → task，提交前的验证）：场景"文件上传 + 病毒扫描（服务未选型）"。验证 spec 的验收标准落到 task 完成定义、TBD 对应 spike、依赖方向单向。5 项衔接验证全过。
- **三环**（clarifying → spec → task，2026-06-22）：场景"内部知识库"。验证 clarifying 的硬问题被 spec 继承、spec 的验收标准被 task 映射、三环方向零漂移。5 项衔接验证全过。
- **task → verify-and-fix 衔接**（2026-06-26，随 verify-and-fix 三轮验证）：verify-and-fix 的验证目标即 task-breakdown 的"完成定义"——把纸面标准变成实际跑过的证据。三轮 evals（声称完成未验证 / 为通过弱化检查 / 修症状不修病因 + 重构回归 / 断言灰度 / 防回归测试）共 9 次测试 × 40+ 检查点全过，且与 task-breakdown 的"完成定义"概念对齐。
- **执行段三 skill 衔接**（2026-06-27，随 testing/debugging/code-review 各自三轮验证）：三者各 9 次测试 × 40 检查点全过，且边界清晰——testing 落地完成定义、debugging 定位根因交给 verify-and-fix 修、code-review 审查发现问题进 verify-and-fix。三 skill 都在正文里显式 mention 了与 verify-and-fix 的边界，衔接契约自洽。

两次串跑 + verify-and-fix/执行段各三轮共同证明：主链各环节的时机判断与衔接契约成立。

---

## 6. 何时该用单个 skill，何时该走流水线

不是所有任务都要走完整主链。判断：

- **小改动 / 明确需求** → 直接做，用 testing 补测试、code-review 自查、verify-and-fix 验证、commit-message 提交。
- **中等需求（方向明但有设计决策）** → spec-writing 单独用，或 spec-writing → task-breakdown → 执行段（testing/code-review）→ verify-and-fix。
- **大需求 / 模糊需求 / 高风险方案** → 完整主链 clarifying → spec → task → 执行段 → verify-and-fix → commit。
- **修任何 bug** → debugging 定位根因 → verify-and-fix 修+验证（无论需求大小）。
- **接手陌生代码库** → onboarding-unknown-codebase，与主链解耦。

误用流水线和误用单个 skill 一样有害——小需求套全链是流程负担，大需求跳过澄清是返工源头。

---

## 7. 验收标准（这份文档自身）

按 spec-writing 规范，定义"这份文档怎样算成功"：

- 读者能在 5 分钟内看清 12 个 skill 的关系，不必读完 12 个 SKILL.md 才理解协作
- 流水线主链（含执行段三 skill）+ 横切（4 skill）+ 元（1 skill）的分层清晰
- 衔接契约（上下游产出对齐）显式写出，不是隐含
- 已验证的协作有证据引用（提交 hash + 验证场景）
- 文档自身符合 spec-writing 的精简原则（决策为主、不堆背景）

---

## 8. 后续

- **新增 skill 时**：按本文档第 1/2 节的分类判断它属于主链 / 横切 / 元，并更新对应分类表。
- **新增 skill 后**：跑 `lion-writing-skills` 的轻量验证流程（至少 3 轮），若属主链/横切与已有 skill 有衔接，需跑跨 skill 一致性检查 + 必要时流水线串跑。
- **本文档滞后时**：以各 SKILL.md 为真相（与 `2026-06-16-lion-skills-design.md` §9 的处理策略一致），本文档回填。
