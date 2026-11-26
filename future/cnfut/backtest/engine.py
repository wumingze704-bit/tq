"""§14 回测引擎：账户、成交、约束、逐日推进。

口径修正（见 future/design-review.md）：
* [R-01] 风险与保护性止损统一用 ``risk.risk_atr_k``（默认 3 ATR），不再用 2 ATR 记账。
* [R-04] 权益改现金流式：``cash`` 记已实现盈亏与费用，持仓按 ``(close - entry)`` 计浮盈，
  平仓日先实现再盯市，避免“lots 取 t 还是 t−1”的歧义。
* [R-06] 所有价格按 ``(trade_date, symbol)`` 精确查找，不依赖序列位置。

未覆盖（MVP 范围外，见 design-review §7）：平今/平昨费率区分、停板顺延重试、结算价口径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..config import Params

BAR_COLUMNS = ("trade_date", "symbol", "product", "open", "high", "low", "close",
               "limit_up", "limit_down", "volume_multiple", "price_tick",
               "margin_rate", "fee_open", "fee_close", "atr_pts")


@dataclass
class Position:
    """带符号手数的持仓；``lots > 0`` 为多，``< 0`` 为空。"""

    product: str
    symbol: str
    lots: int
    entry_price: float
    stop_price: Optional[float]
    volume_multiple: float


@dataclass
class Result:
    equity: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame


@dataclass
class Backtester:
    """逐日推进的回测器；回测、模拟、实盘共用同一套目标持仓口径。"""

    params: Params
    bars: pd.DataFrame
    exec_map: pd.DataFrame
    cash: float = field(init=False)
    positions: Dict[str, Position] = field(init=False, default_factory=dict)
    peak_equity: float = field(init=False)
    trades: List[dict] = field(init=False, default_factory=list)
    rows: List[dict] = field(init=False, default_factory=list)
    pending_flatten: Optional[str] = field(init=False, default=None)
    cooldown: int = field(init=False, default=0)
    terminal: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        missing = [c for c in BAR_COLUMNS if c not in self.bars.columns]
        if missing:
            raise ValueError(f"bars 缺少列: {missing}")
        frame = self.bars.copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        self._bar_index = frame.set_index(["trade_date", "symbol"], drop=False).sort_index()
        mapping = self.exec_map.copy()
        mapping["trade_date"] = pd.to_datetime(mapping["trade_date"])
        self._exec = {(row.trade_date, row.product): row.exec_symbol
                      for row in mapping.itertuples()}
        self.cash = float(self.params.backtest.capital_cny)
        self.peak_equity = self.cash

    # ------------------------------------------------------------------ 基础查询
    def _bar(self, day, symbol: Optional[str]) -> Optional[pd.Series]:
        if symbol is None:
            return None
        key = (pd.Timestamp(day), symbol)
        if key not in self._bar_index.index:
            return None
        return self._bar_index.loc[key]

    def exec_symbol(self, day, product: str) -> Optional[str]:
        return self._exec.get((pd.Timestamp(day), product))

    def _unrealized(self, prices: Dict[str, float]) -> float:
        total = 0.0
        for pos in self.positions.values():
            price = prices.get(pos.symbol)
            if price is None or not np.isfinite(price):
                price = pos.entry_price
            total += (price - pos.entry_price) * pos.volume_multiple * pos.lots
        return total

    def _margin(self, prices: Dict[str, float], rates: Dict[str, float]) -> float:
        total = 0.0
        for pos in self.positions.values():
            price = prices.get(pos.symbol, pos.entry_price)
            rate = rates.get(pos.symbol, np.nan)
            if not np.isfinite(rate):
                rate = 0.0
            total += abs(pos.lots) * price * pos.volume_multiple * rate
        return total

    def equity(self, prices: Dict[str, float]) -> float:
        return self.cash + self._unrealized(prices)

    # ------------------------------------------------------------------ 成交约束
    def _blocked(self, bar: pd.Series, buy: bool) -> Optional[str]:
        """§14.3 成交约束；返回阻断原因，None 表示可成交。"""
        if not np.isfinite(bar["open"]):
            return "无开盘价"
        if bar["high"] == bar["low"]:
            return "一字板"
        buffer = self.params.backtest.limit_buffer_ticks * bar["price_tick"]
        if buy and np.isfinite(bar["limit_up"]) and bar["open"] >= bar["limit_up"] - buffer:
            return "涨停无法买入"
        if (not buy) and np.isfinite(bar["limit_down"]) \
                and bar["open"] <= bar["limit_down"] + buffer:
            return "跌停无法卖出"
        return None

    def _fill_price(self, bar: pd.Series, buy: bool, extra_ticks: float = 0.0) -> float:
        ticks = self.params.cost.slippage_ticks + extra_ticks
        return bar["open"] + ticks * bar["price_tick"] if buy else bar["open"] - ticks * bar["price_tick"]

    def _record(self, day, bar, symbol: str, side: str, lots: int, price: float,
                fee: float, reason: str, status: str = "成交") -> None:
        self.trades.append({
            "trade_date": pd.Timestamp(day), "symbol": symbol, "side": side,
            "lots": lots, "price": price, "fee": fee, "reason": reason, "status": status,
        })

    # ------------------------------------------------------------------ 交易动作
    def _stop_price(self, bar: pd.Series, entry_price: float, buy: bool) -> Optional[float]:
        atr = bar["atr_pts"]
        if not np.isfinite(atr) or atr <= 0:
            return None
        distance = self.params.risk.risk_atr_k * float(atr)
        return float(entry_price - distance) if buy else float(entry_price + distance)

    def _open(self, day, bar: pd.Series, product: str, lots: int, reason: str) -> bool:
        """按开盘价开仓；``lots`` 带符号。被涨跌停/一字板阻断时记一条 skip。"""
        if lots == 0:
            return True
        buy = lots > 0
        block = self._blocked(bar, buy)
        if block:
            self._record(day, bar, bar["symbol"], "买开" if buy else "卖开", lots,
                         np.nan, 0.0, reason, status=block)
            return False
        price = self._fill_price(bar, buy)
        fee = abs(lots) * float(bar["fee_open"])
        self.cash -= fee
        self.positions[product] = Position(
            product=product, symbol=str(bar["symbol"]), lots=int(lots), entry_price=price,
            stop_price=self._stop_price(bar, price, buy),
            volume_multiple=float(bar["volume_multiple"]))
        self._record(day, bar, bar["symbol"], "买开" if buy else "卖开", lots, price, fee, reason)
        return True

    def _close(self, day, product: str, reason: str, base_price: Optional[float] = None,
               extra_ticks: float = 0.0) -> bool:
        """平掉某品种全部持仓；``base_price`` 用于止损按止损价成交。"""
        pos = self.positions.get(product)
        if pos is None:
            return True
        bar = self._bar(day, pos.symbol)
        if bar is None:
            return False
        buy = pos.lots < 0
        if bar["high"] == bar["low"]:
            self._record(day, bar, pos.symbol, "买平" if buy else "卖平", abs(pos.lots),
                         np.nan, 0.0, reason, status="一字板")
            return False
        buffer = self.params.backtest.limit_buffer_ticks * float(bar["price_tick"])
        base = float(bar["open"]) if base_price is None else float(base_price)
        if buy and np.isfinite(bar["limit_up"]) and base >= bar["limit_up"] - buffer:
            self._record(day, bar, pos.symbol, "买平", abs(pos.lots), np.nan, 0.0,
                         reason, status="涨停无法买入")
            return False
        if (not buy) and np.isfinite(bar["limit_down"]) and base <= bar["limit_down"] + buffer:
            self._record(day, bar, pos.symbol, "卖平", abs(pos.lots), np.nan, 0.0,
                         reason, status="跌停无法卖出")
            return False

        ticks = self.params.cost.slippage_ticks + extra_ticks
        price = base + ticks * float(bar["price_tick"]) if buy \
            else base - ticks * float(bar["price_tick"])
        fee = abs(pos.lots) * float(bar["fee_close"])
        self.cash += (price - pos.entry_price) * pos.volume_multiple * pos.lots - fee
        self._record(day, bar, pos.symbol, "买平" if buy else "卖平", abs(pos.lots),
                     price, fee, reason)
        del self.positions[product]
        return True

    def _check_stop(self, day, product: str) -> None:
        """§14.3 保护性止损的日线保守成交规则。"""
        pos = self.positions.get(product)
        if pos is None or pos.stop_price is None:
            return
        bar = self._bar(day, pos.symbol)
        if bar is None:
            return
        stop = pos.stop_price
        long_side = pos.lots > 0
        gap = bar["open"] <= stop if long_side else bar["open"] >= stop
        if gap:
            self._close(day, product, "止损")
            return
        touch = bar["low"] <= stop if long_side else bar["high"] >= stop
        if touch:
            self._close(day, product, "止损", base_price=stop)

    def _roll(self, day, product: str) -> None:
        """§8.4 第 5 条：t+1 开盘平旧开新，两笔成交各计成本。"""
        pos = self.positions.get(product)
        if pos is None:
            return
        new_symbol = self.exec_symbol(day, product)
        if new_symbol is None or new_symbol == pos.symbol:
            return
        new_bar = self._bar(day, new_symbol)
        if new_bar is None:
            return
        lots = pos.lots
        if not self._close(day, product, "移仓"):
            return  # 旧仓平不掉则保留，不加新仓
        self._open(day, new_bar, product, lots, "移仓")

    def _rebalance(self, day, product: str, target_lots: int) -> None:
        """§12.4 目标持仓落地：反手先平后开，同向加减仓。"""
        pos = self.positions.get(product)
        current = pos.lots if pos else 0
        delta = int(target_lots) - current
        if delta == 0:
            return
        symbol = pos.symbol if pos else self.exec_symbol(day, product)
        bar = self._bar(day, symbol)
        if bar is None:
            return
        if pos is None:
            self._open(day, bar, product, target_lots, "调仓")
            return
        if current * target_lots <= 0:
            if not self._close(day, product, "调仓"):
                return
            if target_lots:
                self._open(day, bar, product, target_lots, "调仓")
            return

        buy = delta > 0
        if bar["high"] == bar["low"]:
            self._record(day, bar, pos.symbol, "买开" if buy else "卖开", delta,
                         np.nan, 0.0, "调仓", status="一字板")
            return
        if abs(target_lots) < abs(current):
            qty = abs(delta)
            price = self._fill_price(bar, buy)
            fee = qty * float(bar["fee_close"])
            signed_qty = qty if current > 0 else -qty
            self.cash += (price - pos.entry_price) * pos.volume_multiple * signed_qty - fee
            pos.lots = current - signed_qty
            self._record(day, bar, pos.symbol, "买平" if buy else "卖平", qty, price, fee, "调仓")
            if pos.lots == 0:
                del self.positions[product]
            return

        price = self._fill_price(bar, buy)
        fee = abs(delta) * float(bar["fee_open"])
        self.cash -= fee
        total = abs(current) + abs(delta)
        pos.entry_price = (pos.entry_price * abs(current) + price * abs(delta)) / total
        pos.lots = current + delta
        self._record(day, bar, pos.symbol, "买开" if buy else "卖开", delta, price, fee, "调仓")

    # ------------------------------------------------------------------ 结算
    def _settle(self, day) -> None:
        prices, rates = {}, {}
        for pos in self.positions.values():
            bar = self._bar(day, pos.symbol)
            if bar is not None and np.isfinite(bar["close"]):
                prices[pos.symbol] = float(bar["close"])
                rates[pos.symbol] = float(bar["margin_rate"])
        equity = self.equity(prices)
        margin = self._margin(prices, rates)
        self.peak_equity = max(self.peak_equity, equity)
        drawdown = 1.0 - equity / self.peak_equity if self.peak_equity > 0 else 0.0
        risk_ratio = margin / equity if equity > 0 else float("inf")

        breaker = 0
        if drawdown >= self.params.backtest.level2_dd:
            breaker = 2
        elif drawdown >= self.params.backtest.level1_dd:
            breaker = 1

        reason = None
        if equity < self.params.backtest.floor_frac * self.params.backtest.capital_cny:
            self.terminal = True
            reason = "终止"
        elif breaker == 2:
            reason = "熔断"
        elif risk_ratio >= self.params.backtest.force_close_ratio:
            reason = "强平"
        if reason and self.positions:
            self.pending_flatten = reason

        self.rows.append({
            "trade_date": pd.Timestamp(day), "equity": equity, "cash": self.cash,
            "margin": margin, "risk_ratio": risk_ratio, "dd": drawdown,
            "breaker_level": breaker, "n_positions": len(self.positions),
        })
        for pos in self.positions.values():
            self.rows_positions.append({
                "trade_date": pd.Timestamp(day), "product": pos.product, "symbol": pos.symbol,
                "lots": pos.lots, "entry_price": pos.entry_price,
                "stop_price": pos.stop_price,
                "margin_cny": abs(pos.lots) * prices.get(pos.symbol, pos.entry_price)
                * pos.volume_multiple * rates.get(pos.symbol, 0.0),
            })

    def run(self, signals: Dict) -> Result:
        """按交易日推进；``signals`` 为 ``{trade_date: {product: 目标带符号手数}}``。"""
        self.rows_positions: List[dict] = []
        days = sorted(pd.to_datetime(self.bars["trade_date"]).unique())
        ledger = {(pd.Timestamp(day), product): lots
                  for day, per_product in signals.items() for product, lots in per_product.items()}

        for day in days:
            day = pd.Timestamp(day)
            if self.terminal:
                self._settle(day)
                continue
            if self.pending_flatten:
                reason, self.pending_flatten = self.pending_flatten, None
                extra = self.params.backtest.force_close_extra_ticks if reason == "强平" else 0.0
                for product in list(self.positions):
                    self._close(day, product, reason, extra_ticks=extra)
                if reason == "熔断":
                    self.cooldown = self.params.backtest.cooldown_days
            elif self.cooldown > 0:
                self.cooldown -= 1
            else:
                for product in list(self.positions):
                    self._check_stop(day, product)
                for product in list(self.positions):
                    self._roll(day, product)
                for (target_day, product), lots in ledger.items():
                    if target_day == day:
                        self._rebalance(day, product, lots)
            self._settle(day)

        return Result(
            equity=pd.DataFrame(self.rows).set_index("trade_date"),
            trades=pd.DataFrame(self.trades),
            positions=pd.DataFrame(self.rows_positions),
        )
