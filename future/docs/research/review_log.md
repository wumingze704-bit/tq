# 前置复核日志（只追加，不删改）

约定：每条记录写 UTC 时间、复核对象（含 `git log -1 --format=%h -- <路径>` 哈希）、
逐项结论（符合 / 不符合 / 无法核实）、处理方式。缺陷编号 D1、D2…，状态 OPEN/CLOSED。

## 2026-11-26 首次复核（建立台账基线）

复核对象：`future/docs/research/{R0,R1,SETUP_DONE}.md`、`future/artifacts/backtest/*`、
`future/docs/research/ledger.yaml`、`future/scripts/research_core.py`。

| 项 | 结论 | 说明 |
| --- | --- | --- |
| 测试基线 | 符合 | `pytest future/tests -q` 18 passed |
| ledger 与轮次文档数字一致 | 符合 | R0 trials=2、R1 trials=10，合计 12 = ledger.trials_used |
| 结果可追溯（目录 ↔ 数字） | **不符合 → D1** | 不同窗口写入同一目录，后者覆盖前者 |
| R1 文档数字与当前代码口径一致 | **不符合 → D2** | CZCE 回填 + 最小手规则后未重跑，文档仍是旧口径 |
| 研究期脚本未读最终窗口 | 符合 | `research_core.py` 无 2024-07 之后的硬编码日期；窗口来自入参 |

### D1 结果覆盖导致不可追溯（OPEN → 已修复）

- 现象：`--start` 不同的两次运行都写入 `artifacts/backtest/combined_h10`，前者被覆盖，
  违反"判定必须能追溯到原始输出"。
- 修复：`out_dir` 改为 `{strategy}_h{H}_{start}-{end}{tag}`；删除旧命名目录并按登记配置重跑。
- 复现方式：`python future/scripts/research_core.py --strategy combined --horizon 10 --start 2020-01-01 --end 2023-12-31`
  后检查目录名含窗口区间。
- 状态：**CLOSED**。

### D2 R1 数字与当前口径不一致（OPEN → 已修复）

- 现象：R1 §2/§3/§6 的 2019 窗口数字（−5.05%、−9.44%、−9.71%）产生于"CZCE 未回填、无最小手规则"
  的旧代码；当前代码重跑得 +1.10%（0.148，IC t=1.36）。
- 处理：R1 新增 §8「口径变更后重跑」，列出新数字并**显式作废旧数字**；
  同时作废 §6 关于"止损效果随窗口符号翻转=噪声"的结论（新口径下两窗口结果几乎相同）。
  原段落保留不删，符合"失败与更正同权登记"。
- 复现方式：对比 `artifacts/backtest/combined_h10_20200101-20231231/metrics.json` 与 R1 §2 表中数字。
- 状态：**CLOSED**。

## 2026-11-26 14:34 UTC 前置复核（执行 00-一次性准备）

复核对象：`future/docs/research/{SETUP_DONE,R0,R1,ledger.yaml}`、`future/artifacts/backtest/*`、
`future/data/bars_all.parquet`、`future/scripts/{research_core,pull_data,data_health}.py`、
`future/cnfut/**`。基线提交 `aa7fc4f`。

| 项 | 结论 | 说明 |
| --- | --- | --- |
| SETUP_DONE 首行标记 | **不符合 → D3** | 首行为 `# SETUP_DONE：§23 待实测清单回填（期货）`，不是 `结论：DONE`，调度器无法放行 |
| 测试基线 | 符合 | `pytest future/tests -q` 26 passed（原 18 + 新增 8） |
| 目录结构 | 符合 | `future/{cnfut,scripts,tests,docs/research,data,artifacts,plan}` 齐全 |
| 数据体检 C1~C4 | 符合 | 重复 0、占位行 0、有成交行 close 全 >0、无成交行 close 全空 |
| ledger 可解析、字段齐全 | 符合 | `yaml.safe_load` 通过；stage/trials_used/rounds/candidates/final_windows/blockers 均在 |
| `trials_used` = 各轮 trials 之和 | 符合 | 12 = R0(2) + R1(10) |
| ledger.data 与实测数据一致 | 符合 | rows 130157、products 15、symbols 649、start 2017-09-01 逐项相等 |
| R1 文档数字 ↔ metrics.json | 符合 | 5 个 run 目录的年化/夏普/回撤/笔数/IC t 与 R1 §8 表逐项一致 |
| R0 文档数字 ↔ 技术产物 | **无法核实 → D4** | `tsm_only`/`xsm_only` 的产物目录在 D1 修复时被删除，无 metrics.json 可比 |
| 研究期脚本未读最终窗口 | 符合 | `research_core.py` 无 2024-07 之后的日期常量（默认窗口 2020-01-01~2023-12-31，来自入参）；`grep 2024-0[7-9]｜2025｜2026` 只命中文档/probe 输出 |
| `scripts/final_run.py` 存在性 | 符合（尚未创建，符合预期） | 无候选、无最终检验需求；研究期代码未 import final_run |
| 窗口数据文件是否已下载 | 符合 | `future/data/` 下无 `final/` 目录、无不含窗口区间数据；FW1 `unused` |

### D3 SETUP_DONE 缺 `结论：DONE` 标记（OPEN → 已修复）

- 现象：`SETUP_DONE.md` 是上一轮 §23 探测报告，首行不是调度器要求的 `结论：DONE`，
  导致 `0-自动调度` 永远停在第 0 步、无法进入研究循环。
- 修复：保留原 §23 报告全文（编号 §0~§7 不动，`cnfut/data/quality.py` 与
  `design-review.md` §10 仍引用其 §3/§4），在文件顶部新增 §P1~§P8 准备记录，
  **首行改为 `结论：DONE`**；同时新增 `progress_setup.md` 记录每步状态。
- 复现方式：`Get-Content future/docs/research/SETUP_DONE.md -TotalCount 1` 应为 `结论：DONE`。
- 状态：**CLOSED**（随本次提交）。

### D4 R0 的技术产物缺失，数字不可追溯（OPEN）

- 现象：`artifacts/backtest/` 只剩 5 个目录（combined/carry_only），R0 的
  `tsm_only`/`xsm_only` 目录在 D1 重命名清理时被删除，R0.md 的 2.89%/0.778、
  2.13%/0.639、IC t=0.54/0.74 无原始输出可核对。
- 影响：不影响任何"当前判定"——R0 已是 failed（t 远小于 2），且 R1 之后口径已变
  （CZCE 回填 + 最小手规则），补跑等于新试验、要占试验预算，不能用来"恢复" R0 的数字。
- 处理：按 §五.3.d 记 **无法核实**，不改 R0 判定、不补跑；在 `SETUP_DONE.md` §P6 与
  `R0.md` §2 顶部标注该限制。
- 状态：**OPEN**（记录性缺陷，不阻塞任何轮次；后续判定一律要求产物目录存在）。

### 结论

本项（00-一次性准备）前置复核通过：D3 已修复并关闭，D4 为记录性缺陷（OPEN，不影响推进）。
**没有作废任何已作出的判定。**

## 2026-11-26 前置复核（执行 1-研究循环 → R2）

复核对象：`future/docs/research/{R0,R1,R2,ledger.yaml}`、`future/artifacts/backtest/*/metrics.json`、
`future/data/bars_all.parquet`、`future/scripts/{research_core,pull_data,data_health}.py`、`future/cnfut/**`。
基线提交 `46b0f0b`。

| 项 | 结论 | 说明 |
| --- | --- | --- |
| 测试全绿 | 符合 | `pytest tests -q` 26 passed |
| 上一轮数字 ↔ 原始产物 | 符合 | R1 §8 表 5 行与 5 个 `metrics.json` 逐项相等（年化/夏普/回撤/笔数/IC t） |
| `trials_used` = Σ 各轮 trials | 符合 | 12 = R0(2) + R1(10) |
| 本轮已预登记 | 符合 | `R2.md` 先提交，判据照抄 `ledger.acceptance` |
| 研究期脚本未读 2024-07-01 之后 | 符合 | 日期常量 grep 零命中；`bars_all.parquet` 最晚 2023-12-29；无 `scripts/final_run.py`、无 import |
| 数据依赖 | 不满足（预期内） | SHFE/DCE 8 品种最早 2019-09-17~2019-11-15，正是 B1 要回填的部分；R2 第 1 步先回填 |
| 次要判据可计算性 | **不符合 → D5** | `metrics` 无分块 bootstrap、`research_core` 无 cost2x 开关（R0/R1 同样未算） |

### D5 次要判据有两项当前不可计算（OPEN）

- 现象：`ledger.acceptance.secondary` 第 1 条的"分块 bootstrap 95% 下限"与第 3 条的"cost2x 情景"在现有引擎里都没有实现，
  `artifacts/backtest/*/metrics.json` 里也没有对应字段。R0/R1 的判定只实际用到 primary + 最大回撤 + 年化收益。
- 影响：**不影响任何已作出的判定**（全部 failed 的原因是 primary 不满足，与缺失两项无关）。
  但若将来某轮 primary 通过，必须先把这两项补成可计算，才允许判 pass。
- 处理：在 `R2.md` §5 显式标注"未计算"，不改判据；补实现留到"primary 通过"时再做（否则会占试验预算去跑无意义的情景）。
- 状态：**OPEN**（记录性，不阻塞推进）。

### 结论

R2 前置复核通过（数据缺口属本轮任务本身，已登记为 B1）。**没有作废任何已作出的判定。**

## 2026-11-26 执行后复核（R2 结果记账）

复核对象：`future/docs/research/{R2,ledger.yaml}`、`future/artifacts/backtest/*_r2backfill|*_cost2x`、
`future/data/bars_all.parquet`、`future/artifacts/data_health.json`、`future/cnfut/backtest/metrics.py`、
`future/scripts/{research_core,backfill_history}.py`、`future/cnfut/data/contracts.py`。

| 项 | 结论 | 说明 |
| --- | --- | --- |
| R2 文档数字 ↔ 原始产物 | 符合 | 3 个 `metrics.json` 与 R2 §8.2 表逐项相等（年化/夏普/回撤/笔数/换手/成本/IC t/bootstrap） |
| `trials_used` = Σ 各轮 trials | 符合 | 15 = R0(2) + R1(10) + R2(3) |
| 数据体检 C1~C4 | 符合 | 162,370 行、826 合约、15 品种、1,538 交易日；四项全通过 |
| 回填未读最终窗口 | 符合 | 回填区间 `--start 2017-09-01 --end 2019-09-30`；`bars_all.parquet` 最晚 2023-12-29 |
| 测试基线 | 符合 | 38 passed（新增 `test_contracts.py` 7 项 + `test_metrics.py` 4 项） |
| 旧产物目录被覆盖 | **不符合 → D6** | 无 tag 的 `combined_h10_20200101-20231231` 被回填后重跑覆盖 |

### D5 次要判据不可计算（OPEN → 已修复/CLOSED）

- 修复：`cnfut/backtest/metrics.py` 新增 `block_bootstrap_annual_return`（分块 20 天、2000 次、固定种子
  `20261126`、取 5% 分位为下限），`research_core.py` 新增 `--cost-mult`（手续费率与滑点同时放大）
  并把 `bootstrap_annual_lower95` / `cost_mult` 写进 `metrics.json`；单测 4 项（常数收益、可复现、
  负收益为负、样本不足）。
- 影响：R2 因此可以完整判定——primary 过（t=2.25）但两条 secondary 不过 → failed。
- 状态：**CLOSED**（随 R2 提交；提交哈希见本次提交）。

### D6 重跑覆盖了 R1 的无 tag 产物目录（OPEN）

- 现象：R2 的对照窗口直接用 `--start 2020-01-01 --end 2023-12-31`（未加 tag）跑了一次，
  覆盖了 R1 §8 的 `artifacts/backtest/combined_h10_20200101-20231231`；随后才意识到应加 tag。
  该目录现在装的是**回填后**的数字（年化 −0.77%、IC t=1.27），与 R1 §8 的 +1.18%/1.36 不同。
- 影响：R1 的原数字失去"可直接对照的产物目录"。但 ① R1.md §8 的表格完整保留；
  ② 本次会话的前置复核（见上一条记录）已在**覆盖之前**逐项核对过 R1 §8 与当时产物一致；
  ③ R2 §8.4 已写明差异原因（数据覆盖变化导致执行合约与 carry 序列变化）。
  **不因此作废 R1 的判定**（R1 判定本就是 failed，且差异方向不影响"不达标"）。后续 R2 结果一律用
  `_r2backfill` / `_cost2x` tag 的独立目录。
- 处理：`review_log.md` 记录 + `R2.md` §8.4 说明；今后**重跑必须带 tag**（写入 `1-研究循环` 的执行习惯，
  本条即为提醒）。
- 状态：**OPEN**（记录性；已用带 tag 的目录保证 R2 可追溯）。


