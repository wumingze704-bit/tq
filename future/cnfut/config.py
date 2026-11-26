"""策略参数（future.md §18 的最小子集）。

MVP 只收录回测与研究真正用到的字段；网格与冻结逻辑在轮次脚本里处理。
口径修正：
* [R-01] ``risk_atr_k`` 默认 3.0，与保护性止损距离一致；不再用 2 ATR 记账。
* [R-14] ``max_lots_per_product`` 保留为参数，但两万元档位由调用方传入 1 或 3。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Tuple

import yaml


@dataclass(frozen=True)
class UniverseParams:
    """§4.2 品种入选门槛。"""

    exchanges: Tuple[str, ...] = ("SHFE", "DCE", "CZCE")
    exclude_products: Tuple[str, ...] = ()
    max_margin_lot_frac: float = 0.25
    min_avg_volume: float = 50000.0
    min_open_interest: float = 50000.0
    min_listed_days: int = 250
    min_pool_size: int = 6


@dataclass(frozen=True)
class RollParams:
    """§8.4 执行合约与移仓。"""

    exit_buffer_days: int = 5
    broker_extra_days: int = 0
    oi_switch_ratio: float = 1.0
    confirm_days: int = 1


@dataclass(frozen=True)
class RiskParams:
    """§4.3/§13.1 风险参数。固定值，不参与调参。"""

    risk_per_trade: float = 0.04
    max_total_risk: float = 0.08
    max_margin_usage: float = 0.50
    margin_usage_hard: float = 0.70
    max_gross_notional_mult: float = 4.0
    max_notional_lot_mult: float = 2.5
    max_limit_loss: float = 0.25
    risk_atr_k: float = 3.0  # [R-01]
    max_lots_per_product: int = 3


@dataclass(frozen=True)
class CostParams:
    """§14.4 成本。费率由数据层按品种/日期给到引擎，这里只放全局倍数。"""

    broker_fee_mult: float = 2.0
    slippage_ticks: float = 1.0


@dataclass(frozen=True)
class BacktestParams:
    """§14 回测与 §13.6 熔断。"""

    capital_cny: float = 20000.0
    limit_buffer_ticks: float = 1.0
    force_close_ratio: float = 1.0
    force_close_extra_ticks: float = 2.0
    level1_dd: float = 0.15
    level1_recover: float = 0.95
    level2_dd: float = 0.25
    cooldown_days: int = 20
    floor_frac: float = 0.50


@dataclass(frozen=True)
class PortfolioParams:
    """§4.3/§12.4 组合与选择。"""

    max_positions: int = 2
    structure: str = "long_short"
    long_q: float = 0.30
    short_q: float = 0.30
    hold_q: float = 0.40
    corr_window: int = 120
    max_same_dir_corr: float = 0.70


@dataclass(frozen=True)
class Params:
    """全部参数的聚合，附 §18 的交叉校验。"""

    universe: UniverseParams = field(default_factory=UniverseParams)
    roll: RollParams = field(default_factory=RollParams)
    risk: RiskParams = field(default_factory=RiskParams)
    cost: CostParams = field(default_factory=CostParams)
    backtest: BacktestParams = field(default_factory=BacktestParams)
    portfolio: PortfolioParams = field(default_factory=PortfolioParams)

    def validate(self) -> None:
        """§18 交叉校验，不满足抛 ValueError（配置错误在启动时暴露）。"""
        risk, bt = self.risk, self.backtest
        if not (risk.max_margin_usage < risk.margin_usage_hard < bt.force_close_ratio):
            raise ValueError("需满足 max_margin_usage < margin_usage_hard < force_close_ratio")
        if not (bt.level1_dd < bt.level2_dd < 1.0 - bt.floor_frac):
            raise ValueError("需满足 level1_dd < level2_dd < 1 - floor_frac")
        if risk.risk_per_trade > risk.max_total_risk:
            raise ValueError("需满足 risk_per_trade <= max_total_risk")
        if self.portfolio.structure not in ("long_short", "free"):
            raise ValueError("structure 只能是 long_short / free")
        if self.portfolio.max_positions < 1:
            raise ValueError("max_positions 必须 >= 1")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Params":
        """从 YAML 字典构造；未知字段直接报错，避免拼写错误被静默忽略。"""
        groups = {
            "universe": UniverseParams,
            "roll": RollParams,
            "risk": RiskParams,
            "cost": CostParams,
            "backtest": BacktestParams,
            "portfolio": PortfolioParams,
        }
        kwargs = {}
        for key, value in raw.items():
            if key not in groups:
                raise ValueError(f"未知配置段: {key}")
            group = groups[key]
            unknown = set(value) - {f.name for f in group.__dataclass_fields__.values()}
            if unknown:
                raise ValueError(f"{key} 段存在未知字段: {sorted(unknown)}")
            kwargs[key] = group(**value)
        params = cls(**kwargs)
        params.validate()
        return params


def load_params(path: str) -> Params:
    """读取 configs/futures_cn.yaml。"""
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return Params.from_mapping(raw)
