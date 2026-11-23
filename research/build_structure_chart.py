"""Build the fresh BTC Market Structure page: 4h candles, 2-day swings and the LTF trend strip only.

Usage: python build_chart.py   (run with the okx-algo venv; writes btc-market-structure.html next to this file)
"""
import bisect
import json
import sys
from pathlib import Path

REPO = str(Path(__file__).resolve().parent.parent)   # the okx-algo repo root
sys.path.insert(0, REPO)
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd

from run_backtest_ms import build_parser, make_strategy
from structure import bos_pivot_list

HERE = Path(__file__).parent
TF, TFH, SWING = "1h", 1, 48   # 2-day swings
files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
if not files:
    raise SystemExit("No BTC 1h index data in data/ - run research/download_data.py first")
sig = pd.read_csv(files[-1], index_col=0, parse_dates=True)   # newest download
DAYS = 730   # show the last 2 years only (keeps the page small and the build fast)
sig = sig[sig.index >= sig.index[-1] - pd.Timedelta(days=DAYS)]
strat = make_strategy(build_parser().parse_args(["--timeframe", TF, "--swing-candles", str(SWING)]))
plan = strat.analyze(sig)

ts = lambda t: int(pd.Timestamp(t).timestamp())
T = [ts(t) for t in sig.index]
o, h, l, c = (sig[k].to_numpy(float) for k in ("open", "high", "low", "close"))
trend = plan["trend"].to_numpy()

candles = [[T[i], round(o[i], 1), round(h[i], 1), round(l[i], 1), round(c[i], 1)] for i in range(len(sig))]
ltf = [{"bullish": 1, "bearish": -1}.get(x, 0) for x in trend]
swings = [[ts(r.pivot_time), r.type[0], r.label, round(float(r.price), 1), ts(r.confirmed_at), r.how == "bos"]
          for r in bos_pivot_list(sig, plan).itertuples()]

# MSS areas of a lower structure: for each MSS of a smaller swing size, the area from the extreme the
# move started at (lowest low since the broken swing high for a bullish MSS) to the broken level,
# spanning from that extreme candle to the candle that closed through. Several lower swing sizes in hours.
from structure import analyze_bos_structure  # noqa: E402

LOWER_HOURS = (6, 12, 24)
lower = {}
for hrs in LOWER_HOURS:
    nl = max(1, round(hrs / TFH))
    sl = analyze_bos_structure(sig, nl)
    areas = []
    for i in range(len(sig)):
        for d, col in ((1, "choch_up"), (-1, "choch_down")):
            if not sl[col].iat[i]:
                continue
            if ltf[i] != d:          # only MSSs in the direction of the 2-day trend (end of a pullback)
                continue
            p = int(sl["bos_from_idx"].iat[i])
            seg = slice(p, i + 1)
            e = p + int(l[seg].argmin() if d == 1 else h[seg].argmax())
            areas.append([T[e], T[i], round(float(sl["bos_level"].iat[i]), 1), round(float(l[e] if d == 1 else h[e]), 1), d, T[p]])
    lower[str(hrs)] = areas

# Leg swings (matched to a hand-labelled TradingView chart): the 8-candle break structure, then the smallest
# leg under MIN_LEG % is removed again and again (the pair goes, the more extreme neighbour of each side stays)
# until every leg is at least MIN_LEG %. The last leg is still forming and is never removed.
LEG_N, MIN_LEG = 8, 2.5


def base_swings(df, n):
    """The 8-candle break-structure swings: [pivot time, "h" | "l", price, confirmed time]."""
    base = bos_pivot_list(df, analyze_bos_structure(df, n))
    return [[ts(r.pivot_time), r.type[0], float(r.price), ts(r.confirmed_at)] for r in base.itertuples()]


def swing_events(df, n):
    """Every swing as it was confirmed, in confirmation order, including the ones a more extreme swing of the same
    type replaced later (base_swings has dropped those, which is hindsight): [pivot time, "h" | "l", price, confirmed]."""
    ev = analyze_bos_structure(df, n).attrs.get("events", [])
    return [[T[pi], ty[0], float(price), T[ci]] for pi, ci, ty, _, price, _ in ev]


def merge_legs(P, min_leg):
    """Remove the smallest leg under min_leg % again and again; the first and last swing always stay."""
    while True:
        small = [(abs(P[i + 1][2] - P[i][2]) / P[i][2] * 100, i) for i in range(1, len(P) - 2)]
        small = [s for s in small if s[0] < min_leg]
        if not small:
            return P
        i = min(small)[1]
        merged = []
        for p in P[:i] + P[i + 2:]:
            if merged and merged[-1][1] == p[1]:
                if (p[2] > merged[-1][2]) == (p[1] == "h"):
                    merged[-1] = p
            else:
                merged.append(p)
        P = merged


def structure(close, P, i0, i1):
    """The leg structure on swings P over candles i0..i1-1, read like the hand-labelled chart (a close, not a wick,
    breaks a level):
    - a close below the structural low is a bearish break: the highest swing high since that low was set becomes
      the structural LH, the leg's next swing low the new LL, and the first swing high after that LL is taken
      as the LH right away, so the range to break is [LL, LH];
    - swings inside the range that break nothing are internal. Bullish mirrored.
    A swing is used from its pivot candle on, so this only has no hindsight when every swing in P is confirmed."""
    at = {t_idx[p[0]]: k for k, p in enumerate(P)}
    hi = lo = None                # indices into P of the structural high / low (None = waiting for the leg's extreme)
    need, trend, keep, brk = None, 0, set(), i0
    breaks = []                   # every close through a structural level: [candle index, direction, level, trend before]
    bias = [0] * (i1 - i0)        # trend at each candle's close
    since_hi = since_lo = None    # lowest swing low since hi was set / highest swing high since lo was set
    for i in range(i0, i1):
        if hi is not None and close[i] > P[hi][2]:          # bullish break
            breaks.append([i, 1, P[hi][2], trend])
            # the HL / LL the break started from: the lowest swing low since the broken high
            cand = [x for x in (since_hi, lo if lo is not None and lo > hi else None) if x is not None]
            if cand:
                new = min(cand, key=lambda x: P[x][2])
                if new != lo and lo is not None and lo > hi:
                    keep.discard(lo)                        # a lower low in the same pullback replaces it
                lo = new
                keep.add(lo)
            trend, hi, need, since_lo, brk = 1, None, None, None, i
        elif lo is not None and close[i] < P[lo][2]:        # bearish break
            breaks.append([i, -1, P[lo][2], trend])
            cand = [x for x in (since_lo, hi if hi is not None and hi > lo else None) if x is not None]
            if cand:
                new = max(cand, key=lambda x: P[x][2])
                if new != hi and hi is not None and hi > lo:
                    keep.discard(hi)                        # a higher high in the same pullback replaces it
                hi = new
                keep.add(hi)
            trend, lo, need, since_hi, brk = -1, None, None, None, i
        bias[i - i0] = trend
        k = at.get(i)
        if k is None:
            continue
        ty, price = P[k][1], P[k][2]
        if ty == "h":
            if hi is None or need == "h":                   # the leg's high after a bullish break, or the first LH
                hi, since_hi = k, None
                keep.add(k)
                need = "l" if trend > 0 else None
            elif since_lo is None or price > P[since_lo][2]:
                since_lo = k
        else:
            if lo is None or need == "l":
                lo, since_lo = k, None
                keep.add(k)
                need = "h" if trend < 0 else None
            elif since_hi is None or price < P[since_hi][2]:
                since_hi = k
    return {"hi": hi, "lo": lo, "trend": trend, "keep": keep, "brk": brk, "breaks": breaks, "bias": bias}


def leg_swings(df, n, min_leg):
    """The leg swings and structure as drawn now, with hindsight (labels, current MSS / MSB)."""
    P = merge_legs(base_swings(df, n), min_leg)
    st = structure(df["close"].to_numpy(float), P, 0, len(df))
    hi, lo, trend, keep, brk = st["hi"], st["lo"], st["trend"], st["keep"], st["brk"]
    # MSB = the structural level in the trend direction, MSS = the one against it
    mss, msb = (hi, lo) if trend < 0 else (lo, hi)
    mss = [P[mss][0], round(P[mss][2], 1)]
    if msb is None:                                         # the leg after the last break has no swing yet
        seg = df["low" if trend < 0 else "high"].to_numpy(float)[brk:]
        j = brk + int(seg.argmin() if trend < 0 else seg.argmax())
        msb = [T[j], round(float(seg[j - brk]), 1)]
    else:
        msb = [P[msb][0], round(P[msb][2], 1)]
    out, last = [], {}
    for k, (t, ty, price, conf) in enumerate(P):
        prev = last.get(ty)
        lab = ("HH" if prev is None or price > prev else "LH") if ty == "h" else ("LL" if prev is None or price < prev else "HL")
        if k in keep:
            last[ty] = price
        out.append([t, ty, lab, round(price, 1), conf, k not in keep])
    levels = {"trend": trend, "mss": mss, "msb": msb}
    return out, levels, st["bias"], st["breaks"]


def live_structure(df, n, min_leg, window=120, low_start=False):
    """Replays the leg structure close by close without hindsight: each time swings are confirmed, the legs are
    rebuilt from the last `window` confirmed swings only and the structure runs on until the next confirmation.
    Returns per candle the trend at its close, the breaks ([candle, direction, level, trend before]) and the swing
    start (pivot time of the latest settled leg high: one with at least two leg swings after it). With low_start also
    the same for the latest settled leg low (for the demand zones), as a fourth value."""
    close = df["close"].to_numpy(float)
    ev = swing_events(df, n)
    conf_i = [t_idx[p[3]] for p in ev]                      # ev is in confirmation order
    events = sorted(set(conf_i))
    trend, start, breaks = [0] * len(df), [None] * len(df), []
    start_low = [None] * len(df)
    for e, c in enumerate(events):
        c2 = events[e + 1] if e + 1 < len(events) else len(df)
        known = ev[:bisect.bisect_right(conf_i, c)][-2 * window:]
        # a swing replaced by a more extreme one of the same type (both known by now) is dropped, like base_swings
        known = [p for k, p in enumerate(known) if k == len(known) - 1 or known[k + 1][1] != p[1]]
        P = merge_legs(known[-window:], min_leg)
        i0 = t_idx[P[0][0]]
        st = structure(close, P, i0, c2)
        trend[c:c2] = st["bias"][c - i0:]
        breaks += [b for b in st["breaks"] if b[0] >= c]
        highs = [p[0] for p in P[:-2] if p[1] == "h"]
        start[c:c2] = [highs[-1] if highs else None] * (c2 - c)
        lows = [p[0] for p in P[:-2] if p[1] == "l"]
        start_low[c:c2] = [lows[-1] if lows else None] * (c2 - c)
    for b in breaks:
        b[3] = trend[b[0] - 1]
    return (trend, breaks, start, start_low) if low_start else (trend, breaks, start)


def bias_bar(trend, breaks):
    """The bias bar as seen live: a MSS (break against the confirmed trend) is grey (+-0.5) until the new trend
    makes its first MSB (then it is confirmed) or price breaks back in the old direction (the MSS failed and the
    old trend continues). A reversal is any change of the live trend (a close through the MSS, or the legs being
    rebuilt when a swing is confirmed); a MSB is a close through the level in the trend direction."""
    bar, conf, pend = [0.0] * len(trend), 0, None
    msb = {b[0] for b in breaks if b[1] == b[3]}
    for i in range(1, len(trend)):
        d = trend[i]
        if d != trend[i - 1] and d != 0:
            if d == conf:                                   # back to the confirmed trend: the MSS failed
                pend = None
            else:                                           # a MSS against the confirmed trend
                pend = d
        elif i in msb:
            if pend is not None and d == pend:              # first MSB of the new trend confirms it
                conf, pend = d, None
            elif pend is None:
                conf = d
        bar[i] = pend / 2 if pend is not None else conf
    return bar


t_idx = {t: i for i, t in enumerate(T)}
legs, leg_levels, hind_bias, _ = leg_swings(sig, LEG_N, MIN_LEG)
live_trend, live_breaks, swing_start, swing_start_low = live_structure(sig, LEG_N, MIN_LEG, low_start=True)
leg_bias = bias_bar(live_trend, live_breaks)


# Supply zones: every 8-candle swing high (the base swings of the legs, as confirmed live).  Zone = the full range of the candle that made
# the high. Live, no hindsight: a zone shows from the first close at which (1) its swing high is confirmed, (2) the
# bias bar is bearish (red: a confirmed downtrend, not a grey MSS) and (3) it is still alive. It ends at the first
# candle that trades back into it after price left it (a high below it), at a higher high, when the bias stops being
# bearish, or when a newer leg high settles (the next swing starts: zones only count for the current swing). The box is drawn from the swing-high candle itself.
# Very sharp move down: within SHARP_K candles after the swing-high candle, a low at least SHARP_ATR x ATR(14) (Wilder, as of the
# swing-high candle) below the zone bottom. Only these zones are kept (DRAW_GREY = False drops the others). A sharp zone counts as known from the later of the confirmation and the candle that completes the drop (no hindsight).
# Zones at a LH (the top of a pullback of at least MIN_LEG % after the leg low, below the previous leg high; leg swings known at confirmation)
# get looser rules: no bearish bias needed (shown from confirmation, not ended by the bias) and touches before
# confirmation do not count. The other end rules (touch after it is known, higher high, newer leg high) still apply.
# [tCandle, tEnd (None = still active), top, bottom, sharp (1/0), at a LH (1/0)]
SHARP_K, SHARP_ATR = 4, 2.0
DRAW_GREY = False     # zones without the very sharp drop (grey on the chart): off for now
# A zone candle taller than WIDE_ZONE % becomes its upper wick: from the high down to the body high (max of open and
# close), but at least MIN_ZONE % tall. The sharp-drop test still uses the candle low.
WIDE_ZONE, MIN_ZONE = 1.0, 0.5
# LH_BIAS: zones at a LH also need the bearish live bias (shown only then, ended when it stops); they keep the rule that
# touches before the swing high is confirmed do not count. False = the looser LH rules (no bias needed).
LH_BIAS = True
# MSS_OK: a pending MSS in the zone's direction counts as the right bias (bias bar faded: -0.5 bearish MSS for supply,
# +0.5 bullish MSS for demand), so zones at the start of a reversal are shown before the first MSB confirms it.
MSS_OK = True
# Minimum size of the sharp move in % beyond the zone candle (on top of SHARP_ATR x ATR): in quiet markets 2 ATR can be
# only ~1 %, which does not look sharp. 0 = ATR only.
SUPPLY_MIN_PCT, DEMAND_MIN_PCT = 0.0, 1.5


def wilder_atr(df, length=14):
    H, L, C = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    tr = np.maximum(H, np.r_[C[0], C[:-1]]) - np.minimum(L, np.r_[C[0], C[:-1]])
    return pd.Series(tr).ewm(alpha=1 / length, adjust=False).mean().to_numpy()


def is_lower_high(ev, conf_i, t, price, c):
    """Zone at the high: in the leg swings known at the close of candle c, the last leg swing before the swing high
    at t is a low (the high is the top of the pullback after the low) and the leg high before that is higher (a LH)."""
    known = ev[:bisect.bisect_right(conf_i, c)][-240:]
    known = [p for k, p in enumerate(known) if k == len(known) - 1 or known[k + 1][1] != p[1]]
    prev = [p for p in merge_legs(known[-120:], MIN_LEG) if p[0] < t]
    if not prev or prev[-1][1] != "l" or (price / prev[-1][2] - 1) * 100 < MIN_LEG:
        return False                                           # no full leg (>= MIN_LEG %) up from the low into it
    highs = [p for p in prev if p[1] == "h"]
    return bool(highs) and price < highs[-1][2]


def supply_zones(df, n, bias, start):
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = wilder_atr(df)
    ev = swing_events(df, n)
    conf_i = [t_idx[p[3]] for p in ev]
    out = []
    for t, ty, price, conf in ev:
        if ty != "h":
            continue
        j, c = t_idx[t], t_idx[conf]
        lh = is_lower_high(ev, conf_i, t, price, c)            # looser rules for a zone at a LH (see above)
        top, bot = H[j], L[j]
        hit = np.flatnonzero(L[j + 1:j + 1 + SHARP_K] <= min(bot - SHARP_ATR * atr[j], bot * (1 - SUPPLY_MIN_PCT / 100)))
        sharp = hit.size > 0                                   # else drawn grey
        if sharp:
            c = max(c, j + 1 + int(hit[0]))                    # a sharp zone is only known once the drop has happened
        if (top - bot) / bot * 100 > WIDE_ZONE:                # a wide candle: the zone is its upper wick
            bot = min(max(O[j], C[j]), top / (1 + MIN_ZONE / 100))
        shown, end = zone_life(H, j, c, top, bot, t, bias, start, lh, need_bias=not lh or LH_BIAS)
        if shown is not None and (sharp or DRAW_GREY):
            out.append([T[j], None if end is None else T[end], round(top, 1), round(bot, 1), int(sharp), int(lh)])
    return out


def zone_life(H, j, c, top, bot, t, bias, start, lh=False, need_bias=True):
    """When a zone at candle j (known from the close of candle c) is shown and ends: (shown, end) candle indices, None
    when it is never shown / still active. See the supply-zone rules above. lh: touches before c do not count;
    need_bias: shown only with a bearish live bias and ended when the bias stops being bearish."""
    left, shown = False, None
    for k in range(j + 1, len(H)):
        if H[k] > top or (left and H[k] >= bot) or (start[k] is not None and start[k] > t) \
                or (shown is not None and need_bias and bias[k] > -(0.5 if MSS_OK else 1)):
            return shown, k
        if not lh or k >= c:                                   # at a LH, touches before it is known do not count
            left = left or H[k] < bot
        if shown is None and k >= c and (not need_bias or bias[k] <= -(0.5 if MSS_OK else 1)):
            shown = k
    return shown, None


supply = supply_zones(sig, LEG_N, leg_bias, swing_start)


# Demand zones: the supply-zone rules mirrored for longs. Every 8-candle swing low; zone = the full range of that candle
# (a candle taller than WIDE_ZONE % only its lower wick: from the low up to the body low, at least MIN_ZONE % tall).
# Very sharp move up: within SHARP_K candles a high at least SHARP_ATR x ATR and DEMAND_MIN_PCT % above the candle high (else not kept, like
# DRAW_GREY). Shown from the first close it is known with a bullish live bias (bias bar green) and before price traded
# back into it; ended at the first candle that trades back into it after price left it (a low above it), a lower low,
# when the bias stops being bullish, or when a newer leg low settles. Zones at a HL (the bottom of a pullback of at
# least MIN_LEG % down from the leg high, above the previous leg low): touches before the confirmation do not count,
# and with LH_BIAS they need the bias like the others.
# [tCandle, tEnd (None = still active), top, bottom, sharp (1/0), at a HL (1/0)]


def is_higher_low(ev, conf_i, t, price, c):
    """Mirror of is_lower_high: the last leg swing before the swing low at t is a high at least MIN_LEG % above it,
    and the leg low before that is lower (a HL)."""
    known = ev[:bisect.bisect_right(conf_i, c)][-240:]
    known = [p for k, p in enumerate(known) if k == len(known) - 1 or known[k + 1][1] != p[1]]
    prev = [p for p in merge_legs(known[-120:], MIN_LEG) if p[0] < t]
    if not prev or prev[-1][1] != "h" or (1 - price / prev[-1][2]) * 100 < MIN_LEG:
        return False
    lows = [p for p in prev if p[1] == "l"]
    return bool(lows) and price > lows[-1][2]


def demand_life(L, j, c, top, bot, t, bias, start, hl=False, need_bias=True):
    """Mirror of zone_life for a demand zone: (shown, end) candle indices."""
    left, shown = False, None
    for k in range(j + 1, len(L)):
        if L[k] < bot or (left and L[k] <= top) or (start[k] is not None and start[k] > t) \
                or (shown is not None and need_bias and bias[k] < (0.5 if MSS_OK else 1)):
            return shown, k
        if not hl or k >= c:
            left = left or L[k] > top
        if shown is None and k >= c and (not need_bias or bias[k] >= (0.5 if MSS_OK else 1)):
            shown = k
    return shown, None


def demand_zones(df, n, bias, start):
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = wilder_atr(df)
    ev = swing_events(df, n)
    conf_i = [t_idx[p[3]] for p in ev]
    out = []
    for t, ty, price, conf in ev:
        if ty != "l":
            continue
        j, c = t_idx[t], t_idx[conf]
        hl = is_higher_low(ev, conf_i, t, price, c)
        top, bot = H[j], L[j]
        hit = np.flatnonzero(H[j + 1:j + 1 + SHARP_K] >= max(top + SHARP_ATR * atr[j], top * (1 + DEMAND_MIN_PCT / 100)))
        sharp = hit.size > 0
        if sharp:
            c = max(c, j + 1 + int(hit[0]))
        if (top - bot) / bot * 100 > WIDE_ZONE:                # a wide candle: the zone is its lower wick
            top = max(min(O[j], C[j]), bot * (1 + MIN_ZONE / 100))
        shown, end = demand_life(L, j, c, top, bot, t, bias, start, hl, need_bias=not hl or LH_BIAS)
        if shown is not None and (sharp or DRAW_GREY):
            out.append([T[j], None if end is None else T[end], round(top, 1), round(bot, 1), int(sharp), int(hl)])
    return out


demand = demand_zones(sig, LEG_N, leg_bias, swing_start_low)


# Retest zones (a level tested twice, no sharp move needed): a new 8-candle swing low within RETEST_PCT % of an earlier
# swing low from the last RETEST_DAYS days, with a bounce of at least RETEST_BOUNCE % between them and no low below the
# level in between. Zone = from the lower of the two lows up to the higher body low of the two candles, at least
# MIN_ZONE % and at most WIDE_ZONE % tall, drawn from the first test. Known when the second low is confirmed; shown,
# ended and filtered like a demand zone (bullish bias or bullish MSS, ended by a touch after price left it, a lower
# low, the bias turning or a newer leg low). Supply mirrored: two swing highs.
# [tSecondTest, tEnd (None = still active), top, bottom, 1, 0, tFirstTest]
RETEST_PCT, RETEST_BOUNCE, RETEST_DAYS = 0.5, 1.5, 10


def retest_zones(df, n, bias, start, side):
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    body_lo, body_hi = np.minimum(O, C), np.maximum(O, C)
    pts = [(t_idx[p[0]], t_idx[p[3]], float(p[2])) for p in swing_events(df, n) if p[1] == ("l" if side > 0 else "h")]
    span, out = RETEST_DAYS * 24 / TFH, []
    for b, (j2, c2, p2) in enumerate(pts):
        for j1, c1, p1 in reversed(pts[:b]):
            if j1 >= j2 or c1 > c2 or j2 - j1 > span or abs(p2 / p1 - 1) * 100 > RETEST_PCT:
                continue
            if side > 0:
                bot = min(p1, p2)
                if L[j1 + 1:j2].min() < bot or H[j1 + 1:j2].max() < max(p1, p2) * (1 + RETEST_BOUNCE / 100):
                    continue
                top = min(max(body_lo[j1], body_lo[j2]), bot * (1 + WIDE_ZONE / 100))
                top = max(top, bot * (1 + MIN_ZONE / 100))
                shown, end = demand_life(L, j2, c2, top, bot, T[j2], bias, start)
            else:
                top = max(p1, p2)
                if H[j1 + 1:j2].max() > top or L[j1 + 1:j2].min() > min(p1, p2) * (1 - RETEST_BOUNCE / 100):
                    continue
                bot = max(min(body_hi[j1], body_hi[j2]), top / (1 + WIDE_ZONE / 100))
                bot = min(bot, top / (1 + MIN_ZONE / 100))
                shown, end = zone_life(H, j2, c2, top, bot, T[j2], bias, start)
            if shown is not None:
                out.append([T[j2], None if end is None else T[end], round(top, 1), round(bot, 1), 1, 0, T[j1]])
            break                                              # the most recent earlier swing that qualifies
    return out


demand_retest = retest_zones(sig, LEG_N, leg_bias, swing_start_low, 1)
supply_retest = retest_zones(sig, LEG_N, leg_bias, swing_start, -1)


data = {"supply": supply, "demand": demand, "demandRetest": demand_retest, "supplyRetest": supply_retest, "tfh": TFH, "n": strat.n, "candles": candles, "ltf": ltf, "swings": swings, "lower": lower,
        "legs": legs, "legLevels": leg_levels, "legBias": [float(b) for b in leg_bias],   # bias bar: the live bias (+-1 trend, +-0.5 pending MSS), as the zones use it
        "legN": LEG_N, "minLeg": MIN_LEG}
page = (HERE / "template_structure.html").read_text(encoding="utf-8").replace(
    "__DATA__", json.dumps(data, separators=(",", ":")))
(HERE / "ms_structure_chart.html").write_text(page, encoding="utf-8")
print({k: len(v) for k, v in lower.items()}, "lower MSS areas;", f"{sum(z[4] for z in supply)} sharp + {sum(1 - z[4] for z in supply)} grey supply zones ({sum(z[1] is None for z in supply if z[4])} / {sum(z[1] is None for z in supply if not z[4])} active),", f"{len(demand)} demand zones, retest zones {len(demand_retest)} demand / {len(supply_retest)} supply,", f"{len(candles)} candles, {len(swings)} swings, {len(legs)} leg swings, swing {strat.n} candles, {len(page) / 1e6:.2f} MB")
