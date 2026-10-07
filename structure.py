"""
Market structure, ported rule for rule from ms_vwap_strategy.pine.

  1. Swing size n (candles). Swing high: the candle n bars ago has a high
     STRICTLY above the n candles before it and >= the n candles after it
     (lows mirrored). It is only known n candles later, at its confirmation candle.
  2. Alternation: a new high while the last pivot was a high replaces it only if
     higher (lows: only if lower); otherwise it's ignored. If a high and a low
     are confirmed on the same candle, the high goes first.
  3. Labels: HH if strictly above the previous high, else LH; LL if strictly
     below the previous low, else HL.
  4. Structure trend: bullish = HH + HL, bearish = LH + LL, otherwise mixed.
  5. Anchored VWAP (ohlc4 by default):
       "swing_low": anchored at every newly confirmed swing low.
       "trend":     last LL in a bullish trend, last HH in a bearish one. The
                    trend is the structure trend, or anchor_trend if given (the
                    strategy passes its SMA trend).
     Like Pine, at most 5000 candles are summed.

Everything is causal: row i only uses candles up to i, so the backtest sees
exactly what the live bot would have seen at that candle's close.
"""
import math
from typing import Optional

import numpy as np
import pandas as pd

PINE_MAX_BACK = 4999


def candles_for(days: float, timeframe_seconds: int) -> int:
    """Candles in `days` for a timeframe, rounded like Pine's math.round, minimum 1."""
    return max(1, int(math.floor(days * 86400.0 / timeframe_seconds + 0.5)))


def wilder_atr(df: pd.DataFrame, length: int = 14) -> np.ndarray:
    """Same as Pine's ta.atr: RMA of true range, seeded with an SMA."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1).to_numpy()
    atr = np.full(len(tr), np.nan)
    if len(tr) >= length:
        atr[length - 1] = tr[:length].mean()
        for i in range(length, len(tr)):
            atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length
    return atr


def find_pivots(df: pd.DataFrame, n: int):
    """Boolean arrays: is the candle at index p a swing high / swing low (rule 1)."""
    high, low = df["high"], df["low"]
    hmax, lmin = high.rolling(n).max(), low.rolling(n).min()
    ph = (high > hmax.shift(1)) & (high >= hmax.shift(-n))
    pl = (low < lmin.shift(1)) & (low <= lmin.shift(-n))
    return ph.to_numpy(), pl.to_numpy()


def source_series(df: pd.DataFrame, source: str) -> np.ndarray:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    return {"ohlc4": (o + h + l + c) / 4, "hlc3": (h + l + c) / 3,
            "hl2": (h + l) / 2, "close": c}[source]


def analyze_structure(df: pd.DataFrame, n: int, atr_len: int = 14,
                      anchor_mode: str = "swing_low", source: str = "ohlc4",
                      anchor_trend: Optional[np.ndarray] = None) -> pd.DataFrame:
    """Per-candle structure state. df needs open, high, low, close, volume."""
    size = len(df)
    ph, pl = find_pivots(df, n)
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    src = source_series(df, source)
    vol = np.nan_to_num(df["volume"].to_numpy(dtype=float), nan=0.0)
    cpv = np.concatenate([[0.0], np.cumsum(src * vol)])   # cpv[k] = sum of the first k candles
    cv = np.concatenate([[0.0], np.cumsum(vol)])

    out = {k: np.full(size, np.nan) for k in ("last_high", "last_low", "avwap", "anchor_idx")}
    out.update({k: np.full(size, "", dtype=object) for k in ("structure_trend", "high_tag", "low_tag")})
    for k in ("conf_high", "conf_low", "reanchor"):
        out[k] = np.zeros(size, dtype=bool)

    last_high = last_low = prev_high = prev_low = math.nan
    last_high_b = last_low_b = last_hh_b = last_ll_b = None
    high_tag = low_tag = ""
    last_type = 0
    anchor = vwap_start = None

    for i in range(size):
        conf_h = conf_l = False
        p = i - n
        if p >= 0 and ph[p] and (last_type != 1 or high[p] > last_high):
            if last_type != 1:
                prev_high = last_high
            last_high, last_high_b, last_type, conf_h = high[p], p, 1, True
            high_tag = "H" if math.isnan(prev_high) else ("HH" if last_high > prev_high else "LH")
            if high_tag == "HH":
                last_hh_b = p
        if p >= 0 and pl[p] and (last_type != -1 or low[p] < last_low):
            if last_type != -1:
                prev_low = last_low
            last_low, last_low_b, last_type, conf_l = low[p], p, -1, True
            low_tag = "L" if math.isnan(prev_low) else ("LL" if last_low < prev_low else "HL")
            if low_tag == "LL":
                last_ll_b = p

        if high_tag == "HH" and low_tag == "HL":
            s_trend = "bullish"
        elif high_tag == "LH" and low_tag == "LL":
            s_trend = "bearish"
        elif high_tag == "" or low_tag == "":
            s_trend = "none"
        else:
            s_trend = "mixed"

        new_anchor = anchor
        a_trend = s_trend if anchor_trend is None else anchor_trend[i]
        if anchor_mode == "trend":
            if a_trend == "bullish" and last_ll_b is not None:
                new_anchor = last_ll_b
            elif a_trend == "bearish" and last_hh_b is not None:
                new_anchor = last_hh_b
        elif conf_l:
            new_anchor = last_low_b
        reanchor = new_anchor is not None and new_anchor != anchor
        if reanchor:
            anchor = new_anchor
            vwap_start = max(anchor, i - PINE_MAX_BACK)
        if vwap_start is not None:
            sv = cv[i + 1] - cv[vwap_start]
            out["avwap"][i] = (cpv[i + 1] - cpv[vwap_start]) / sv if sv > 0 else np.nan

        out["last_high"][i], out["last_low"][i] = last_high, last_low
        out["anchor_idx"][i] = np.nan if anchor is None else anchor
        out["structure_trend"][i], out["high_tag"][i], out["low_tag"][i] = s_trend, high_tag, low_tag
        out["conf_high"][i], out["conf_low"][i], out["reanchor"][i] = conf_h, conf_l, reanchor

    return pd.DataFrame(out, index=df.index)


def pivot_list(df: pd.DataFrame, state: pd.DataFrame, n: int) -> pd.DataFrame:
    """Confirmed pivots as a table (pivot time, type, label, price, confirmed at),
    keeping only the final version of each swing."""
    rows = []
    for i in np.flatnonzero(state["conf_high"].to_numpy() | state["conf_low"].to_numpy()):
        for kind in ("high", "low"):
            if state[f"conf_{kind}"].iloc[i]:
                rows.append({"pivot_time": df.index[i - n], "type": kind,
                             "label": state[f"{kind}_tag"].iloc[i],
                             "price": state[f"last_{kind}"].iloc[i], "confirmed_at": df.index[i]})
    piv = pd.DataFrame(rows)
    if piv.empty:
        return piv
    return piv[piv["type"] != piv["type"].shift(-1)].reset_index(drop=True)


# =============================================================================
# Market structure with break of structure (BOS), ported rule for rule from
# ms_strategy.pine (the "MS strategy" TradingView script).
#
#   1-3. Pivots, alternation and HH/HL/LH/LL labels as above. For every swing the
#        body edges of its candle are kept too: body top = max(open, close),
#        body bottom = min(open, close).
#   4.   Trend: set to bullish when a confirmation leaves HH + HL, to bearish when
#        it leaves LH + LL; any other combination keeps the previous trend.
#   5.   BOS down: the first candle that CLOSES below the last swing low turns the
#        trend bearish at once. If no swing high was confirmed since that low, the
#        highest candle since it (the most recent one on a tie) becomes the new
#        swing high right away (labelled LH/HH). BOS up is mirrored.
#   6.   BOS targets: a BOS down stores the body bottom of the swing low BEFORE the
#        broken one (short target until the next swing low is confirmed); a BOS
#        up stores the body top of the swing high before the broken one.
# =============================================================================
def analyze_bos_structure(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Per-candle structure state at each candle's close (causal)."""
    size = len(df)
    ph, pl = find_pivots(df, n)
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    nan = math.nan

    f_cols = ("last_high", "last_low", "high_body", "low_body", "bos_long_tgt", "bos_short_tgt",
              "bos_level")
    i_cols = ("last_high_idx", "last_low_idx", "bos_from_idx")
    b_cols = ("conf_high", "conf_low", "bos_long", "bos_short", "bos_up", "bos_down")
    s_cols = ("trend", "high_tag", "low_tag")
    out = {k: np.full(size, nan) for k in f_cols}
    out.update({k: np.full(size, -1, dtype=np.int64) for k in i_cols})
    out.update({k: np.zeros(size, dtype=bool) for k in b_cols})
    out.update({k: np.full(size, "", dtype=object) for k in s_cols})

    last_high = prev_high = last_low = prev_low = nan
    last_high_b = last_low_b = -1
    high_body = prev_high_body = low_body = prev_low_body = nan
    high_tag = low_tag = ""
    last_type = 0
    low_broken = high_broken = False
    bos_l = bos_s = False
    bos_l_tgt = bos_s_tgt = nan
    trend = "neutral"
    events = []   # (pivot_idx, confirmed_idx, type, label, price, how)

    def tag_high(price, prev):
        return "H" if math.isnan(prev) else ("HH" if price > prev else "LH")

    def tag_low(price, prev):
        return "L" if math.isnan(prev) else ("LL" if price < prev else "HL")

    for i in range(size):
        conf_h = conf_l = False
        p = i - n
        # ---- confirmed swing high (rule 1-3)
        if p >= 0 and ph[p] and (last_type != 1 or h[p] > last_high):
            if last_type != 1:
                prev_high, prev_high_body = last_high, high_body
            last_high, last_high_b = h[p], p
            high_body = max(o[p], c[p])
            high_broken, last_type, conf_h = False, 1, True
            high_tag = tag_high(last_high, prev_high)
            events.append((p, i, "high", high_tag, last_high, "swing"))
        # ---- confirmed swing low
        if p >= 0 and pl[p] and (last_type != -1 or l[p] < last_low):
            if last_type != -1:
                prev_low, prev_low_body = last_low, low_body
            last_low, last_low_b = l[p], p
            low_body = min(o[p], c[p])
            low_broken, last_type, conf_l = False, -1, True
            low_tag = tag_low(last_low, prev_low)
            events.append((p, i, "low", low_tag, last_low, "swing"))

        # ---- trend from the labels (rule 4)
        if conf_h or conf_l:
            if high_tag == "HH" and low_tag == "HL":
                trend = "bullish"
            elif high_tag == "LH" and low_tag == "LL":
                trend = "bearish"
        # a newly confirmed swing ends the BOS setup on that side
        if conf_h:
            bos_l = False
        if conf_l:
            bos_s = False

        # ---- BOS down (rule 5)
        if not low_broken and not math.isnan(last_low) and c[i] < last_low:
            low_broken, trend = True, "bearish"
            out["bos_down"][i], out["bos_from_idx"][i], out["bos_level"][i] = True, last_low_b, last_low
            bos_l, bos_s, bos_s_tgt = False, True, prev_low_body
            if last_type == -1:
                length = min(i - last_low_b, PINE_MAX_BACK)
                hb, hp = i, h[i]
                for k in range(length):
                    if h[i - k] > hp:
                        hp, hb = h[i - k], i - k
                prev_high, prev_high_body = last_high, high_body
                last_high, last_high_b = hp, hb
                high_body = max(o[hb], c[hb])
                high_broken, last_type = False, 1
                high_tag = tag_high(hp, prev_high)
                events.append((hb, i, "high", high_tag, hp, "bos"))

        # ---- BOS up (mirrored)
        if not high_broken and not math.isnan(last_high) and c[i] > last_high:
            high_broken, trend = True, "bullish"
            out["bos_up"][i], out["bos_from_idx"][i], out["bos_level"][i] = True, last_high_b, last_high
            bos_s, bos_l, bos_l_tgt = False, True, prev_high_body
            if last_type == 1:
                length = min(i - last_high_b, PINE_MAX_BACK)
                lb, lp = i, l[i]
                for k in range(length):
                    if l[i - k] < lp:
                        lp, lb = l[i - k], i - k
                prev_low, prev_low_body = last_low, low_body
                last_low, last_low_b = lp, lb
                low_body = min(o[lb], c[lb])
                low_broken, last_type = False, -1
                low_tag = tag_low(lp, prev_low)
                events.append((lb, i, "low", low_tag, lp, "bos"))

        out["last_high"][i], out["last_low"][i] = last_high, last_low
        out["last_high_idx"][i], out["last_low_idx"][i] = last_high_b, last_low_b
        out["high_body"][i], out["low_body"][i] = high_body, low_body
        out["bos_long"][i], out["bos_short"][i] = bos_l, bos_s
        out["bos_long_tgt"][i], out["bos_short_tgt"][i] = bos_l_tgt, bos_s_tgt
        out["conf_high"][i], out["conf_low"][i] = conf_h, conf_l
        out["trend"][i], out["high_tag"][i], out["low_tag"][i] = trend, high_tag, low_tag

    state = pd.DataFrame(out, index=df.index)
    state.attrs["events"] = events
    return state


def bos_pivot_list(df: pd.DataFrame, state: pd.DataFrame) -> pd.DataFrame:
    """The swings drawn on the chart (pivot time, type, label, price, confirmed at, how).
    A swing replaced by a more extreme one in the same leg is dropped, like the
    label that Pine deletes."""
    ev = state.attrs.get("events", [])
    if not ev:
        return pd.DataFrame()
    piv = pd.DataFrame(ev, columns=["pivot_idx", "conf_idx", "type", "label", "price", "how"])
    piv = piv[piv["type"] != piv["type"].shift(-1)].reset_index(drop=True)
    piv.insert(0, "pivot_time", df.index[piv["pivot_idx"].to_numpy()])
    piv.insert(5, "confirmed_at", df.index[piv["conf_idx"].to_numpy()])
    return piv.drop(columns=["pivot_idx", "conf_idx"])
