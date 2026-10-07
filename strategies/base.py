"""
Template that every strategy follows.

A strategy only DECIDES. It receives a table of closed candles (oldest first,
newest last) and returns BUY, SELL or None. It never places orders itself:
the engines (backtester.py and okx_trader.py) do that. Because both engines
call the same strategy, the logic you backtest is the logic that trades.
"""
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

BUY = "buy"
SELL = "sell"


class Strategy(ABC):
    name: str = "unnamed"

    @property
    @abstractmethod
    def lookback(self) -> int:
        """How many closed candles the strategy needs for one decision.
        Both engines pass exactly this many, so live and backtest see the same data."""

    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame) -> Optional[str]:
        """candles: columns open, high, low, close, volume, indexed by time.
        Return BUY, SELL, or None (do nothing)."""

    def describe(self) -> str:
        return self.name
