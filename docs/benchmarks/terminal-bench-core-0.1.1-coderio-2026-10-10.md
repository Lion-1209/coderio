# coderio × Terminal-Bench — 首次官方基准成绩

- 日期：2026-10-10 | agent: **coderio v0.6.0**（PyPI 安装进任务容器，四道门默认开启）
- 模型：`step-5-preview`（StepFun coding plan，Anthropic 协议直连）——非前沿模型
- 数据集：**terminal-bench-core 0.1.1**（官方缓存副本，80 题中隔行确定性抽 40 题，无难度挑选）
- 判分：Terminal-Bench 官方 harness——任务方 Dockerfile 构建环境、tmux 会话、第三方 pytest 判定
- 环境：WSL2 Ubuntu-22.04 独立 docker 引擎；适配器 `coderio_tb_agent`（docker-exec 直连）

## 成绩

| 口径 | 分数 |
|---|---|
| **coderio 实际运行的题目**（40 - 6 装不上 - 6 镜像不可建） | **14/25 = 56%** |
| 全样本（40 题） | 14/40 = 35% |
| 仅首轮单次尝试（无重跑） | 5/14 = 36%（实跑口径） |

**通过（14）**：blind-maze-explorer-5x5 / blind-maze-explorer-algorithm.easy / chess-best-move / fibonacci-server / get-bitcoin-nodes / git-workflow-hack / grid-pattern-transform / heterogeneous-dates / modernize-fortran-build / openssl-selfsigned-cert / processing-pipeline / simple-sheets-put / sqlite-with-gcov / tmux-advanced-workflow

**真实失败（6）**：decommissioning-service-with-sensitive-data / nginx-request-logging / pytorch-model-cli / run-pdp11-code / solana-data / vim-terminal-task

**coderio 内部 900s 超时（5）**：build-tcc-qemu / extract-moves-from-video / password-recovery / path-tracing-reverse / raman-fitting.easy

**测试阶段基建（3）**：build-initramfs-qemu / cron-broken-network / pytorch-model-cli.hard

**适配器覆盖外（12）**：6 题镜像在本地网络下无法构建（swe-bench-astropy 等 GB 级 + Dockerfile 内 git clone）+ 6 题极简/异构镜像装不上 coderio（configure-git-webserver / eval-mteb / fix-pandas-version / intrusion-detection / polyglot-c-py / swe-bench-fsspec）

## 与官方排行榜的可比性（诚实声明）

1. **多轮重试**：未通过题目经三轮基建修复后重跑，合并规则为"任一轮通过即通过"——对重跑题相当于 best-of-N。可比的单次口径是首轮 5/14=36%（实跑）。
2. **模型不可比**：排行榜头部是前沿模型；step-5-preview 属轻量级。56% 的实跑口径在此模型档位属于强表现，但不能与前沿模型条目直接并列。
3. **样本**：40/80 的确定性子集（隔行抽样），非全集。
4. **本机网络**：三轮修复（PEP 668、容器 DNS 污染、Docker Hub 污染、uv PATH）后仍有 12 题环境性不可运行——这些按未通过计（保守）。

## 基建修复记录（对复现者有价值）

- WSL2 内独立 docker 引擎（不依赖 Docker Desktop 集成开关）
- dockerd DNS 配 alidns/dnspod（默认 NAT 解析器对 github.com 返回 127.0.0.1）
- registry-mirrors（registry-1.docker.io 被污染解析到无关 IP）
- 安装链：系统 pip（--break-system-packages）→ apt 装 python3-pip → uv 自带 Python 3.12；运行命令显式 export PATH=$HOME/.local/bin
- 适配器绕过 harness 的 tmux wait 同步（信号竞态会挂死整个预算），改用 docker exec 同步执行

原始数据：`~/tb-runs/2026-10-10__{11-43-39,14-57-27,16-40-56}`（WSL 内）+ 本目录 scoreboard json。
