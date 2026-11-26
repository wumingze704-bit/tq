# STATE：迭代状态与路线图（**摘要**，权威状态见 `docs/research/ledger.yaml`）

> 驱动方式：把 `future/plan/0-自动调度-每次发这个.txt` 全文发给 AI 即可，它会自己判断下一步。
> 本文件只做人类可读的摘要；机器判断一律读 ledger.yaml。

> 更新时间：2026-11-26 ｜ 阶段：**S0 研究期**（未选策略、未冻结、未实盘）｜ trials_used：**15 / 50**


## 1. 现在在哪

- 设计书：`future/future.md`（参考，不作硬约束）
- 评审：`future/design-review.md`（22+3 项，含 P0 级口径错误已修）
- 实测回填：`future/docs/research/SETUP_DONE.md`
- 预登记与结果：`future/docs/research/R0.md`、`R1.md`、`R2.md`
- 工程：`future/cnfut/`（config / data.rolling_contract / data.quality / data.fetch / data.contracts /
  backtest.engine / backtest.metrics）
  脚本：`future/scripts/{probe_tqsdk,pull_data,backfill_history,data_health,research_core}.py`；测试 **38 项全绿**

## 2. 已确认的硬事实（不要重复验证）

| 事实 | 数值/结论 |
| --- | --- |
| 环境 | Python 3.11.9 + TqSdk 3.10.6（不升级） |
| 交易日历 | 2003-01-01 ~ 2027-12-31；无 `has_night` 字段 |
| 日线 datetime | **交易日 00:00**（夜盘含在同根内）；序列左端有 `datetime=0` 占位行必须过滤 |
| 无成交日 | 返回整行：`close=NaN`、`volume=0`、`close_oi` 有值 |
| K 线上限 | 每序列 10000 根 |
| 已知偏差 | SHFE/DCE 老合约不在 `query_quotes` 列表里，但**直接按代码请求可取**（R-25，R2 已回填） |
| 限频 | 同一会话累计订阅变多后请求会退化到 85s 超时（新会话 0.2s）→ 取数脚本**每 20 个合约换一个会话** |
| 当前数据 | 15 品种 / 826 合约 / 162,370 根；13 品种起 2017-09-01，eg 2018-12-10、SA 2019-12-06（上市日） |

## 3. 当前结论（截至 R2）

- 判据：Rank IC 的 Newey-West **t ≥ 2** + 年化为正且 bootstrap 95% 下限 > 0 + 回撤 ≤ 30% + cost2x 年化为正（预登记，不得改）。
- 15 次试验全不通过。
- **R2 是本项目第一次 primary 达标**：`combined` 2018-2023 的 IC t=**2.25**、年化 +2.14%、夏普 0.225、回撤 24.19%；
  但 **bootstrap 95% 下限 −5.78%**、**cost2x 年化 −1.40%** 两条 secondary 不过 → 按 `reject_if` 判 failed。
- 含义：信号在统计上有排序能力，但扣成本后收益不稳、统计区间跨 0。**不能只报 primary 就说可用**。
- 因此：**不选策略、不冻结**。下一步只能"加信息/加诊断"，不能调参。


## 4. ROADMAP（顺序固定；完成一项就回写状态）

| 编号 | 任务 | 验收标准 | 状态 |
| --- | --- | --- | --- |
| P1 | 补齐 SHFE/DCE 的 2017-2019 历史：按品种挂牌月份**生成合约代码**直接取日线（不依赖 `query_quotes` 列表） | 15 品种最早数据 ≤ 2017-10-31；在 SETUP_DONE 记录 | **DONE**（R2：7/8 SHFE+DCE 品种到 2017-09-01；eg 2018-12-10、SA 2019-12-06 受上市日限制，记 B2） |
| P2 | 年度 IC 分解（`combined` / `carry_only`） | 输出逐年 Rank IC 与 t，判断是否只在个别年份有效 | **NEXT**（ledger R3，纯诊断、不计试验） |
| P3 | 多空腿分解：`long_only` / `short_only` 的 IC 与收益，区分 carry alpha 与商品 beta | 表格 + 结论 | 待做 |
| P4 | 品种扩到 ≥20（能入池即加，含 INE/GFEX 视权限） | 池子 ≥20 且截面 IC 重算 | 待做 |
| P5 | 若某候选 IC t ≥ 2：冻结配置 → 最终检验窗口（2024-07-01~2026-09-30）**只跑一次** | 结论写入 docs/research/ | 条件触发 |
| P6 | 若 P1-P4 后仍不达标：写"无可验证 edge"的最终结论，停止调参 | 结论文档 + 汇报 | 条件触发 |

## 5. NEXT（下一轮直接做这个）

**P2 / ledger R3：年度 IC 分解 + 多空腿分解（纯诊断，不计试验）。**

做法要点：
1. 用回填后的数据（`data/bars_all.parquet`，826 合约）重算 `combined` / `carry_only` 的 **逐年** Rank IC 与 NW t；
2. 分别统计多头腿、空头腿的收益贡献与 IC；
3. 结论必须回答：信号是普遍有效、只在个别年份有效、还是完全由商品 beta 驱动；
4. R2 已证明 2018-2023 的 primary 过但收益端不过（bootstrap −5.78%、cost2x −1.40%），
   R3 的任务就是回答"正收益是不是只靠 2018-2019 那两年"。


## 6. 已知坑（省时间用）

- 跑 TqSdk 前先 `$env:TQ_AUTH_USER=[Environment]::GetEnvironmentVariable('TQ_AUTH_USER','User')`（同理 PASSWORD），
  否则新 shell 读不到 `setx` 写入的变量。
- 下载脚本会写 `bars_all.parquet`，**不要并发跑两个 pull_data**。
- 卡住的 python 进程要清掉：`Get-Process python | Stop-Process -Force`，否则连接配额被占满、新请求全部超时。
- `--tag=` 要用等号形式，否则 argparse 会把 `-xxx` 当选项。
- 引擎口径：`[R-01] 风险用 3ATR`（`risk_atr_k=3.0`）、`[R-04] 现金流式权益`。
