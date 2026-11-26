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

### 遗留（不影响当前判定）

- 数据回填 blocker B1：SHFE/DCE 仅 2019-09 起，需按合约代码生成补齐（R2 处理）。
- `carry_only` 的 IC t=−0.19 与正收益并存，需 R3 的多空腿分解解释。
