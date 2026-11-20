"""
Market structure strategy - the same rules as the "MS strategy" Pine script.

  Structure: swings of swing_days (in candles of the timeframe), HH/HL/LH/LL
             labels, break of structure (BOS) and CHoCH, see analyze_bos_structure().
  Trend:     bullish after HH + HL or a break up, bearish after LH + LL or a break down.
  HTF trend: the same structure with htf_days swings and no internal swings.
  Setups:    only a swing confirmed by a break after it is traded: the HL behind a
             break up (long), the LH behind a break down (short). Continuation =
             that break was a BOS (in the trend direction), pullback end = it was a
             CHoCH (it turned the trend). Continuation can be limited to the first
             (or first two) BOS since the trend turned. Each can be switched off per side, and
             each side can require the HTF trend in its direction.
  Long:      in a bullish trend, buy limit at the HL candle's body bottom
             (lower of open / close) + entry_pct %; stop stop_pct % below the HL low;
             target the newest swing high's body top (higher of open / close)
             - target_pct %. Right after a break up, until the next swing high is
             confirmed, the target is the higher of the broken high and the one before.
  Short:     mirrored (LH body top - entry_pct %, stop above the LH high, target
             the newest swing low's body bottom + target_pct %).
  Filters:   a setup needs entry beyond the stop, target beyond the entry, price
             on the right side of the entry, and a potential reward:risk of at
             least min_rr (0 = off). Every setting is separate for longs and shorts.

analyze() returns one row per candle with BOTH plans for the next candle
(l_* for long, s_* for short) and whether each is a valid setup. The engines add
what depends on the trades actually taken: one trade per HL / LH (the *_id
columns identify the pivot), position sizing, break-even and the trailing stop.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from structure import analyze_bos_structure, candles_for


@dataclass
class SideParams:
    enabled: bool = True
    entry_pct: float = 0.35
    stop_pct: float = 0.0
    target_pct: float = 0.1
    min_rr: float = 2.0
    cont: str = "all"       # continuation setups (BOS): none / first / first2 / all
    choch: bool = True      # pullback end setup (CHoCH)
    htf: bool = True        # only with the HTF trend
    rr_filter: bool = True  # False (long Pine script): no min R, and the target only has to be
    use_target: bool = False  # beyond the entry when the take-profit is used
    htf_target: bool = False  # with the HTF filter: target the last HTF swing (long Pine script)
    min_stop_pct: float = 0.0  # skip setups with a stop closer than this % (0 = off)

    def describe(self) -> str:
        if not self.enabled:
            return "off"
        bos = {"none": "", "first": "BOS 1", "first2": "BOS 1-2", "all": "BOS"}[self.cont]
        setups = "+".join(s for s in (bos, "CHoCH" if self.choch else "") if s) or "no setups"
        rr = f"min {self.min_rr}R" if self.rr_filter and self.min_rr > 0 else "no R filter"
        extra = (", with HTF" + (" (HTF target)" if self.htf_target else "") if self.htf else "") + \
            (f", min stop {self.min_stop_pct}%" if self.min_stop_pct > 0 else "")
        return (f"{setups}, entry {self.entry_pct}%, stop {self.stop_pct}%, "
                f"target offset {self.target_pct}%, {rr}{extra}")


def bos_ok(mode: str, num: np.ndarray) -> np.ndarray:
    """Which continuation breaks are traded, by their BOS number."""
    return {"none": num < 0, "first": num == 1, "first2": num <= 2, "all": num >= 0}[mode]


def target_ok(side: SideParams, beyond: np.ndarray, rr: np.ndarray) -> np.ndarray:
    """R filter: target beyond the entry and at least min_rr away. Without it (long Pine
    script) the target only has to be beyond the entry when the take-profit is used."""
    if side.rr_filter:
        return beyond & (rr >= side.min_rr)
    return beyond if side.use_target else np.ones_like(beyond)


class MSStructure:
    name = "ms_structure"

    def __init__(self, timeframe_seconds: int, swing_days: float = 2.0, htf_days: float = 7.0,
                 swing_candles: int = None, htf_candles: int = None,
                 use_internal: bool = True, long: SideParams = None, short: SideParams = None):
        # swing sizes in candles (the long / short Pine scripts) or in days (the combined one)
        self.n = swing_candles or candles_for(swing_days, timeframe_seconds)
        self.n2 = htf_candles or candles_for(htf_days, timeframe_seconds)
        self.swing_days, self.htf_days = swing_days, htf_days
        self.use_internal = use_internal
        self.long = long or SideParams()
        self.short = short or SideParams()

    def describe(self) -> str:
        internal = "" if self.use_internal else ", no internal swings"
        return (f"MS structure (swing {self.n}, HTF {self.n2}{internal} | "
                f"long: {self.long.describe()} | short: {self.short.describe()})")

    @property
    def warmup_candles(self) -> int:
        """Candles needed before the first possible setup (two swings each side)."""
        return 4 * max(self.n, self.n2) + 1

    def analyze(self, candles: pd.DataFrame) -> pd.DataFrame:
        st = analyze_bos_structure(candles, self.n, self.use_internal)
        htf_st = analyze_bos_structure(candles, self.n2, use_internal=False)
        htf = htf_st["trend"].to_numpy()
        htf_high, htf_low = htf_st["last_high"].to_numpy(), htf_st["last_low"].to_numpy()
        close = candles["close"].to_numpy(dtype=float)
        trend = st["trend"].to_numpy()
        hb, lb = st["high_body"].to_numpy(), st["low_body"].to_numpy()
        L, S = self.long, self.short

        l_entry = lb * (1 + L.entry_pct / 100)
        l_stop = st["last_low"].to_numpy() * (1 - L.stop_pct / 100)
        l_tgt = np.where(st["bos_long"], st["bos_long_tgt"], hb) * (1 - L.target_pct / 100)
        s_entry = hb * (1 - S.entry_pct / 100)
        s_stop = st["last_high"].to_numpy() * (1 + S.stop_pct / 100)
        s_tgt = np.where(st["bos_short"], st["bos_short_tgt"], lb) * (1 + S.target_pct / 100)
        with np.errstate(invalid="ignore"):
            # following the HTF trend: target the last HTF swing, if it is beyond the entry
            if L.htf and L.htf_target:
                t = htf_high * (1 - L.target_pct / 100)
                l_tgt = np.where((htf == "bullish") & (t > l_entry), t, l_tgt)
            if S.htf and S.htf_target:
                t = htf_low * (1 + S.target_pct / 100)
                s_tgt = np.where((htf == "bearish") & (t < s_entry), t, s_tgt)

        l_id, s_id = st["last_low_idx"].to_numpy(), st["last_high_idx"].to_numpy()
        l_choch, s_choch = st["valid_low_choch"].to_numpy(), st["valid_high_choch"].to_numpy()
        l_num, s_num = st["valid_low_num"].to_numpy(), st["valid_high_num"].to_numpy()
        l_setup_on = np.where(l_choch, L.choch, bos_ok(L.cont, l_num))
        s_setup_on = np.where(s_choch, S.choch, bos_ok(S.cont, s_num))

        with np.errstate(invalid="ignore", divide="ignore"):
            l_rr = (l_tgt - l_entry) / (l_entry - l_stop)
            s_rr = (s_entry - s_tgt) / (s_stop - s_entry)
            long_ok = (L.enabled & (trend == "bullish") & (l_id == st["valid_low_idx"].to_numpy())
                       & l_setup_on & ((not L.htf) | (htf == "bullish"))
                       & (l_entry > l_stop) & (close > l_entry)
                       & ((l_entry - l_stop) / l_entry * 100 >= L.min_stop_pct)
                       & target_ok(L, l_tgt > l_entry, l_rr))
            short_ok = (S.enabled & (trend == "bearish") & (s_id == st["valid_high_idx"].to_numpy())
                        & s_setup_on & ((not S.htf) | (htf == "bearish"))
                        & (s_stop > s_entry) & (close < s_entry)
                        & ((s_stop - s_entry) / s_entry * 100 >= S.min_stop_pct)
                        & target_ok(S, s_tgt < s_entry, s_rr))

        st["close"], st["htf_trend"] = close, htf
        st["l_entry"], st["l_stop"], st["l_target"], st["l_rr"] = l_entry, l_stop, l_tgt, l_rr
        st["s_entry"], st["s_stop"], st["s_target"], st["s_rr"] = s_entry, s_stop, s_tgt, s_rr
        st["l_id"], st["s_id"] = l_id, s_id
        st["l_setup"] = np.where(l_choch, "CHoCH", np.char.add("BOS ", l_num.astype(str)))
        st["s_setup"] = np.where(s_choch, "CHoCH", np.char.add("BOS ", s_num.astype(str)))
        st["long_ok"], st["short_ok"] = long_ok, short_ok
        return st
