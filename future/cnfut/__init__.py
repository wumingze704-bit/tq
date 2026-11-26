"""cnfut：国内商品期货多空轮动策略工程（future.md 的最小纵切实现）。

本包只实现回测与研究需要的最小闭环，模块划分沿用 future.md §19.2。
所有口径修正记录在 future/design-review.md，代码里用 ``[R-xx]`` 标注。
"""

__all__ = ["config", "data", "features", "portfolio", "backtest"]
