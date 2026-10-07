"""
Market structure VWAP strategy - the same rules as ms_vwap_strategy.pine.

  Trend:   weekly SMA of the close (length = candles in sma_days); bullish if the
           SMA is above its value slope_days ago, bearish if below.
  Anchor:  "swing_low" = every newly confirmed swing low (swing size = swing_days
           of candles); "trend" = last LL in a bullish trend, last HH in a bearish one.
  Setup:   bullish -> buy limit at the anchored VWAP (ohlc4) while price is above it;
           bearish -> sell-short limit at the VWAP while price is below it.
  Levels:  stop = stop_atr x ATR from the entry, target = target_r x that distance.

The engines add what depends on the trades actually taken: the anti-chop gate
(TradeGate below) and the break-even stop move.

analyze() returns one row per candle with the plan for the NEXT candle
(side, entry, stop, target, risk), before the anti-chop gate.
"""
import math

import numpy as np
import pandas as pd

from structure import analyze_structure, candles_for, wilder_atr


class MSVWAP:
    name = "ms_vwap"

    def __init__(self, timeframe_seconds: int, swing_days: float = 7.0, atr_len: int = 14,
                 anchor_mode: str = "swing_low", sma_days: float = 7.0, slope_days: float = 1.25,
                 stop_atr: float = 2.0, target_r: float = 4.0,
                 allow_long: bool = True, allow_short: bool = True):
        self.n = candles_for(swing_days, timeframe_seconds)
        self.sma_len = candles_for(sma_days, timeframe_seconds)
        self.slope_len = candles_for(slope_days, timeframe_seconds)
        self.atr_len, self.anchor_mode = atr_len, anchor_mode
        self.stop_atr, self.target_r = stop_atr, target_r
        self.allow_long, self.allow_short = allow_long, allow_short

    def describe(self) -> str:
        sides = "long+short" if self.allow_long and self.allow_short else \
                "long only" if self.allow_long else "short only"
        return (f"MS VWAP (swing {self.n}, SMA {self.sma_len}/slope {self.slope_len}, "
                f"anchor {self.anchor_mode}, stop {self.stop_atr} ATR, target {self.target_r}R, {sides})")

    @property
    def warmup_candles(self) -> int:
        """Candles needed before the first possible setup (SMA + slope, pivots)."""
        return max(self.sma_len + self.slope_len, 2 * self.n) + 1

    def sma_trend(self, close: pd.Series) -> np.ndarray:
        sma = close.rolling(self.sma_len).mean()
        past = sma.shift(self.slope_len)
        return np.where(sma > past, "bullish", np.where(sma < past, "bearish", "none")).astype(object)

    def analyze(self, candles: pd.DataFrame) -> pd.DataFrame:
        trend = self.sma_trend(candles["close"])
        st = analyze_structure(candles, self.n, self.atr_len, self.anchor_mode, "ohlc4",
                               anchor_trend=trend)
        st["trend"] = trend
        st["atr"] = wilder_atr(candles, self.atr_len)
        close = candles["close"].to_numpy()
        vwap = st["avwap"].to_numpy()
        risk = self.stop_atr * st["atr"].to_numpy()
        have = (~np.isnan(vwap) & ~np.isnan(st["last_high"].to_numpy())
                & ~np.isnan(st["last_low"].to_numpy()) & ~st["reanchor"].to_numpy() & ~np.isnan(risk))
        with np.errstate(invalid="ignore"):
            long_ok = self.allow_long & (trend == "bullish") & have & (close > vwap)
            short_ok = self.allow_short & (trend == "bearish") & have & (close < vwap)
        any_ok = long_ok | short_ok
        sign = np.where(long_ok, 1.0, np.where(short_ok, -1.0, np.nan))
        st["side"] = np.where(long_ok, "long", np.where(short_ok, "short", ""))
        st["entry"] = np.where(any_ok, vwap, np.nan)
        st["stop"] = vwap - sign * risk
        st["target"] = vwap + sign * self.target_r * risk
        st["risk"] = np.where(any_ok, risk, np.nan)
        st["close"] = close
        return st


class TradeGate:
    """Anti-chop rules, shared by the backtest and the live bot (same order as Pine).

    After a trade closes, no new order until a candle closes re_arm_atr x ATR
    beyond the VWAP on the trend side; at most max_per_trend entries per VWAP
    anchor (the counter resets when the VWAP re-anchors).
    """

    def __init__(self, re_arm_atr: float = 2.0, max_per_trend: int = 2):
        self.re_arm_atr, self.max_per_trend = re_arm_atr, max_per_trend
        self.armed = True
        self.trades_total = 0
        self.trades_at_anchor = 0

    def on_entry(self):
        self.trades_total += 1

    def update(self, reanchor: bool, trade_closed: bool, trend: str, close: float,
               avwap: float, atr: float) -> bool:
        """Call once per candle close; returns whether a new order is allowed."""
        if reanchor:
            self.armed = True
            self.trades_at_anchor = self.trades_total
        if trade_closed:
            self.armed = self.re_arm_atr == 0
        if not self.armed and not math.isnan(avwap) and not math.isnan(atr):
            if (trend == "bullish" and close >= avwap + self.re_arm_atr * atr) or \
               (trend == "bearish" and close <= avwap - self.re_arm_atr * atr):
                self.armed = True
        return self.can_trade

    @property
    def trades_this_trend(self) -> int:
        return self.trades_total - self.trades_at_anchor

    @property
    def can_trade(self) -> bool:
        return self.armed and (self.max_per_trend == 0 or self.trades_this_trend < self.max_per_trend)

    def to_dict(self) -> dict:
        return {"armed": self.armed, "trades_total": self.trades_total,
                "trades_at_anchor": self.trades_at_anchor}

    def load(self, d: dict):
        self.armed = d.get("armed", True)
        self.trades_total = d.get("trades_total", 0)
        self.trades_at_anchor = d.get("trades_at_anchor", 0)
