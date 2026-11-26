# STATE：迭代状态与路线图

> 本文件是"继续"指令的唯一入口。每轮**先读、后做、再回写**。
> 更新时间：2026-11-26 ｜ 阶段：**S0 研究期**（未选策略、未冻结、未实盘）｜ trials_used：**12 / 50**

## 1. 现在在哪

- 设计书：`future/future.md`（参考，不作硬约束）
- 评审：`future/design-review.md`（22+3 项，含 P0 级口径错误已修）
- 实测回填：`future/docs/research/SETUP_DONE.md`
- 预登记与结果：`future/docs/research/R0.md`、`R1.md`
- 工程：`future/cnfut/`（config / data.rolling_contract / data.quality / data.fetch / backtest.engine / backtest.metrics）
  脚本：`future/scripts/{probe_tqsdk,pull_data,research_core}.py`；测试 **18 项全绿**

## 2. 已确认的硬事实（不要重复验证）

| 事实 | 数值/结论 |
| --- | --- |
| 环境 | Python 3.11.9 + TqSdk 3.10.6（不升级） |
| 交易日历 | 2003-01-01 ~ 2027-12-31；无 `has_night` 字段 |
| 日线 datetime | **交易日 00:00**（夜盘含在同根内）；序列左端有 `datetime=0` 占位行必须过滤 |
| 无成交日 | 返回整行：`close=NaN`、`volume=0`、`close_oi` 有值 |
| K 线上限 | 每序列 10000 根 |
| 已知偏差 | SHFE/DCE 老合约不在 `query_quotes` 列表里，但**直接按代码请求可取**（R-25） |
| 限频 | 连续大量请求会超时；需 1s 间隔 + 重试 + 缓存；卡住的 python 进程会占满配额 |
| 当前数据 | 15 品种 / 649 合约 / 130,157 根；CZCE 起 2017-09，SHFE+DCE 仅 2019-09 起 |

## 3. 当前结论（截至 R1）

- 判据：Rank IC 的 Newey-West **t ≥ 2**（预登记，不得改）。
- 12 次试验全不通过；最好 t=1.36（`combined`，2020-2023 窗口）。
- 止损效果随窗口**符号翻转**（2019 起去止损变好、2020 起去止损变差）→ 噪声。
- `carry_only` 正收益但 IC 为负（t=−0.19）→ 是商品 beta，不是 carry alpha。
- 因此：**不选策略、不冻结**。下一步只能"加信息"，不能调参。

## 4. ROADMAP（顺序固定；完成一项就回写状态）

| 编号 | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| P1 | 补齐 SHFE/DCE 的 2017-2019 历史：按品种挂牌月份**生成合约代码**直接取日线（不依赖 `query_quotes` 列表） | 15 品种最早数据 ≤ 2017-10-31；在 SETUP_DONE 记录 | **NEXT** |
| P2 | 年度 IC 分解（`combined` / `carry_only`） | 输出逐年 Rank IC 与 t，判断是否只在个别年份有效 | 待做 |
| P3 | 多空腿分解：`long_only` / `short_only` 的 IC 与收益，区分 carry alpha 与商品 beta | 表格 + 结论 | 待做 |
| P4 | 品种扩到 ≥20（能入池即加，含 INE/GFEX 视权限） | 池子 ≥20 且截面 IC 重算 | 待做 |
| P5 | 若某候选 IC t ≥ 2：冻结配置 → 最终检验窗口（2024-07-01~2026-09-30）**只跑一次** | 结论写入 docs/research/ | 条件触发 |
| P6 | 若 P1-P4 后仍不达标：写"无可验证 edge"的最终结论，停止调参 | 结论文档 + 汇报 | 条件触发 |

## 5. NEXT（下一轮直接做这个）

**P1：补 SHFE/DCE 的 2017-2019 历史。**

做法要点：
1. 从现有 `data/bars_all.parquet` 取每个 SHFE/DCE 品种出现过的交割月份，推断该品种的挂牌月份集合。
2. 按 `RYMM`（SHFE/DCE）与 `R YMM`（CZCE 3 位）规则生成 2017-09 ~ 2019-09 的候选合约代码。
3. 逐合约直接 `get_kline_serial`（1s 间隔、失败重试 2 次、失败的记入 `fetch_failures`），
   `limit_date` 用交割月前一月最后交易日（现有 `last_hold_dates` 逻辑）。
4. 合并进 `data/bars_all.parquet`（按 symbol+trade_date 去重）。
5. 验收：重跑分品种起始日期统计，SHFE/DCE 应 ≤ 2017-10-31；然后重跑 `combined`（2020-2023 与 2018-2023 两个窗口）看 IC 是否变化。

## 6. 已知坑（省时间用）

- 跑 TqSdk 前先 `$env:TQ_AUTH_USER=[Environment]::GetEnvironmentVariable('TQ_AUTH_USER','User')`（同理 PASSWORD），
  否则新 shell 读不到 `setx` 写入的变量。
- 下载脚本会写 `bars_all.parquet`，**不要并发跑两个 pull_data**。
- 卡住的 python 进程要清掉：`Get-Process python | Stop-Process -Force`，否则连接配额被占满、新请求全部超时。
- `--tag=` 要用等号形式，否则 argparse 会把 `-xxx` 当选项。
- 引擎口径：`[R-01] 风险用 3ATR`（`risk_atr_k=3.0`）、`[R-04] 现金流式权益`。
