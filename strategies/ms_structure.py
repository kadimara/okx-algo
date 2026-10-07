"""
Market structure strategy - the same rules as the "MS strategy" Pine script.

  Structure: swings of swing_days (in candles of the timeframe), HH/HL/LH/LL
             labels and break of structure (BOS), see analyze_bos_structure().
  Trend:     bullish after HH + HL or a BOS up, bearish after LH + LL or a BOS down.
  Long:      in a bullish trend, buy limit at the latest HL candle's body bottom
             (lower of open / close) + entry_pct %; stop stop_pct % below the HL low;
             target the newest swing high's body top (higher of open / close)
             - target_pct %. Right after a BOS up, until the next swing high is
             confirmed, the target is the swing high before the broken one.
  Short:     mirrored (LH body top - entry_pct %, stop above the LH high, target
             the newest swing low's body bottom + target_pct %).
  Filters:   a setup needs entry beyond the stop, target beyond the entry, price
             on the right side of the entry, and a potential reward:risk of at
             least min_rr (0 = off).

analyze() returns one row per candle with BOTH plans for the next candle
(l_* for long, s_* for short) and whether each is a valid setup. The engines add
what depends on the trades actually taken: one trade per HL / LH (the *_id
columns identify the pivot), position sizing, break-even and the trailing stop.
"""
import numpy as np
import pandas as pd

from structure import analyze_bos_structure, candles_for


class MSStructure:
    name = "ms_structure"

    def __init__(self, timeframe_seconds: int, swing_days: float = 2.0,
                 entry_pct: float = 0.35, stop_pct: float = 0.15, target_pct: float = 0.0,
                 min_rr: float = 3.0, allow_long: bool = True, allow_short: bool = True):
        self.n = candles_for(swing_days, timeframe_seconds)
        self.entry = entry_pct / 100
        self.stop = stop_pct / 100
        self.tgt = target_pct / 100
        self.min_rr = min_rr
        self.allow_long, self.allow_short = allow_long, allow_short
        self.entry_pct, self.stop_pct, self.target_pct = entry_pct, stop_pct, target_pct

    def describe(self) -> str:
        sides = "long+short" if self.allow_long and self.allow_short else \
                "long only" if self.allow_long else "short only"
        rr = f"min {self.min_rr}R" if self.min_rr > 0 else "no R filter"
        return (f"MS structure (swing {self.n}, entry {self.entry_pct}%, stop {self.stop_pct}%, "
                f"target offset {self.target_pct}%, {rr}, {sides})")

    @property
    def warmup_candles(self) -> int:
        """Candles needed before the first possible setup (two swings each side)."""
        return 4 * self.n + 1

    def analyze(self, candles: pd.DataFrame) -> pd.DataFrame:
        st = analyze_bos_structure(candles, self.n)
        close = candles["close"].to_numpy(dtype=float)
        trend = st["trend"].to_numpy()
        hb, lb = st["high_body"].to_numpy(), st["low_body"].to_numpy()

        l_entry = lb * (1 + self.entry)
        l_stop = st["last_low"].to_numpy() * (1 - self.stop)
        l_tgt = np.where(st["bos_long"], st["bos_long_tgt"], hb) * (1 - self.tgt)
        s_entry = hb * (1 - self.entry)
        s_stop = st["last_high"].to_numpy() * (1 + self.stop)
        s_tgt = np.where(st["bos_short"], st["bos_short_tgt"], lb) * (1 + self.tgt)

        with np.errstate(invalid="ignore", divide="ignore"):
            l_rr = (l_tgt - l_entry) / (l_entry - l_stop)
            s_rr = (s_entry - s_tgt) / (s_stop - s_entry)
            long_ok = (self.allow_long & (trend == "bullish") & (l_entry > l_stop) & (l_tgt > l_entry)
                       & (close > l_entry) & (l_rr >= self.min_rr))
            short_ok = (self.allow_short & (trend == "bearish") & (s_stop > s_entry) & (s_tgt < s_entry)
                        & (close < s_entry) & (s_rr >= self.min_rr))

        st["close"] = close
        st["l_entry"], st["l_stop"], st["l_target"], st["l_rr"] = l_entry, l_stop, l_tgt, l_rr
        st["s_entry"], st["s_stop"], st["s_target"], st["s_rr"] = s_entry, s_stop, s_tgt, s_rr
        st["l_id"], st["s_id"] = st["last_low_idx"], st["last_high_idx"]
        st["long_ok"], st["short_ok"] = long_ok, short_ok
        return st
