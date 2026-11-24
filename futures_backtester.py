"""
Futures backtest engine for the MS VWAP strategy (longs and shorts).

Two candle sets, aligned by time:
  plan    -> strategy.analyze() on the SIGNAL candles (index or perp): per candle
             the plan for the next candle (side, entry, stop, target, risk)
  candles -> the TRADED instrument (OKX perpetual): all fills use these prices

Per candle j, in this order (same as the Pine strategy and the live bot):
  1. Flat with an order from j-1's close: a buy limit fills if low <= entry (at
     the open if it opened below), a short limit if high >= entry.
  2. In a position: stop if reached, else target. Both in one candle -> stop
     (pessimistic). No target on the fill candle (we can't know if it came
     before the fill). A gap through the stop fills at the open.
  3. Funding for each 00:00/08:00/16:00 UTC inside the candle (longs pay).
  4. At j's close: break-even check (from the candle after the fill): if the
     candle reached be_after_r R in profit, the stop moves to entry +/- offset.
  5. At j's close: anti-chop gate update, then, if flat and allowed, the next
     order is planned from j's row. Size = risk_pct of equity / stop distance.

Fees: limit entry and target are maker; stops are taker + slippage.
TradingView's Strategy Tester charges 0.05% on every fill: use
--maker-fee 0.05 --taker-fee 0.05 --slippage 0 to compare like for like.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

from strategies.ms_vwap import TradeGate

FUNDING_MS = 8 * 3600 * 1000


@dataclass
class Trade:
    side: str
    entry_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    exit_time: pd.Timestamp
    exit: float
    reason: str       # target, stop, break-even, end of test
    qty: float
    pnl: float        # after fees and funding
    fees: float
    funding: float
    r: float          # pnl in units of the planned risk
    planned_r: float = float("nan")   # potential reward:risk at the entry (if a target exists)
    stop_pct: float = float("nan")    # entry-to-stop distance in % of the entry
    leverage: float = float("nan")    # position value / equity at the entry
    setup: str = ""                   # e.g. BOS / CHoCH (MS strategy)


class FuturesBacktester:
    def __init__(self, plan: pd.DataFrame, candles: pd.DataFrame, starting_cash: float = 10_000,
                 risk_pct: float = 1.0, maker_fee_pct: float = 0.02, taker_fee_pct: float = 0.05,
                 slippage_pct: float = 0.02, funding_pct_8h: float = 0.01,
                 be_after_r: float = 1.0, be_offset_pct: float = 1.0,
                 re_arm_atr: float = 2.0, max_per_trend: int = 2,
                 warmup_candles: int = 0, strategy_name: str = ""):
        common = plan.index.intersection(candles.index)
        self.plan, self.candles = plan.loc[common], candles.loc[common]
        self.cash0 = starting_cash
        self.risk = risk_pct / 100
        self.maker, self.taker, self.slip = maker_fee_pct / 100, taker_fee_pct / 100, slippage_pct / 100
        self.funding = funding_pct_8h / 100
        self.be_r, self.be_off = be_after_r, be_offset_pct / 100
        self.re_arm_atr, self.max_per_trend = re_arm_atr, max_per_trend
        self.warmup, self.name = warmup_candles, strategy_name

    def run(self) -> dict:
        c, p = self.candles, self.plan
        times = c.index
        t_ms = np.array([int(t.timestamp() * 1000) for t in times])
        bar_ms = int(np.median(np.diff(t_ms))) if len(t_ms) > 1 else 0
        o, h, l, cl = (c[k].to_numpy() for k in ("open", "high", "low", "close"))
        side_p, entry_p, stop_p = p["side"].to_numpy(), p["entry"].to_numpy(), p["stop"].to_numpy()
        tgt_p, risk_p, trend = p["target"].to_numpy(), p["risk"].to_numpy(), p["trend"].to_numpy()
        reanchor, sig_close = p["reanchor"].to_numpy(), p["close"].to_numpy()
        avwap, atr = p["avwap"].to_numpy(), p["atr"].to_numpy()

        gate = TradeGate(self.re_arm_atr, self.max_per_trend)
        cash = self.cash0
        pos = None
        pending = None
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
            trades.append(Trade(pos["side"], pos["time"], pos["entry"], pos["stop0"], pos["target"],
                                times[j], price, reason, pos["qty"], pnl, fees, pos["funding"],
                                pnl / risk if risk else 0.0))
            pos = None

        for j in range(len(c)):
            closed_now = False

            # 1) entry fill
            if pos is None and pending is not None:
                s, e = pending["side"], pending["entry"]
                if (l[j] <= e) if s == "long" else (h[j] >= e):
                    fill = min(o[j], e) if s == "long" else max(o[j], e)
                    fee = pending["qty"] * fill * self.maker
                    cash -= fee
                    pos = dict(pending, entry=fill, time=times[j], fees=fee, funding=0.0,
                               stop0=pending["stop"], fill_j=j, be=False)
                    gate.on_entry()
            pending = None

            # 2) stop / target
            if pos:
                long = pos["side"] == "long"
                stop_hit = l[j] <= pos["stop"] if long else h[j] >= pos["stop"]
                tgt_hit = j != pos["fill_j"] and (h[j] >= pos["target"] if long else l[j] <= pos["target"])
                if stop_hit:
                    gap = (o[j] <= pos["stop"]) if long else (o[j] >= pos["stop"])
                    base = o[j] if gap else pos["stop"]
                    close_pos(j, base * (1 - self.slip if long else 1 + self.slip),
                              "break-even" if pos["be"] else "stop", self.taker)
                    closed_now = True
                elif tgt_hit:
                    gap = (o[j] >= pos["target"]) if long else (o[j] <= pos["target"])
                    close_pos(j, o[j] if gap else pos["target"], "target", self.maker)
                    closed_now = True

            # 3) funding
            if pos and bar_ms:
                n_fund = (t_ms[j] + bar_ms - 1) // FUNDING_MS - (t_ms[j] - 1) // FUNDING_MS
                if n_fund:
                    amt = pos["qty"] * o[j] * self.funding * n_fund
                    amt = amt if pos["side"] == "long" else -amt
                    cash -= amt
                    pos["funding"] += amt

            # 4) break-even, from the candle after the fill
            if pos and j > pos["fill_j"] and self.be_r > 0 and not pos["be"]:
                if pos["side"] == "long" and h[j] >= pos["entry"] + self.be_r * pos["risk"]:
                    pos["be"], pos["stop"] = True, pos["entry"] * (1 + self.be_off)
                elif pos["side"] == "short" and l[j] <= pos["entry"] - self.be_r * pos["risk"]:
                    pos["be"], pos["stop"] = True, pos["entry"] * (1 - self.be_off)

            # 5) anti-chop gate, then the next order
            can_trade = gate.update(bool(reanchor[j]), closed_now, trend[j], sig_close[j], avwap[j], atr[j])
            if pos:
                bars_in += 1
            elif j >= self.warmup and can_trade and side_p[j] in ("long", "short"):
                qty = cash * self.risk / risk_p[j]
                if qty > 0:
                    pending = {"side": side_p[j], "entry": entry_p[j], "stop": stop_p[j],
                               "target": tgt_p[j], "risk": risk_p[j], "qty": qty}

            sign = 0 if pos is None else (1 if pos["side"] == "long" else -1)
            equity[j] = cash + (sign * pos["qty"] * (cl[j] - pos["entry"]) if pos else 0.0)

        if pos:
            close_pos(len(c) - 1, cl[-1], "end of test", self.taker)
            equity[-1] = cash

        eq = pd.Series(equity, index=times, name="equity")
        return {"strategy": self.name, "trades": trades, "equity": eq,
                "stats": self._stats(trades, eq, cash, bars_in, o, cl)}

    def _stats(self, trades, eq, final, bars_in, o, cl):
        start = self.warmup if self.warmup < len(o) else 0

        def side_stats(ts):
            if not ts:
                return {"n": 0, "win": None, "avg_r": None}
            return {"n": len(ts), "win": sum(t.pnl > 0 for t in ts) / len(ts),
                    "avg_r": sum(t.r for t in ts) / len(ts)}

        gains = sum(t.pnl for t in trades if t.pnl > 0)
        losses = -sum(t.pnl for t in trades if t.pnl < 0)
        tested = eq.iloc[start:]
        return {
            "period": f"{self.candles.index[start]:%Y-%m-%d} -> {self.candles.index[-1]:%Y-%m-%d}",
            "candles": len(self.candles) - start,
            "start": self.cash0, "final": final,
            "return": final / self.cash0 - 1,
            "buy_hold": cl[-1] / o[start] - 1,
            "max_dd": (tested / tested.cummax() - 1).min() if len(tested) else 0.0,
            "all": side_stats(trades),
            "long": side_stats([t for t in trades if t.side == "long"]),
            "short": side_stats([t for t in trades if t.side == "short"]),
            "total_r": sum(t.r for t in trades),
            "profit_factor": gains / losses if losses else None,
            "exits": {r: sum(t.reason == r for t in trades)
                      for r in dict.fromkeys(["target", "stop", "break-even", "trailing stop", "end of test"]
                                             + [t.reason for t in trades])},
            "fees": sum(t.fees for t in trades),
            "funding": sum(t.funding for t in trades),
            "exposure": bars_in / max(1, len(o) - start),
        }


def print_report(result: dict) -> None:
    s = result["stats"]
    pct = lambda v: "n/a" if v is None else f"{v:+.2%}"

    def side(d):
        return "0 trades" if not d["n"] else f"{d['n']} trades, win {d['win']:.1%}, avg {d['avg_r']:+.2f}R"

    lines = [
        ("Strategy", result["strategy"]),
        ("Period", f"{s['period']}  ({s['candles']} candles)"),
        ("Start -> final", f"{s['start']:.2f} -> {s['final']:.2f} USDT"),
        ("Return", pct(s["return"])),
        ("Buy & hold", pct(s["buy_hold"])),
        ("Max drawdown", pct(s["max_dd"])),
        ("Trades", side(s["all"])),
        ("  Longs", side(s["long"])),
        ("  Shorts", side(s["short"])),
        ("Total R", f"{s['total_r']:+.1f}R"),
        ("Profit factor", "n/a" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}"),
        ("Exits", ", ".join(f"{k} {v}" for k, v in s["exits"].items() if v) or "none"),
        ("Fees / funding", f"{s['fees']:.2f} / {s['funding']:+.2f} USDT"),
        ("Time in market", f"{s['exposure']:.1%}"),
    ]
    print("\n" + "=" * 64)
    for label, value in lines:
        print(f"  {label:<18} {value}")
    print("=" * 64)


def save_results(result: dict, plan: pd.DataFrame, folder: str = "results") -> Path:
    path = Path(folder)
    path.mkdir(exist_ok=True)
    pd.DataFrame([asdict(t) for t in result["trades"]]).to_csv(path / "vwap_trades.csv", index=False)
    result["equity"].to_csv(path / "vwap_equity.csv")
    cols = ["close", "trend", "structure_trend", "high_tag", "low_tag", "last_high", "last_low",
            "avwap", "atr", "side", "entry", "stop", "target"]
    plan[cols].to_csv(path / "vwap_structure.csv")
    return path
