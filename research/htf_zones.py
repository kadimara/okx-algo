"""Higher-timeframe supply / demand zones from 1h candles (resampled to 4h / 1D).

Demand: the last candle before price moves up >= IMPULSE x ATR(14) (a close that far above the candle's high within
the next 3 candles) without trading below that candle first. Zone = that candle's low..high. Supply mirrored.
A zone is fresh until price first comes back into it (touch) and broken at the first close beyond its far edge
(on the zone's own timeframe). Overlapping zones of the same side and timeframe keep the newest.
"""
import numpy as np
import pandas as pd

IMPULSE, LOOK = 1.5, 3


def resample(df, rule):
    return df.resample(rule, origin="epoch", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def atr(df, n=14):
    c = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - c).abs(), (df["low"] - c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def zones(df1h, rule):
    """List of dicts: side (1 demand / -1 supply), t0 (base candle), top, bot, t_touch, t_break, held."""
    d = resample(df1h, rule)
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    a, T, n = atr(d), d.index, len(d)
    out = []
    for i in range(14, n - 1):
        end = min(n, i + 1 + LOOK)
        for side in (1, -1):
            if side == 1:
                ok = np.flatnonzero(c[i + 1:end] - h[i] >= IMPULSE * a[i])
                if not len(ok) or l[i + 1:i + 2 + ok[0]].min() < l[i]:
                    continue
            else:
                ok = np.flatnonzero(l[i] - c[i + 1:end] >= IMPULSE * a[i])
                if not len(ok) or h[i + 1:i + 2 + ok[0]].max() > h[i]:
                    continue
            out.append({"side": side, "i": i, "j": i + 1 + int(ok[0]), "top": h[i], "bot": l[i]})
    # newest zone wins where zones of the same side overlap
    keep = []
    for z in sorted(out, key=lambda z: -z["i"]):
        if not any(k["side"] == z["side"] and z["bot"] <= k["top"] and z["top"] >= k["bot"] for k in keep):
            keep.append(z)
    res = []
    for z in sorted(keep, key=lambda z: z["i"]):
        s, top, bot = z["side"], z["top"], z["bot"]
        touch = brk = None
        held = None
        for k in range(z["j"] + 1, n):
            if touch is None and (l[k] <= top if s == 1 else h[k] >= bot):
                touch = k
            if (c[k] < bot) if s == 1 else (c[k] > top):
                brk = k
                break
        if touch is not None:
            # held = price moves 2 zone heights away from the near edge before a close through the far edge
            tgt = top + 2 * (top - bot) if s == 1 else bot - 2 * (top - bot)
            stop = brk if brk is not None else n
            hit = np.flatnonzero((h[touch:stop] >= tgt) if s == 1 else (l[touch:stop] <= tgt))
            held = bool(len(hit)) if (len(hit) or brk is not None) else None   # None = still open
        res.append({"side": s, "t0": T[z["i"]], "top": top, "bot": bot,
                    "t_touch": T[touch] if touch is not None else None,
                    "t_break": T[brk] if brk is not None else None, "held": held})
    return res


if __name__ == "__main__":
    import glob
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    f = sorted(glob.glob(str(repo / "data" / "BTC_1h_*_index_signal_*.csv")))[-1]
    df = pd.read_csv(f, index_col=0, parse_dates=True)
    last = df["close"].iat[-1]
    print(f"last close {last:,.0f} ({df.index[-1]})")
    for rule in ("4h", "1D"):
        zs = zones(df, rule)
        done = [z for z in zs if z["held"] is not None]
        for side, name in ((1, "demand"), (-1, "supply")):
            dd = [z for z in done if z["side"] == side]
            print(f"{rule} {name}: {len([z for z in zs if z['side'] == side])} zones, first touch held "
                  f"{sum(z['held'] for z in dd)}/{len(dd)} = {100 * sum(z['held'] for z in dd) / max(1, len(dd)):.0f}%")
        live = [z for z in zs if z["t_break"] is None]
        for z in sorted(live, key=lambda z: abs((z["top"] + z["bot"]) / 2 - last))[:6]:
            print(f"   {'demand' if z['side'] == 1 else 'supply'} {z['bot']:,.0f}-{z['top']:,.0f} from {z['t0']:%Y-%m-%d %H}"
                  f" {'fresh' if z['t_touch'] is None else 'touched'}")
