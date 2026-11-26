# 一次性准备进度（00-一次性准备）

> 目的：把地基校准到"可复现"，不研究策略。
> 基线提交：`aa7fc4f`（`future: 改为调度器+总账模式…`）。
> 本文件的每一步都对应 `plan/00-一次性准备-最先发这个.txt` §二 的编号。

## 步骤状态

| 步 | 内容 | 状态 | 结果 / 产物 |
| --- | --- | --- | --- |
| S1 | 环境与依赖 | 完成 | Python 3.11.9 / TqSdk 3.10.6；pyarrow/lightgbm/matplotlib/pydantic/yaml/httpx 全部可导入 |
| S2 | 测试基线 | 完成 | `cd future && python -m pytest tests -q` → **26 passed**（原 18 + 新增 8 个数据体检单测） |
| S3 | 目录结构 | 完成 | `future/{cnfut,scripts,tests,docs/research,data,artifacts,plan}` 全部存在 |
| S4 | 数据体检 | 完成 | 新增 `scripts/data_health.py`（可复现），C1~C4 全通过；结果 `artifacts/data_health.json` |
| S5 | 总账校验 | 完成 | `ledger.yaml` 可 `yaml.safe_load`；`trials_used` 12 = R0(2)+R1(10) |
| S6 | 结果可追溯 | 完成（R0 例外） | 5 个 run 目录的 metrics.json 与 R1 §8 逐项一致；**R0 的产物目录已被 D1 清理，无法核实**（见 review_log D3） |
| S7 | 提交与报告 | 完成 | 本文件 + `SETUP_DONE.md`（首行 `结论：DONE`）+ `review_log.md` 追加 |

## S1 环境

| 项 | 值 |
| --- | --- |
| Python | 3.11.9（≥3.9 满足） |
| TqSdk | 3.10.6（未升级） |
| pyarrow / pandas / numpy | 26.0.0 / 3.0.6 / 2.4.6 |
| lightgbm / matplotlib / pydantic | 4.7.0 / 3.11.2 / 2.14.1 |
| yaml / httpx / pytest | 6.0.3 / 0.28.1 / 9.1.1 |

## S2 测试基线

```
..........................                                               [100%]
26 passed in 3.06s
```

原有 18 个测试全绿（未改任何断言）；新增 `tests/test_data_health.py` 8 个用例覆盖
`scripts/data_health.py` 的四条检查（唯一性、占位行、有成交行价格、无成交行价格置空）。

## S4 数据体检（`scripts/data_health.py`）

```
- 行数 130157；品种 15；合约 649；交易日 1089
- 区间 2017-09-01 ~ 2023-12-29（有成交 125544 行 / 无成交 4613 行）

| 检查 | 结论 | 明细 |
| C1 symbol+trade_date 唯一 | 通过 | 重复行 0 |
| C2 无 datetime=0 占位行 | 通过 | 占位行 0 |
| C3 有成交行 close 非空且 >0 | 通过 | close 为空 0、close<=0 0 |
| C4 无成交行 close 为空（未被前向填充） | 通过 | 无成交行却有 close 的 0 |
```

复现命令：

```powershell
cd future
python scripts/data_health.py --data data/bars_all.parquet --out artifacts/data_health.json
```

## S5/S6 总账与技术产物对账

| 项 | 结果 |
| --- | --- |
| `ledger.stage` | `research` |
| `budget` | `trials_used=12` / `global_trial_budget=50` |
| `rounds[].trials` 之和 | 12 → 与 `trials_used` 相等（符合） |
| `ledger.data` vs 体检 | rows 130157/130157、products 15/15、symbols 649/649、start 2017-09-01/2017-09-01（符合） |
| `candidates` | `[]`（无冻结候选） |
| `final_windows` | FW1 2024-07-01~2026-09-30，`status=unused`（未被读取） |
| `blockers` | B1 open（SHFE/DCE 历史只到 2019-09，R2 处理） |

`artifacts/backtest/*/metrics.json` 与 `R1.md` §8 逐项核对（全部一致）：

| 目录 | 年化 | 夏普 | 回撤 | 笔数 | IC (NW t) |
| --- | --- | --- | --- | --- | --- |
| `combined_h10_20190101-20231231` | +1.10% | 0.148 | 24.0% | 29 | 0.0352 (1.36) |
| `combined_h10_20200101-20231231` | +1.18% | 0.153 | 24.0% | 29 | 0.0352 (1.36) |
| `combined_h10_20200101-20231231_nostop` | −5.59% | −0.413 | 27.8% | 26 | 0.0352 (1.36) |
| `carry_only_h10_20200101-20231231` | +4.21% | 0.381 | 24.7% | 35 | −0.0057 (−0.19) |
| `carry_only_h10_20200101-20231231_nostop` | +6.91% | 0.591 | 21.8% | 31 | −0.0057 (−0.19) |

例外：`tsm_only` / `xsm_only`（R0 的两次试验）没有对应产物目录——D1 修复时把旧命名目录
（`artifacts/backtest/tsm_only_h10` 等）删除了。R0.md 的数字**无法再与技术产物核对**，
已记入 `review_log.md` 的 D3（处理：标注不可核实，不改判定、不补跑占试验预算）。

## 待使用者处理（非本次能自动完成）

1. 2015~2016 合约可及性仍未确认（上一轮 3 次重试超时），R2 只在 2017-09~2019-09 范围内回填。
2. `TqBacktest` 免费可用性、15:00~17:00 数据就绪时刻、交易所保证金率与涨跌停标准仍需外部信息。

## 提交哈希

- 基线：`aa7fc4f`
- 本次准备提交：见下方补记（同一工作流内追加）
