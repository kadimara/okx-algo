"""Retune the supply / demand zone rules on the current structure (ATR-based legs) with a hold-out period.

The trades are the chart's own (zone_trades in build_structure_chart.py: entry at the zone edge or the body high of a wide
candle, stop beyond the zone at least MIN_STOP %, target the extreme of the move away before the fill, at least MIN_RR R,
fees included; 1 % risk per trade, so R = % of the account).

Each side gets a grid of its zone rules. Rules are ranked on the TRAIN period only (before SPLIT) and then shown on the
TEST period (SPLIT onwards), which the ranking never saw. Combinations with fewer than MIN_TRADES train trades are not
ranked. The chart's current rule is shown first for comparison.

Usage: python research/backtest_retune.py   (okx-algo venv, newest BTC 1h index file in data/)
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
ns = {"analyze_bos_structure": analyze_bos_structure, "bos_pivot_list": bos_pivot_list, "pd": pd, "np": np,
      "bisect": bisect, "heapq": __import__("heapq"), "_MEMO": {}}
SETTINGS = {"LEG_N", "SHARP_K", "DRAW_GREY", "WIDE_ZONE", "LH_BIAS", "SUPPLY_MIN_PCT", "SUPPLY_MSS_OK", "COUNTER_MSS_ENDS",
            "STRUCT_MODE", "DEMAND_HL_ONLY", "DEMAND_SHARP_ATR", "DEMAND_SHARP_K", "DEMAND_COUNTER_MSS_ENDS", "MIN_RR"}
for node in ast.parse(src).body:
    names = {getattr(e, "id", None) for t in getattr(node, "targets", []) for e in getattr(t, "elts", [t])}
    if isinstance(node, ast.FunctionDef) or names & SETTINGS:
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)

files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
df = pd.read_csv(files[-1], index_col=0, parse_dates=True)
ns["ts"] = ts = lambda t: int(pd.Timestamp(t).timestamp())
ns["T"] = T = [ts(t) for t in df.index]
ns["t_idx"] = {t: i for i, t in enumerate(T)}
trend, breaks, start, start_low = ns["live_structure"](df, ns["LEG_N"], ns["MIN_LEG"], low_start=True)
bias = np.array(ns["bias_bar"](trend, breaks))

SPLIT = pd.Timestamp("2024-01-01", tz=df.index.tz)
MIN_TRADES = 15
CUR = {k: ns[k] for k in ("SHARP_K", "SHARP_ATR", "SUPPLY_MIN_PCT", "SUPPLY_MSS_OK", "COUNTER_MSS_ENDS", "DEMAND_SHARP_K",
                          "DEMAND_SHARP_ATR", "DEMAND_MIN_PCT", "DEMAND_MSS_OK", "DEMAND_COUNTER_MSS_ENDS", "DEMAND_HL_ONLY",
                          "DEMAND_AFTER_HH")}


def trades(side):
    if side == "short":
        z = ns["supply_zones"](df, ns["LEG_N"], bias, start)
        res = ns["zone_trades"](df, z, "short", bias, start, 0.5 if ns["SUPPLY_MSS_OK"] else 1, keep_mss=not ns["COUNTER_MSS_ENDS"])
    else:
        z = ns["demand_zones"](df, ns["LEG_N"], bias, start_low)
        res = ns["zone_trades"](df, z, "long", bias, start_low, 0.5 if ns["DEMAND_MSS_OK"] else 1,
                                keep_mss=not ns["DEMAND_COUNTER_MSS_ENDS"])
    rows = [(pd.Timestamp(r[1], unit="s", tz=df.index.tz), r[3]) for r in res if r[0] is not None]
    return pd.DataFrame(rows, columns=["time", "r"])


def stats(d):
    if not len(d):
        return {"n": 0, "win": np.nan, "ret": 0.0, "dd": 0.0}
    eq = (1 + d["r"].reset_index(drop=True) / 100).cumprod()
    return {"n": len(d), "win": round((d["r"] > 0).mean() * 100), "ret": round(d["r"].sum(), 1),
            "dd": round((1 - eq / eq.cummax()).max() * 100, 1)}


def evaluate(side, setting):
    ns.update(setting)
    d = trades(side)
    tr, te = stats(d[d["time"] < SPLIT]), stats(d[d["time"] >= SPLIT])
    yrs = d.groupby(d["time"].dt.year)["r"].sum()
    return {**setting, **{f"tr_{k}": v for k, v in tr.items()}, **{f"te_{k}": v for k, v in te.items()},
            "all": round(d["r"].sum(), 1), "neg_years": int((yrs < 0).sum())}


GRIDS = {
    "short": {"SHARP_K": (4, 6, 8), "SHARP_ATR": (1.0, 1.5, 2.0, 2.5), "SUPPLY_MIN_PCT": (0.0, 1.0),
              "SUPPLY_MSS_OK": (True, False), "COUNTER_MSS_ENDS": (True, False)},
    "long": {"DEMAND_SHARP_K": (4, 8, 12), "DEMAND_SHARP_ATR": (1.0, 1.5, 2.0), "DEMAND_MIN_PCT": (0.0, 1.0, 1.5),
             "DEMAND_MSS_OK": (True, False), "DEMAND_COUNTER_MSS_ENDS": (True, False), "DEMAND_HL_ONLY": (True, False),
             "DEMAND_AFTER_HH": (False, True)},
}
pd.set_option("display.width", 250, "display.max_columns", 30)
print(f"Data {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}; train < {SPLIT:%Y-%m-%d} <= test. "
      f"Returns in % of the account (1 % risk per trade, fees included).\n")
for side, grid in GRIDS.items():
    keys = list(grid)
    ns.update(CUR)
    cur = evaluate(side, {k: CUR[k] for k in keys})
    ns.update(CUR)
    res = pd.DataFrame([evaluate(side, dict(zip(keys, v))) for v in itertools.product(*grid.values())])
    ns.update(CUR)
    short_names = {k: k.replace("DEMAND_", "").replace("SUPPLY_", "").lower() for k in keys}
    show = lambda d: d.rename(columns=short_names).to_string(index=False)
    print(f"=== {'Supply zones (shorts)' if side == 'short' else 'Demand zones (longs)'}: {len(res)} combinations")
    print("Current rule:")
    print(show(pd.DataFrame([cur])))
    ok = res[res["tr_n"] >= MIN_TRADES]
    print(f"\nTop 15 on the TRAIN period (>= {MIN_TRADES} train trades), with their TEST result:")
    print(show(ok.sort_values("tr_ret", ascending=False).head(15)))
    q = ok["tr_ret"].rank(pct=True)
    top = ok[q >= 0.8]
    print(f"\nTrain top 20 % ({len(top)} combinations) on the test period: mean {top['te_ret'].mean():+.1f} %, "
          f"median {top['te_ret'].median():+.1f} %; all ranked combinations: mean {ok['te_ret'].mean():+.1f} %. "
          f"Rank correlation train vs test: {ok['tr_ret'].rank().corr(ok['te_ret'].rank()):+.2f}")
    print("\nEach choice averaged over all ranked combinations (train / test return, trades):")
    for k in keys:
        g = ok.groupby(k)[["tr_ret", "te_ret", "tr_n", "te_n"]].mean().round(1)
        print(f"  {short_names[k]}: " + "   ".join(f"{i}: {r.tr_ret:+.1f} / {r.te_ret:+.1f} % ({r.tr_n:.0f} / {r.te_n:.0f})"
                                                  for i, r in g.iterrows()))
    print("\n")
