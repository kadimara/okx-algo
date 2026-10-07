"""
Futures backtest engine for the MS structure strategy (longs and shorts).

Two candle sets, aligned by time:
  plan    -> MSStructure.analyze() on the SIGNAL candles (index or perp): per
             candle the long and short plan for the next candle
  candles -> the TRADED instrument (OKX perpetual): all fills use these prices

Per candle j, in this order (same as the Pine strategy and the live bot):
  1. Flat with an order from j-1's close: a buy limit fills if low <= entry (at
     the open if it opened below), a short limit if high >= entry.
  2. In a position: stop if reached (also on the fill candle), else target (not
     on the fill candle: we can't know if it came before the fill). Both in one
     candle -> stop (pessimistic). A gap through a level fills at the open.
  3. Funding for each 00:00/08:00/16:00 UTC inside the candle (longs pay).
  4. At j's close, from the candle after the fill: break-even (stop to entry once
     price has moved be_after_r R) and the candle trailing stop (once price has
     moved trail_after_r R, the stop follows the lowest low / highest high of the
     last trail_candles traded candles -/+ the stop offset, only in your favour).
  5. At j's close, if flat: the next order from j's row. One trade per HL (long)
     / LH (short): a pivot whose order filled is not traded again.
     Size = risk_pct of equity / stop distance, capped at max_leverage x equity.

Fees: limit entry and target are maker; stops are taker + slippage.
TradingView's Strategy Tester charges 0.05% on every fill: use
--maker-fee 0.05 --taker-fee 0.05 --slippage 0 to compare like for like.
"""
from typing import List

import numpy as np
import pandas as pd

from futures_backtester import FUNDING_MS, FuturesBacktester, Trade


class MSBacktester(FuturesBacktester):
    def __init__(self, plan: pd.DataFrame, candles: pd.DataFrame, starting_cash: float = 10_000,
                 risk_pct: float = 1.0, max_leverage: float = 2.0,
                 maker_fee_pct: float = 0.02, taker_fee_pct: float = 0.05,
                 slippage_pct: float = 0.02, funding_pct_8h: float = 0.01,
                 use_target: bool = False, be_after_r: float = 0.0,
                 trail: bool = True, trail_after_r: float = 0.0, trail_candles: int = 12,
                 stop_pct: float = 0.15, warmup_candles: int = 0, strategy_name: str = ""):
        super().__init__(plan, candles, starting_cash=starting_cash, risk_pct=risk_pct,
                         maker_fee_pct=maker_fee_pct, taker_fee_pct=taker_fee_pct,
                         slippage_pct=slippage_pct, funding_pct_8h=funding_pct_8h,
                         be_after_r=be_after_r, be_offset_pct=0.0,
                         warmup_candles=warmup_candles, strategy_name=strategy_name)
        self.max_lev = max_leverage
        self.use_target = use_target
        self.trail, self.trail_r, self.trail_n = trail, trail_after_r, max(1, int(trail_candles))
        self.stop_off = stop_pct / 100

    def run(self) -> dict:
        c, p = self.candles, self.plan
        times = c.index
        t_ms = np.array([int(t.timestamp() * 1000) for t in times])
        bar_ms = int(np.median(np.diff(t_ms))) if len(t_ms) > 1 else 0
        o, h, l, cl = (c[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        low_n = c["low"].rolling(self.trail_n, min_periods=1).min().to_numpy()
        high_n = c["high"].rolling(self.trail_n, min_periods=1).max().to_numpy()
        plans = {side: {k: p[f"{side[0]}_{k}"].to_numpy() for k in ("entry", "stop", "target", "rr", "id")}
                 for side in ("long", "short")}
        ok = {"long": p["long_ok"].to_numpy(), "short": p["short_ok"].to_numpy()}

        cash = self.cash0
        pos = None
        pending = None
        used = {"long": -1, "short": -1}
        trades: List[Trade] = []
        equity = np.full(len(c), np.nan)
        bars_in = 0

        def close_pos(j, price, reason, fee_rate):
            nonlocal cash, pos
            sign = 1 if pos["side"] == "long" else -1
            fee = pos["qty"] * price * fee_rate
            gross = sign * pos["qty"] * (price - pos["entry"])
            cash += gross - fee
            fees = pos["fees"] + fee
            pnl = gross - fees - pos["funding"]
            risk = pos["qty"] * pos["risk"]
            trades.append(Trade(pos["side"], pos["time"], pos["entry"], pos["stop0"],
                                pos["target"] if self.use_target else float("nan"),
                                times[j], price, reason, pos["qty"], pnl, fees, pos["funding"],
                                pnl / risk if risk else 0.0, pos["planned_r"], pos["stop_pct"],
                                pos["leverage"]))
            pos = None

        for j in range(len(c)):
            # 1) entry fill
            if pos is None and pending is not None:
                s, e = pending["side"], pending["entry"]
                if (l[j] <= e) if s == "long" else (h[j] >= e):
                    fill = min(o[j], e) if s == "long" else max(o[j], e)
                    fee = pending["qty"] * fill * self.maker
                    cash -= fee
                    pos = dict(pending, entry=fill, time=times[j], fees=fee, funding=0.0,
                               stop0=pending["stop"], fill_j=j, be=False, trailed=False,
                               best=None)
                    used[s] = pending["id"]        # one trade per HL / LH
            pending = None

            # 2) stop / target
            if pos:
                long = pos["side"] == "long"
                stop_hit = l[j] <= pos["stop"] if long else h[j] >= pos["stop"]
                tgt_hit = (self.use_target and j != pos["fill_j"] and
                           (h[j] >= pos["target"] if long else l[j] <= pos["target"]))
                if stop_hit:
                    gap = (o[j] <= pos["stop"]) if long else (o[j] >= pos["stop"])
                    base = o[j] if gap else pos["stop"]
                    reason = "trailing stop" if pos["trailed"] else "break-even" if pos["be"] else "stop"
                    close_pos(j, base * (1 - self.slip if long else 1 + self.slip), reason, self.taker)
                elif tgt_hit:
                    gap = (o[j] >= pos["target"]) if long else (o[j] <= pos["target"])
                    close_pos(j, o[j] if gap else pos["target"], "target", self.maker)

            # 3) funding
            if pos and bar_ms:
                n_fund = (t_ms[j] + bar_ms - 1) // FUNDING_MS - (t_ms[j] - 1) // FUNDING_MS
                if n_fund:
                    amt = pos["qty"] * o[j] * self.funding * n_fund
                    amt = amt if pos["side"] == "long" else -amt
                    cash -= amt
                    pos["funding"] += amt

            # 4) stop management, from the candle after the fill
            if pos and j > pos["fill_j"]:
                long = pos["side"] == "long"
                ep = pos["entry"]
                best = h[j] if long else l[j]
                pos["best"] = best if pos["best"] is None else (max(pos["best"], best) if long
                                                                 else min(pos["best"], best))
                r0 = (ep - pos["stop0"]) if long else (pos["stop0"] - ep)
                move_r = ((pos["best"] - ep) if long else (ep - pos["best"])) / r0 if r0 > 0 else 0.0
                new = pos["stop"]
                if self.be_r > 0 and not pos["be"] and move_r >= self.be_r:
                    pos["be"] = True
                    new = max(new, ep) if long else min(new, ep)
                if self.trail and move_r >= self.trail_r:
                    ts = low_n[j] * (1 - self.stop_off) if long else high_n[j] * (1 + self.stop_off)
                    if (ts > new) if long else (ts < new):
                        new, pos["trailed"] = ts, True
                pos["stop"] = new

            # 5) next order
            if pos:
                bars_in += 1
            elif j >= self.warmup:
                for side in ("long", "short"):
                    pl = plans[side]
                    if ok[side][j] and pl["id"][j] != used[side]:
                        e, st = pl["entry"][j], pl["stop"][j]
                        dist = abs(e - st)
                        qty = min(cash * self.risk / dist, cash * self.max_lev / e) if dist > 0 else 0.0
                        if qty > 0:
                            pending = {"side": side, "entry": e, "stop": st, "target": pl["target"][j],
                                       "risk": dist, "qty": qty, "id": pl["id"][j],
                                       "planned_r": pl["rr"][j], "stop_pct": dist / e * 100,
                                       "leverage": qty * e / cash}
                        break

            sign = 0 if pos is None else (1 if pos["side"] == "long" else -1)
            equity[j] = cash + (sign * pos["qty"] * (cl[j] - pos["entry"]) if pos else 0.0)

        if pos:
            close_pos(len(c) - 1, cl[-1], "end of test", self.taker)
            equity[-1] = cash

        eq = pd.Series(equity, index=times, name="equity")
        return {"strategy": self.name, "trades": trades, "equity": eq,
                "stats": self._stats(trades, eq, cash, bars_in, o, cl)}


def save_ms_results(result: dict, plan: pd.DataFrame, folder: str = "results"):
    from dataclasses import asdict
    from pathlib import Path
    path = Path(folder)
    path.mkdir(exist_ok=True)
    pd.DataFrame([asdict(t) for t in result["trades"]]).to_csv(path / "ms_trades.csv", index=False)
    result["equity"].to_csv(path / "ms_equity.csv")
    cols = ["close", "trend", "high_tag", "low_tag", "last_high", "last_low", "bos_up", "bos_down",
            "long_ok", "l_entry", "l_stop", "l_target", "l_rr",
            "short_ok", "s_entry", "s_stop", "s_target", "s_rr"]
    plan[cols].to_csv(path / "ms_structure.csv")
    return path
