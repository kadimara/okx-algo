"""
Moving-average crossover strategy.

BUY  when the fast average crosses ABOVE the slow average.
SELL when the fast average crosses BELOW the slow average.
"""
from typing import Optional

import pandas as pd

from .base import BUY, SELL, Strategy


class MACrossover(Strategy):
    name = "ma_crossover"

    def __init__(self, fast: int = 9, slow: int = 21, ma_type: str = "sma"):
        ma_type = ma_type.lower()
        if fast < 1 or slow < 2:
            raise ValueError("fast must be >= 1 and slow must be >= 2")
        if fast >= slow:
            raise ValueError("fast must be smaller than slow")
        if ma_type not in ("sma", "ema"):
            raise ValueError("ma_type must be 'sma' or 'ema'")
        self.fast, self.slow, self.ma_type = fast, slow, ma_type

    @property
    def lookback(self) -> int:
        # SMA: `slow` candles for the current value + 1 for the previous value.
        # EMA depends on all history; 4x the length makes older candles negligible.
        return self.slow + 1 if self.ma_type == "sma" else self.slow * 4 + 1

    def _ma(self, series: pd.Series, length: int) -> pd.Series:
        if self.ma_type == "sma":
            return series.rolling(length).mean()
        return series.ewm(span=length, adjust=False).mean()

    def generate_signal(self, candles: pd.DataFrame) -> Optional[str]:
        if len(candles) < self.lookback:
            return None
        close = candles["close"]
        diff = self._ma(close, self.fast) - self._ma(close, self.slow)
        prev, cur = diff.iloc[-2], diff.iloc[-1]
        if pd.isna(prev) or pd.isna(cur):
            return None
        if prev <= 0 < cur:
            return BUY
        if prev >= 0 > cur:
            return SELL
        return None

    def describe(self) -> str:
        return f"MA crossover ({self.ma_type.upper()} {self.fast}/{self.slow})"
