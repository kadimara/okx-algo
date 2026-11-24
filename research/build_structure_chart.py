"""Build the fresh BTC Market Structure page: 4h candles, 2-day swings and the LTF trend strip only.

Usage: python build_chart.py   (run with the okx-algo venv; writes btc-market-structure.html next to this file)
"""
import bisect
import heapq
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
    # columns read once as arrays: pandas deep-copies the frame's attrs (the swing events) on every column access,
    # which made this loop take minutes
    col = {k: sl[k].to_numpy() for k in ("choch_up", "choch_down", "bos_from_idx", "bos_level")}
    areas = []
    for i in range(len(sig)):
        for d, name in ((1, "choch_up"), (-1, "choch_down")):
            if not col[name][i]:
                continue
            if ltf[i] != d:          # only MSSs in the direction of the 2-day trend (end of a pullback)
                continue
            p = int(col["bos_from_idx"][i])
            seg = slice(p, i + 1)
            e = p + int(l[seg].argmin() if d == 1 else h[seg].argmax())
            areas.append([T[e], T[i], round(float(col["bos_level"][i]), 1), round(float(l[e] if d == 1 else h[e]), 1), d, T[p]])
    lower[str(hrs)] = areas

# Leg swings (matched to a hand-labelled TradingView chart): the 8-candle break structure, then the smallest
# leg under MIN_LEG % is removed again and again (the pair goes, the more extreme neighbour of each side stays)
# until every leg is at least MIN_LEG %. The last leg is still forming and is never removed.
# ZONE_MERGE_EXTREME: the real-leg check of the zones (ZONE_REAL_LEG) uses legs merged with the most extreme swings kept
# (merge_legs extreme=True), so a zone does not end when its swing briefly disappears; the structure and bias keep the
# normal merge. False / "supply" / True (both sides).
LEG_N, MIN_LEG, ZONE_MERGE_EXTREME = 8, 2.5, "supply"
# STRUCT_MODE: "structural" = breaks of the structural high / low (the levels that turn or continue the trend, see
# structure); "swing" = breaks of the most recent swing high / low (a close above the last swing high is bullish, below
# the last swing low bearish). Both on the same leg swings; MSS / MSB and the bias bar work the same.
STRUCT_MODE = "structural"


def base_swings(df, n):
    """The 8-candle break-structure swings: [pivot time, "h" | "l", price, confirmed time]."""
    base = bos_pivot_list(df, analyze_bos_structure(df, n))
    return [[ts(r.pivot_time), r.type[0], float(r.price), ts(r.confirmed_at)] for r in base.itertuples()]


def swing_events(df, n):
    """Every swing as it was confirmed, in confirmation order, including the ones a more extreme swing of the same
    type replaced later (base_swings has dropped those, which is hindsight): [pivot time, "h" | "l", price, confirmed]."""
    ev = analyze_bos_structure(df, n).attrs.get("events", [])
    return [[T[pi], ty[0], float(price), T[ci]] for pi, ci, ty, _, price, _ in ev]


def more_b(Q, x, y):
    """Is swing x more extreme than swing y (same type): higher for highs, lower for lows."""
    return Q[x][2] > Q[y][2] if Q[x][1] == "h" else Q[x][2] < Q[y][2]


def merge_legs(P, min_leg, extreme=False):
    """Remove the smallest leg under min_leg % again and again (ties: the leftmost); the first and last swing always
    stay. extreme: for l, a, b, r with a -> b the smallest leg, keep the most extreme swings (drop b and r when a is more
    extreme than r, else l and a when b is more extreme than l, else a and b); otherwise a and b are dropped. A heap of
    legs over a linked list (same result as rebuilding the list after each removal, much faster)."""
    Q = []                                         # alternate first: of two swings of the same type the more extreme stays
    for p in P:
        if Q and Q[-1][1] == p[1]:
            if (p[2] > Q[-1][2]) == (p[1] == "h"):
                Q[-1] = p
        else:
            Q.append(p)
    n = len(Q)
    if n < 4:
        return Q
    nxt, prv, alive = list(range(1, n + 1)), list(range(-1, n - 1)), [True] * n
    size = lambda a, b: abs(Q[b][2] - Q[a][2]) / Q[a][2] * 100
    heap = [(size(i, i + 1), i, i + 1) for i in range(1, n - 2) if size(i, i + 1) < min_leg]
    heapq.heapify(heap)
    while heap:
        _, a, b = heapq.heappop(heap)
        if not (alive[a] and alive[b] and nxt[a] == b) or prv[a] < 0 or nxt[b] >= n:
            continue                                # stale, or an end of the leg is the first / last swing
        l, r = prv[a], nxt[b]
        drop = (a, b)
        if extreme:
            if r < n and more_b(Q, a, r):
                drop = (b, r)                       # a stays the leg extreme (a lower high r does not replace it)
            elif prv[l] >= 0 and more_b(Q, b, l):
                drop = (l, a)                       # b is the lower low / higher high: it stays
        x0, x1 = drop
        L_, R_ = prv[x0], nxt[x1]
        alive[x0] = alive[x1] = False
        nxt[L_] = R_
        if R_ < n:
            prv[R_] = L_
        if prv[L_] >= 0 and R_ < n - 1:             # the new leg L_ -> R_
            v = size(L_, R_)
            if v < min_leg:
                heapq.heappush(heap, (v, L_, R_))
    out, i = [], 0
    while i < n:
        out.append(Q[i])
        i = nxt[i]
    return out


def leg_big_enough(prev, price):
    """Is the leg from the last leg swing in prev to price a full leg (>= MIN_LEG %)?"""
    return abs(price / prev[-1][2] - 1) * 100 >= MIN_LEG


def structure(close, P, i0, i1, track=None):
    """The leg structure on swings P over candles i0..i1-1, read like the hand-labelled chart (a close, not a wick,
    breaks a level):
    - a close below the structural low is a bearish break: the highest swing high since that low was set becomes
      the structural LH, the leg's next swing low the new LL, and the first swing high after that LL is taken
      as the LH right away, so the range to break is [LL, LH];
    - swings inside the range that break nothing are internal. Bullish mirrored.
    A swing is used from its pivot candle on, so this only has no hindsight when every swing in P is confirmed.
    With STRUCT_MODE "swing" the levels are simply the most recent swing high and low instead (see swing_structure).
    track: a list that gets (hi, lo, trend, brk) after each candle's breaks (the state at its close)."""
    if STRUCT_MODE == "swing":
        return swing_structure(close, P, i0, i1)
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
        if track is not None:
            track.append((hi, lo, trend, brk))
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


def swing_structure(close, P, i0, i1):
    """Structure on the most recent swings: the levels are the last swing high and the last swing low; a close above the
    last swing high is a bullish break, below the last swing low a bearish one (a broken level is used up until the next
    swing of that type). The broken swings are the structural ones (keep)."""
    at = {t_idx[p[0]]: k for k, p in enumerate(P)}
    hi = lo = None
    trend, keep, brk, breaks, bias = 0, set(), i0, [], [0] * (i1 - i0)
    for i in range(i0, i1):
        if hi is not None and close[i] > P[hi][2]:
            breaks.append([i, 1, P[hi][2], trend])
            trend, brk = 1, i
            keep.add(hi)
            hi = None
        elif lo is not None and close[i] < P[lo][2]:
            breaks.append([i, -1, P[lo][2], trend])
            trend, brk = -1, i
            keep.add(lo)
            lo = None
        bias[i - i0] = trend
        k = at.get(i)
        if k is not None:
            if P[k][1] == "h":
                hi = k
            else:
                lo = k
    return {"hi": hi, "lo": lo, "trend": trend, "keep": keep, "brk": brk, "breaks": breaks, "bias": bias}


def structure_levels(df, P, st, end):
    """The current MSS / MSB of a structure run on swings P up to candle end: MSB = the structural level in the trend
    direction, MSS = the one against it (None when not set)."""
    hi, lo, trend, brk = st["hi"], st["lo"], st["trend"], st["brk"]
    mss, msb = (hi, lo) if trend < 0 else (lo, hi)
    mss = None if mss is None else [P[mss][0], round(P[mss][2], 1)]
    if msb is None:                                         # the leg after the last break has no swing yet
        seg = df["low" if trend < 0 else "high"].to_numpy(float)[brk:end]
        j = brk + int(seg.argmin() if trend < 0 else seg.argmax())
        msb = [T[j], round(float(seg[j - brk]), 1)]
    else:
        msb = [P[msb][0], round(P[msb][2], 1)]
    return {"trend": trend, "mss": mss, "msb": msb}


def label_swings(P, keep):
    """HH / LH / HL / LL for the swings P against the previous swing of the same type; the swings outside keep (they
    broke no structure) are internal: [pivot time, type, label, price, confirmed time, internal]."""
    return relabel([[t, ty, "", round(price, 1), conf, k not in keep] for k, (t, ty, price, conf) in enumerate(P)])


def relabel(rows):
    """Set the label of each swing row [time, type, label, price, ...] (in time order) against the previous swing of the
    same type: HH / LH for highs, HL / LL for lows."""
    last = {}
    for row in rows:
        prev = last.get(row[1])
        row[2] = ("HH" if prev is None or row[3] > prev else "LH") if row[1] == "h" else ("LL" if prev is None or row[3] < prev else "HL")
        last[row[1]] = row[3]
    return rows


def leg_swings(df, n, min_leg):
    """The leg swings and structure as drawn now, with hindsight (labels, current MSS / MSB)."""
    P = merge_legs(base_swings(df, n), min_leg)
    st = structure(df["close"].to_numpy(float), P, 0, len(df))
    return label_swings(P, st["keep"]), structure_levels(df, P, st, len(df)), st["bias"], st["breaks"]


def live_structure(df, n, min_leg, window=120, low_start=False, labels=False):
    """Replays the leg structure close by close without hindsight: each time swings are confirmed, the legs are
    rebuilt from the last `window` confirmed swings only and the structure runs on until the next confirmation.
    Returns per candle the trend at its close, the breaks ([candle, direction, level, trend before]) and the swing
    start (pivot time of the latest settled leg high: one with at least two leg swings after it). With low_start also
    the same for the latest settled leg low (for the demand zones), as a fourth value. With labels also, last, the live
    labels and levels: every swing that was part of the live legs, labelled as in the last rebuild it was part of
    (swings merged away later stay), and the MSS / MSB of the structure at the last candle."""
    close = df["close"].to_numpy(float)
    ev = swing_events(df, n)
    conf_i = [t_idx[p[3]] for p in ev]                      # ev is in confirmation order
    events = sorted(set(conf_i))
    trend, start, breaks = [0] * len(df), [None] * len(df), []
    start_low = [None] * len(df)
    lab, levels, gone = {}, None, set()     # gone: swings a later rebuild merged away (inside its window, not in its legs)
    # The chart's right-edge view (no hindsight): the swing rows of each rebuild, diff encoded against the running list G
    # ([c, n, tail]: G = G[:n] + tail from candle c on), and the MSS / MSB per candle ([time, price] or None).
    G, rb = [], []
    legsets = []                                    # per rebuild: [candle, {(pivot time, type)} of the real leg swings]
    H_, L_ = df["high"].to_numpy(float), df["low"].to_numpy(float)
    lv_mss, lv_msb = [None] * len(df), [None] * len(df)
    for e, c in enumerate(events):
        c2 = events[e + 1] if e + 1 < len(events) else len(df)
        known = ev[:bisect.bisect_right(conf_i, c)][-2 * window:]
        # a swing replaced by a more extreme one of the same type (both known by now) is dropped, like base_swings
        known = [p for k, p in enumerate(known) if k == len(known) - 1 or known[k + 1][1] != p[1]]
        P = merge_legs(known[-window:], min_leg)
        Ps = P
        real = Ps if len(Ps) < 2 or leg_big_enough(Ps[:-1], Ps[-1][2]) else Ps[:-1]
        Px = merge_legs(known[-window:], min_leg, extreme=True) if ZONE_MERGE_EXTREME else Ps
        realx = Px if len(Px) < 2 or leg_big_enough(Px[:-1], Px[-1][2]) else Px[:-1]
        legsets.append((c, {(q[0], q[1]) for q in real}, {(q[0], q[1]) for q in realx}))
        i0 = t_idx[P[0][0]]
        track = [] if labels else None
        st = structure(close, Ps, i0, c2, track)
        trend[c:c2] = st["bias"][c - i0:]
        breaks += [b for b in st["breaks"] if b[0] >= c]
        highs = [p[0] for p in P[:-2] if p[1] == "h"]
        start[c:c2] = [highs[-1] if highs else None] * (c2 - c)
        lows = [p[0] for p in P[:-2] if p[1] == "l"]
        start_low[c:c2] = [lows[-1] if lows else None] * (c2 - c)
        if labels:
            # The first swings of the window are never merged (merge_legs keeps the first swing), so an old swing that
            # reaches the window edge would come back: only the swings from the third one on update the labels.
            edge = P[2][0] if len(P) > 2 and lab else P[0][0]
            cur = {(q[0], q[1]) for q in Ps}
            gone |= {key for key in lab if key[0] >= edge and key not in cur}
            for row in label_swings(Ps, st["keep"]):
                if row[0] >= edge:
                    lab[(row[0], row[1])] = row
                    gone.discard((row[0], row[1]))
            if c2 == len(df):
                levels = structure_levels(df, Ps, st, c2)
            # only the newest 20 swings update the view: older ones only change by the window start (not real news)
            e20 = Ps[max(2, len(Ps) - 20)][0] if G and len(Ps) > 2 else edge
            rows = [[r[0], r[1], r[2], r[3], int(r[5])] for r in label_swings(Ps, st["keep"]) if r[0] >= e20]
            cand = [g for g in G if g[0] < e20] + rows
            k = 0
            while k < min(len(G), len(cand)) and G[k] == cand[k]:
                k += 1
            if k < len(cand) or k < len(G):
                rb.append([T[c], k, cand[k:]])
            G = cand
            ext_b, ext = None, None
            for i in range(c, c2):
                hi_, lo_, tr_, bk = track[i - i0]
                mss, msb = (hi_, lo_) if tr_ < 0 else (lo_, hi_)
                lv_mss[i] = None if mss is None else [Ps[mss][0], round(Ps[mss][2], 1)]
                if msb is not None:
                    lv_msb[i] = [Ps[msb][0], round(Ps[msb][2], 1)]
                else:                                   # the leg after the break has no swing yet: its extreme so far
                    if ext_b != (bk, tr_):
                        ext_b, ext = (bk, tr_), bk
                        for j in range(bk, i + 1):
                            if (L_[j] < L_[ext]) if tr_ < 0 else (H_[j] > H_[ext]):
                                ext = j
                    elif (L_[i] < L_[ext]) if tr_ < 0 else (H_[i] > H_[ext]):
                        ext = i
                    lv_msb[i] = [T[ext], round(float(L_[ext] if tr_ < 0 else H_[ext]), 1)]
    for b in breaks:
        b[3] = trend[b[0] - 1]
    _MEMO[("legsets", n, min_leg, len(df), T[0], T[-1])] = ([x[0] for x in legsets], [x[1] for x in legsets], [x[2] for x in legsets])
    out = (trend, breaks, start, start_low) if low_start else (trend, breaks, start)
    if not labels:
        return out
    # Swings merged away later are no real legs: no label, drawn as a small grey dot (7th field 1), and left out of
    # the label comparison (each label compares with the previous real swing of the same type).
    real = relabel([r for key, r in sorted(lab.items()) if key not in gone])
    merged = [[r[0], r[1], "", r[3], r[4], True, 1] for key, r in sorted(lab.items()) if key in gone]
    return out + ({"legs": sorted(real + merged, key=lambda r: (r[0], r[1])), "levels": levels,
                   "rb": rb, "mss": lv_mss, "msb": lv_msb},)


_MEMO = {}   # leg sets of live_structure and results of is_lower_high / is_higher_low per swing (the backtests ask the same swings for many variants)


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
live_trend, live_breaks, swing_start, swing_start_low, live_lab = live_structure(sig, LEG_N, MIN_LEG, low_start=True, labels=True)
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
# SUPPLY_MSS_OK / DEMAND_MSS_OK: a pending MSS in the zone's direction counts as the right bias (bias bar faded: -0.5 bearish MSS for supply,
# +0.5 bullish MSS for demand), so zones at the start of a reversal are shown before the first MSB confirms it.
SUPPLY_MSS_OK, DEMAND_MSS_OK = True, True   # per side: shorts also in a bearish MSS, longs also in a bullish MSS
# COUNTER_MSS_ENDS: a shown zone ends when the bias leaves its side, also for a pending MSS against it (often from a
# small internal break). False = only a confirmed opposite trend ends it (supply: bias +1, demand: bias -1).
COUNTER_MSS_ENDS = True
# Minimum size of the sharp move in % beyond the zone candle (on top of SHARP_ATR x ATR): in quiet markets 2 ATR can be
# only ~1 %, which does not look sharp. 0 = ATR only.
SUPPLY_MIN_PCT, DEMAND_MIN_PCT, ZONE_REAL_LEG = 0.0, 1.0, True
# ZONE_REAL_LEG: a zone only counts while its swing is a real leg swing of the live structure (in the legs of the latest
# rebuild; the newest swing only once the leg into it is >= MIN_LEG): not shown before, ended when it is merged away.


def wilder_atr(df, length=14):
    H, L, C = (df[k].to_numpy(float) for k in ("high", "low", "close"))
    tr = np.maximum(H, np.r_[C[0], C[:-1]]) - np.minimum(L, np.r_[C[0], C[:-1]])
    return pd.Series(tr).ewm(alpha=1 / length, adjust=False).mean().to_numpy()




def is_lower_high(ev, conf_i, t, price, c):
    key = ("is_lower_high", t, c, len(ev), tuple(ev[-1]) if ev else None)
    if key not in _MEMO:
        _MEMO[key] = _is_lower_high(ev, conf_i, t, price, c)
    return _MEMO[key]


def _is_lower_high(ev, conf_i, t, price, c):
    """Zone at the high: in the leg swings known at the close of candle c, the last leg swing before the swing high
    at t is a low (the high is the top of the pullback after the low) and the leg high before that is higher (a LH)."""
    known = ev[:bisect.bisect_right(conf_i, c)][-240:]
    known = [p for k, p in enumerate(known) if k == len(known) - 1 or known[k + 1][1] != p[1]]
    prev = [p for p in merge_legs(known[-120:], MIN_LEG) if p[0] < t]
    if not prev or prev[-1][1] != "l" or not leg_big_enough(prev, price):
        return False                                           # no full leg (>= MIN_LEG %) up from the low into it
    highs = [p for p in prev if p[1] == "h"]
    return bool(highs) and price < highs[-1][2]


def supply_zones(df, n, bias, start, shown_out=None):
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = wilder_atr(df)
    ev = swing_events(df, n)
    conf_i = [t_idx[p[3]] for p in ev]
    real = leg_ok_fn(df, n) if ZONE_REAL_LEG else None
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
        okf = (lambda k, t=t: real(t, "h", k)) if real else None
        shown, end = zone_life(H, j, c, top, bot, t, bias, start, lh, need_bias=not lh or LH_BIAS, ok=okf)
        if shown is not None and (sharp or DRAW_GREY):
            out.append([T[j], None if end is None else T[end], round(top, 1), round(bot, 1), int(sharp), int(lh)])
            if shown_out is not None:
                shown_out.append(T[shown])
    return out


def leg_ok_fn(df, n):
    """ok(t, ty, k): is the swing (pivot time t, type ty) a real leg swing in the latest live rebuild at or before candle k
    (ZONE_REAL_LEG)?"""
    key = ("legsets", n, MIN_LEG, len(df), T[0], T[-1])
    if key not in _MEMO:
        live_structure(df, n, MIN_LEG)
    cs, sets_n, sets_x = _MEMO[key]

    def ok(t, ty, k):
        sets = sets_x if ZONE_MERGE_EXTREME is True or (ZONE_MERGE_EXTREME == "supply" and ty == "h") else sets_n
        r = bisect.bisect_right(cs, k) - 1
        return r >= 0 and (t, ty) in sets[r]
    return ok


def zone_life(H, j, c, top, bot, t, bias, start, lh=False, need_bias=True, ok=None):
    """When a zone at candle j (known from the close of candle c) is shown and ends: (shown, end) candle indices, None
    when it is never shown / still active. See the supply-zone rules above. lh: touches before c do not count;
    need_bias: shown only with a bearish live bias and ended when the bias stops being bearish; ok(k): its swing is a real
    leg at candle k (ZONE_REAL_LEG; not shown before, ended when not)."""
    left, shown = False, None
    for k in range(j + 1, len(H)):
        if shown is not None and ok is not None and not ok(k):
            return shown, k
        if H[k] > top or (left and H[k] >= bot) or (start[k] is not None and start[k] > t) \
                or (shown is not None and need_bias and (bias[k] > -(0.5 if SUPPLY_MSS_OK else 1) if COUNTER_MSS_ENDS else bias[k] >= 1)):
            return shown, k
        if not lh or k >= c:                                   # at a LH, touches before it is known do not count
            left = left or H[k] < bot
        if shown is None and k >= c and (not need_bias or bias[k] <= -(0.5 if SUPPLY_MSS_OK else 1)) and (ok is None or ok(k)):
            shown = k
    return shown, None


supply_shown = []
supply = supply_zones(sig, LEG_N, leg_bias, swing_start, supply_shown)


# Demand zones: the supply-zone rules mirrored for longs. Every 8-candle swing low; zone = the full range of that candle
# (a candle taller than WIDE_ZONE % only its lower wick: from the low up to the body low, at least MIN_ZONE % tall).
# Very sharp move up: within SHARP_K candles a high at least SHARP_ATR x ATR and DEMAND_MIN_PCT % above the candle high (else not kept, like
# DRAW_GREY). Shown from the first close it is known with a bullish live bias (bias bar green) and before price traded
# back into it; ended at the first candle that trades back into it after price left it (a low above it), a lower low,
# when the bias stops being bullish, or when a newer leg low settles. Zones at a HL (the bottom of a pullback of at
# least MIN_LEG % down from the leg high, above the previous leg low): touches before the confirmation do not count,
# and with LH_BIAS they need the bias like the others.
# [tCandle, tEnd (None = still active), top, bottom, sharp (1/0), at a HL (1/0)]


# Demand zones are only kept at a HL (DEMAND_HL_ONLY) that came after a HH (DEMAND_AFTER_HH: on the 8-candle swings
# known at the confirmation, the swing high between the previous low and the zone's low is above the high before it).
# Supply zones have no such filter (their best trades are at the top of a pullback, a local HH).
DEMAND_HL_ONLY, DEMAND_AFTER_HH = True, False
# Demand-only settings (the grid in backtest_demand.py): a less sharp rise (DEMAND_SHARP_ATR x ATR and DEMAND_MIN_PCT %
# within SHARP_K candles), and a bearish MSS does not end a demand zone (only a confirmed downtrend does).
DEMAND_SHARP_ATR = 1.5
DEMAND_SHARP_K = 4        # candles after the swing low in which the rise must happen
DEMAND_COUNTER_MSS_ENDS = False


def after_hh(ev, conf_i, t, c):
    """Whether, on the 8-candle swings known at the close of candle c (a swing replaced by a more extreme one dropped),
    the swing high just before the swing low at t is higher than the swing high before that (a HH came first)."""
    known = ev[:bisect.bisect_right(conf_i, c)][-40:]
    known = [p for k, p in enumerate(known) if k == len(known) - 1 or known[k + 1][1] != p[1]]
    pos = next((k for k, p in enumerate(known) if p[0] == t and p[1] == "l"), None)
    if pos is None or pos < 3:
        return False
    return known[pos - 1][2] > known[pos - 3][2]


def is_higher_low(ev, conf_i, t, price, c):
    key = ("is_higher_low", t, c, len(ev), tuple(ev[-1]) if ev else None)
    if key not in _MEMO:
        _MEMO[key] = _is_higher_low(ev, conf_i, t, price, c)
    return _MEMO[key]


def _is_higher_low(ev, conf_i, t, price, c):
    """Mirror of is_lower_high: the last leg swing before the swing low at t is a high at least MIN_LEG % above it,
    and the leg low before that is lower (a HL)."""
    known = ev[:bisect.bisect_right(conf_i, c)][-240:]
    known = [p for k, p in enumerate(known) if k == len(known) - 1 or known[k + 1][1] != p[1]]
    prev = [p for p in merge_legs(known[-120:], MIN_LEG) if p[0] < t]
    if not prev or prev[-1][1] != "h" or not leg_big_enough(prev, price):
        return False
    lows = [p for p in prev if p[1] == "l"]
    return bool(lows) and price > lows[-1][2]


def demand_life(L, j, c, top, bot, t, bias, start, hl=False, need_bias=True, ok=None):
    """Mirror of zone_life for a demand zone: (shown, end) candle indices."""
    left, shown = False, None
    for k in range(j + 1, len(L)):
        if shown is not None and ok is not None and not ok(k):
            return shown, k
        if L[k] < bot or (left and L[k] <= top) or (start[k] is not None and start[k] > t) \
                or (shown is not None and need_bias and (bias[k] < (0.5 if DEMAND_MSS_OK else 1) if DEMAND_COUNTER_MSS_ENDS else bias[k] <= -1)):
            return shown, k
        if not hl or k >= c:
            left = left or L[k] > top
        if shown is None and k >= c and (not need_bias or bias[k] >= (0.5 if DEMAND_MSS_OK else 1)) and (ok is None or ok(k)):
            shown = k
    return shown, None


def demand_zones(df, n, bias, start, shown_out=None):
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = wilder_atr(df)
    ev = swing_events(df, n)
    conf_i = [t_idx[p[3]] for p in ev]
    real = leg_ok_fn(df, n) if ZONE_REAL_LEG else None
    out = []
    for t, ty, price, conf in ev:
        if ty != "l":
            continue
        j, c = t_idx[t], t_idx[conf]
        hl = is_higher_low(ev, conf_i, t, price, c)
        ahh = after_hh(ev, conf_i, t, c)
        if (DEMAND_HL_ONLY and not hl) or (DEMAND_AFTER_HH and not ahh):
            continue
        top, bot = H[j], L[j]
        hit = np.flatnonzero(H[j + 1:j + 1 + DEMAND_SHARP_K] >= max(top + DEMAND_SHARP_ATR * atr[j], top * (1 + DEMAND_MIN_PCT / 100)))
        sharp = hit.size > 0
        if sharp:
            c = max(c, j + 1 + int(hit[0]))
        if (top - bot) / bot * 100 > WIDE_ZONE:                # a wide candle: the zone is its lower wick
            top = max(min(O[j], C[j]), bot * (1 + MIN_ZONE / 100))
        okf = (lambda k, t=t: real(t, "l", k)) if real else None
        shown, end = demand_life(L, j, c, top, bot, t, bias, start, hl, need_bias=not hl or LH_BIAS, ok=okf)
        if shown is not None and (sharp or DRAW_GREY):
            out.append([T[j], None if end is None else T[end], round(top, 1), round(bot, 1), int(sharp), int(hl), int(ahh)])
            if shown_out is not None:
                shown_out.append(T[shown])
    return out


demand_shown = []
demand = demand_zones(sig, LEG_N, leg_bias, swing_start_low, demand_shown)



# Trade result per zone (the backtest's entry plan, as in backtest_zone_rules.run with entry "plan" and the drop-low /
# rally-high target): sell at the zone bottom (buy at the top for demand), or at the body edge of the zone candle when that
# stop would be wider than WIDE_STOP %; stop beyond the zone, at least MIN_STOP %; target the extreme of the move between
# the zone candle and the fill, at least MIN_RR R (else no trade). Cancelled before a fill by a newer leg high / low or
# when the bias leaves the zone's side. A candle hitting both stop and target is a loss. R after FEE % per side.
MIN_RR, FEE, MIN_STOP, WIDE_STOP = 2, 0.05, 0.5, 1.0


def zone_trades(df, zones, side, bias, start, lim, keep_mss=False):
    """Per zone [result "win" / "loss" / None (no trade or still open), fill time, exit time, R after fees]."""
    O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    b = np.asarray(bias, float)
    if side == "long":
        H, L, O, C, b = -L, -H, -O, -C, -b
    out = []
    for z in zones:
        t0, t1, top, bot = z[:4]
        if side == "long":
            top, bot = -bot, -top
        res = [None, None, None, None]
        out.append(res)
        if t1 is None:
            continue
        j, e = t_idx[t0], t_idx[t1]
        if H[e] < bot:                                  # ended without a touch
            continue
        x = bot if (top - bot) / abs(bot) * 100 <= WIDE_STOP else max(O[j], C[j])
        risk = max(top - x, MIN_STOP / 100 * abs(x))
        f = None
        for k in range(e, len(df)):
            if H[k] >= x:
                f = k
                break
            if (start[k] is not None and start[k] > t0) or (b[k] >= 1 if keep_mss else b[k] > -lim):
                break
        if f is None:
            continue
        lo = L[j + 1:f].min()
        rr = (x - lo) / risk
        if rr < MIN_RR - 1e-9:
            continue
        for k in range(f, len(df)):
            if H[k] >= x + risk:
                r, kind = -1.0, "loss"
                break
            if k > f and L[k] <= lo:
                r, kind = rr, "win"
                break
        else:
            continue
        res[:] = [kind, T[f], T[k], round(r - 2 * FEE / (risk / abs(x) * 100), 2)]
    return out


def runs(vals):
    """Run-length form of a per-candle list: [[candle index, value], ...] at each change."""
    out = []
    for i, v in enumerate(vals):
        if not out or out[-1][1] != v:
            out.append([i, v])
    return out

data = {"supply": supply, "demand": demand, "tfh": TFH, "n": strat.n, "candles": candles, "ltf": ltf, "swings": swings, "lower": lower,
        # Legs, live: swings, MSS / MSB and zones as the live structure knew them at the right edge of the view (no hindsight)
        "rb": live_lab.get("rb", []), "lvMss": runs(live_lab.get("mss", [])), "lvMsb": runs(live_lab.get("msb", [])),
        "supplyShown": supply_shown, "demandShown": demand_shown, "legLevels": live_lab["levels"],
        "supplyTrades": zone_trades(sig, supply, "short", leg_bias, swing_start, 0.5 if SUPPLY_MSS_OK else 1),
        "demandTrades": zone_trades(sig, demand, "long", leg_bias, swing_start_low, 0.5 if DEMAND_MSS_OK else 1,
                                    keep_mss=not DEMAND_COUNTER_MSS_ENDS),
        "legsHind": legs, "legLevelsHind": leg_levels, "legBias": [float(b) for b in leg_bias],   # bias bar: the live bias (+-1 trend, +-0.5 pending MSS), as the zones use it
        "legN": LEG_N, "minLeg": MIN_LEG}
page = (HERE / "template_structure.html").read_text(encoding="utf-8").replace(
    "__DATA__", json.dumps(data, separators=(",", ":")))
(HERE / "ms_structure_chart.html").write_text(page, encoding="utf-8")
print({k: len(v) for k, v in lower.items()}, "lower MSS areas;", f"{sum(z[4] for z in supply)} sharp + {sum(1 - z[4] for z in supply)} grey supply zones ({sum(z[1] is None for z in supply if z[4])} / {sum(z[1] is None for z in supply if not z[4])} active),", f"{len(demand)} demand zones,", f"{len(candles)} candles, {len(swings)} swings, {len(legs)} leg swings, swing {strat.n} candles, {len(page) / 1e6:.2f} MB")
