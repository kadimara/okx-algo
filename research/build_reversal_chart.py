"""Reversal study chart (clean slate): BTC 1h candles with the 4-candle swing structure and every reversal (MSS) on it.

- Swings: 4-candle pivots (highest / lowest of 4 candles on each side), legs under REV_MIN_LEG % merged (the leg
  structure of build_structure_chart.py with LEG_N = 4); labels HH / HL / LH / LL with hindsight (final legs).
- Reversals: every MSS of the live 4-candle structure (a close through the structural level against the trend on a
  powerful candle: body >= REV_POWER x ATR; no hindsight). Area: from the extreme of the move before it (the lowest low / highest high since the previous opposite
  MSS) to the MSS candle, between that extreme and the broken level.
- After each reversal: the move in its direction until the next opposite MSS (max high / min low vs the MSS close).
Writes research/reversal_chart.html from research/template_reversal.html.

Usage: python research/build_reversal_chart.py
"""
import ast
import bisect
import heapq
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
import numpy as np
import pandas as pd
from structure import analyze_bos_structure, bos_pivot_list

REV_N, REV_MIN_LEG, DAYS = 4, 1.0, 730
# REV_POWER: a MSS (break against the trend) only counts on a powerful candle: its body in the break direction is at
# least REV_POWER x ATR(14) (Wilder, as of the candle before). 0 = any close beyond the level. MSBs are unchanged.
REV_POWER = 1.5

src = (HERE / "build_structure_chart.py").read_text(encoding="utf-8")
ns = {"analyze_bos_structure": analyze_bos_structure, "bos_pivot_list": bos_pivot_list, "pd": pd, "np": np,
      "bisect": bisect, "heapq": heapq, "_MEMO": {}}
for node in ast.parse(src).body:
    names = {getattr(e, "id", None) for t in getattr(node, "targets", []) for e in getattr(t, "elts", [t])}
    if isinstance(node, ast.FunctionDef) or names & {"LEG_N", "STRUCT_MODE", "SUPPLY_MIN_PCT"}:
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)

files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
df = pd.read_csv(files[-1], index_col=0, parse_dates=True)
df = df[df.index >= df.index[-1] - pd.Timedelta(days=DAYS)]
ns["ts"] = ts = lambda t: int(pd.Timestamp(t).timestamp())
ns["T"] = T = [ts(t) for t in df.index]
ns["t_idx"] = {t: i for i, t in enumerate(T)}
H, L, C = (df[k].to_numpy(float) for k in ("high", "low", "close"))
O = df["open"].to_numpy(float)
if REV_POWER:
    atr = ns["wilder_atr"](df)
    a_prev = np.r_[atr[0], atr[:-1]]
    ns["BREAK_POW"] = ((C - O) >= REV_POWER * a_prev, (O - C) >= REV_POWER * a_prev)

trend, breaks, _ = ns["live_structure"](df, REV_N, REV_MIN_LEG)
bias = ns["bias_bar"](trend, breaks)
legs, _, _, _ = ns["leg_swings"](df, REV_N, REV_MIN_LEG)

mss = [b for b in breaks if b[3] != 0 and b[1] != b[3]]           # [candle, direction, level, trend before]
rev, prev = [], None
for q, (i, d, level, _) in enumerate(mss):
    a = prev if prev is not None else max(0, i - 200)
    e = a + int(np.argmin(L[a:i + 1]) if d > 0 else np.argmax(H[a:i + 1]))
    nxt = mss[q + 1][0] if q + 1 < len(mss) else len(df) - 1
    seg_end = max(nxt, i + 1)
    follow = (H[i:seg_end + 1].max() / C[i] - 1) * 100 if d > 0 else (1 - L[i:seg_end + 1].min() / C[i]) * 100
    rev.append([T[e], T[i], round(float(L[e] if d > 0 else H[e]), 1), round(float(level), 1), d, round(float(follow), 1),
                round(float(abs(C[i] / (L[e] if d > 0 else H[e]) - 1) * 100), 1)])
    prev = i

# Zones at the reversals (the main chart's zone rules): the candle of the extreme before each MSS (the bottom of a reversal
# up = demand, the top of a reversal down = supply); a candle taller than 1 % only its wick, at least 0.5 % tall. Shown
# from the MSS candle; ends at the first candle that trades back into it (the fill), that trades through it, or at the
# next opposite MSS. Trade (the main chart's entry plan): entry at the zone edge, stop beyond the zone (at least 0.5 %),
# target the extreme of the move between the zone candle and the fill (at least 2R, else no trade); a candle hitting
# both stop and target is a loss; R after 0.05 % fee per side. With TRAIL (default) a trailing stop replaces the target.
WIDE_ZONE_, MIN_ZONE_, MIN_STOP_, MIN_RR_, FEE_ = 1.0, 0.5, 0.5, 2.0, 0.05
# TRAIL: no fixed target; the stop trails behind the structure (REV_N-candle swing lows for longs, highs for shorts)
TRAIL = True
_lmin, _hmax = df["low"].rolling(REV_N).min(), df["high"].rolling(REV_N).max()
piv_lo = ((df["low"] < _lmin.shift(1)) & (df["low"] <= _lmin.shift(-REV_N))).to_numpy()
piv_hi = ((df["high"] > _hmax.shift(1)) & (df["high"] >= _hmax.shift(-REV_N))).to_numpy()
zones = []
for q, (i, d, level, _) in enumerate(mss):
    t0 = rev[q][0]; e = ns["t_idx"][t0]
    nxt = mss[q + 1][0] if q + 1 < len(mss) else len(df)
    Hs, Ls, Os, Cs = (H, L, O, C) if d > 0 else (-L, -H, -O, -C)       # longs; supply mirrored
    top, bot = Hs[e], Ls[e]
    if (top - bot) / abs(bot) * 100 > WIDE_ZONE_:
        top = max(min(Os[e], Cs[e]), bot + MIN_ZONE_ / 100 * abs(bot))
    end, f = None, None
    for k in range(i + 1, min(nxt + 1, len(df))):
        if k == nxt:
            end = k; break
        if Ls[k] <= top:
            end = f = k; break
    trade = None
    if f is not None:
        x = top; risk = max(x - bot, MIN_STOP_ / 100 * abs(x))
        fee = 2 * FEE_ / (risk / abs(x) * 100)
        if TRAIL:
            # trailing stop: from the zone stop, moved to every higher confirmed REV_N-candle swing low after the entry
            # (mirrored for supply); exit when the stop is hit (checked before the stop moves at that close)
            piv = piv_lo if d > 0 else piv_hi
            stop = x - risk
            for k in range(f, len(df)):
                if Ls[k] <= stop:
                    rr = (stop - x) / risk
                    trade = ["win" if rr - fee > 0 else "loss", T[f], T[k], round(rr - fee, 2)]; break
                p = k - REV_N
                if p > f and piv[p] and Ls[p] > stop:
                    stop = Ls[p]
        else:
            tgt = Hs[e + 1:f].max()
            rr = (tgt - x) / risk
            if rr >= MIN_RR_:
                for k in range(f, len(df)):
                    if Ls[k] <= x - risk:
                        trade = ["loss", T[f], T[k], round(-1 - fee, 2)]; break
                    if k > f and Hs[k] >= tgt:
                        trade = ["win", T[f], T[k], round(rr - fee, 2)]; break
    zt, zb = (top, bot) if d > 0 else (-bot, -top)
    zones.append([t0, T[i], None if end is None else T[end], round(float(zt), 1), round(float(zb), 1), d, trade])

data = {"zones": zones, "candles": [[T[k], *(round(float(v), 1) for v in r)] for k, r in enumerate(df[["open", "high", "low", "close"]].to_numpy())],
        "legs": legs, "bias": [float(b) for b in bias], "rev": rev, "n": REV_N, "minLeg": REV_MIN_LEG, "power": REV_POWER}
page = (HERE / "template_reversal.html").read_text(encoding="utf-8").replace("__DATA__", json.dumps(data, separators=(",", ":")))
(HERE / "reversal_chart.html").write_text(page, encoding="utf-8")
up = [r for r in rev if r[4] > 0]; dn = [r for r in rev if r[4] < 0]
for d_, name in ((1, "demand (longs)"), (-1, "supply (shorts)")):
    tr_ = [z[6] for z in zones if z[5] == d_ and z[6]]
    if tr_:
        net = np.array([t[3] for t in tr_])
        print(f"{name}: {sum(z[5] == d_ for z in zones)} zones, {len(tr_)} trades, win {np.mean(net > 0) * 100:.0f}%, total {net.sum():+.1f}R, avg {net.mean():+.2f}R")
print(f"{len(df)} candles, {len(legs)} 4-candle leg swings, {len(up)} reversals up / {len(dn)} down "
      f"(median follow-through {np.median([r[5] for r in up]):.1f}% / {np.median([r[5] for r in dn]):.1f}%), {len(page) / 1e6:.2f} MB")
