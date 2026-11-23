"""Demand zones only (longs): which rules give more long trades without losing the edge. 4-year grid on the newest BTC 1h
index file in data/, with the entry plan of backtest_zone_rules.py (entry at the zone top, stop at the zone bottom at
least MIN_STOP % below, RISK % of the account per trade, fees FEE % per side; conservative fills: on the fill candle only
the stop counts, a candle hitting both stop and target is a loss).

Grid: zone filter (all / at a HL / at a HL after a HH), bias (confirmed uptrend / a bullish MSS too), whether a bearish MSS
ends the zone (yes / only a confirmed downtrend), the sharp rise (SHARP_ATR x ATR and DEMAND_MIN_PCT % within SHARP_K
candles), target (the high of the rally before the fill, at least MIN_RR R / fixed 2R / 3R).
Only results with at least MIN_TRADES trades are ranked: few trades over many combinations is how overfitting looks.

Usage: python research/backtest_demand.py   (okx-algo venv)
"""
import ast
import bisect
import itertools
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd

from structure import analyze_bos_structure, bos_pivot_list

src = (Path(__file__).parent / "build_structure_chart.py").read_text(encoding="utf-8")
ns = {"analyze_bos_structure": analyze_bos_structure, "bos_pivot_list": bos_pivot_list, "pd": pd, "np": np, "bisect": bisect}
SETTINGS = {"LEG_N", "SHARP_K", "DRAW_GREY", "WIDE_ZONE", "LH_BIAS", "SUPPLY_MIN_PCT", "SUPPLY_MSS_OK", "COUNTER_MSS_ENDS",
            "STRUCT_MODE", "DEMAND_HL_ONLY", "DEMAND_SHARP_ATR", "DEMAND_COUNTER_MSS_ENDS"}
for node in ast.parse(src).body:
    names = {getattr(e, "id", None) for t in getattr(node, "targets", []) for e in getattr(t, "elts", [t])}
    if isinstance(node, ast.FunctionDef) or names & SETTINGS:
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)

files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
df = pd.read_csv(files[-1], index_col=0, parse_dates=True)
ns["ts"] = ts = lambda t: int(pd.Timestamp(t).timestamp())
ns["T"] = T = [ts(t) for t in df.index]
ns["t_idx"] = t_idx = {t: i for i, t in enumerate(T)}
ns["TFH"] = 1
O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
trend, breaks, start, start_low = ns["live_structure"](df, ns["LEG_N"], ns["MIN_LEG"], low_start=True)
bias = np.array(ns["bias_bar"](trend, breaks))
ns["DEMAND_HL_ONLY"] = ns["DEMAND_AFTER_HH"] = False      # filtered below from the zone fields (hl, after HH)

MIN_RR, FEE, MIN_STOP, RISK, MIN_TRADES = 2, 0.05, 0.5, 1.0, 8


def longs(zones, lim, keep_mss, target):
    """One row per filled long: entry at the zone top, stop at the bottom (at least MIN_STOP % below)."""
    rows = []
    for z in zones:
        t0, t1, top, bot = z[:4]
        if t1 is None:
            continue
        j, e = t_idx[t0], t_idx[t1]
        if L[e] > top:                       # ended without a touch
            continue
        x = top
        risk = max(top - bot, MIN_STOP / 100 * x)
        stop = x - risk
        f = None
        for k in range(e, len(df)):
            if L[k] <= x:
                f = k
                break
            if (start_low[k] is not None and start_low[k] > t0) or (bias[k] <= -1 if keep_mss else bias[k] < lim):
                break
        if f is None:
            continue
        tgt = H[j + 1:f].max() if target == "rally high" else x + float(target[0]) * risk
        rr = (tgt - x) / risk
        if rr < MIN_RR - 1e-9:
            continue
        r = np.nan
        for k in range(f, len(df)):
            if L[k] <= stop:
                r = -1.0
                break
            if k > f and H[k] >= tgt:
                r = rr
                break
        if not np.isnan(r):
            rows.append({"time": df.index[f], "net": r - 2 * FEE / (risk / x * 100), "hl": z[5], "ahh": z[6]})
    return pd.DataFrame(rows)


def stats(d):
    net = d["net"].reset_index(drop=True)
    eq = (1 + RISK / 100 * net).cumprod()
    dd = (1 - eq / eq.cummax()).max() * 100
    yrs = d.groupby(d["time"].dt.year)["net"].sum() * RISK
    return {"trades": len(d), "win": (net > 0).mean() * 100, "avgR": net.mean(), "ret": net.sum() * RISK, "dd": dd,
            "neg_years": int((yrs < 0).sum()), "years": "  ".join(f"{y}: {v:+.1f}%" for y, v in yrs.items())}


res = []
for sharp_atr, min_pct in itertools.product((1.5, 2.0, 2.5), (0.0, 1.0, 1.5, 2.0)):
    ns["DEMAND_SHARP_ATR"], ns["DEMAND_MIN_PCT"] = sharp_atr, min_pct
    for mss, counter in itertools.product((False, True), (True, False)):
        ns["DEMAND_MSS_OK"], ns["DEMAND_COUNTER_MSS_ENDS"] = mss, counter
        zones = ns["demand_zones"](df, ns["LEG_N"], bias, start_low)
        for target in ("rally high", "2R", "3R"):
            d = longs(zones, 0.5 if mss else 1, not counter, target)
            if not len(d):
                continue
            for filt, g in (("all", d), ("HL", d[d["hl"] == 1]), ("HL after HH", d[(d["hl"] == 1) & (d["ahh"] == 1)])):
                if len(g):
                    res.append({"sharp": f"{sharp_atr:g} ATR / {min_pct:g}%", "bias": "MSS too" if mss else "confirmed",
                                "counter MSS": "ends" if counter else "keeps", "target": target, "zones": filt, **stats(g)})
res = pd.DataFrame(res)
pd.set_option("display.width", 250, "display.max_columns", 20, "display.max_colwidth", 80)
print(f"Data {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}; {len(res)} combinations. Returns in % of the account "
      f"({RISK}% risk per trade, fees included).\n")
print("Current chart rule (1.5 ATR / 1%, MSS too, a counter MSS keeps the zone):")
cur = res[(res["sharp"] == "1.5 ATR / 1%") & (res["bias"] == "MSS too") & (res["counter MSS"] == "keeps")]
print(cur.drop(columns=["sharp", "bias", "counter MSS"]).round(2).to_string(index=False))
ok = res[res["trades"] >= MIN_TRADES]
print(f"\nTop 25 by return with at least {MIN_TRADES} trades:")
print(ok.sort_values("ret", ascending=False).head(25).round(2).to_string(index=False))
print(f"\nAverage over all combinations with at least {MIN_TRADES} trades (robustness: how each choice does across the rest):")
for col in ("zones", "bias", "counter MSS", "target", "sharp"):
    print(ok.groupby(col)[["trades", "win", "avgR", "ret", "dd"]].mean().round(2).to_string(), "\n")
