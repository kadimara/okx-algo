"""
Backtest engine: replays historical candles through a strategy and simulates
trades in a pretend account.

Two sets of candles can be used:
  signal candles -> what the strategy looks at (OKX index or spot)
  trade candles  -> the tradable OKX spot market: all fills and the stop-loss
If no signal candles are given, the strategy looks at the trade candles.

Order of events for every candle:
  1. If holding and a stop-loss is set, check whether the spot low hit it.
  2. When the candle closes, show the strategy ONLY the signal candles up to now.
  3. Act on its signal at the NEXT spot candle's open (no look-ahead bias).
Fees and slippage are charged on every buy and sell.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

import pandas as pd

from market_data import align
from strategies.base import BUY, SELL, Strategy


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    amount: float
    reason: str        # "signal", "stop-loss" or "end of test"
    pnl: float         # in quote currency (e.g. USDT), after fees
    pnl_pct: float


@dataclass
class BacktestResult:
    strategy: str
    signal_data: str
    trades: List[Trade]
    equity: pd.Series
    stats: dict


class Backtester:
    def __init__(self, strategy: Strategy, candles: pd.DataFrame,
                 starting_cash: float = 1000.0, fee_pct: float = 0.1,
                 slippage_pct: float = 0.05, stop_loss_pct: Optional[float] = None,
                 signal_candles: Optional[pd.DataFrame] = None, signal_data: str = "spot"):
        self.strategy = strategy
        self.signal_data = signal_data
        if signal_candles is None:
            self.signals, self.candles = candles, candles
        else:
            self.signals, self.candles = align(signal_candles, candles)
        self.starting_cash = starting_cash
        self.fee = fee_pct / 100
        self.slip = slippage_pct / 100
        self.stop_loss = stop_loss_pct / 100 if stop_loss_pct else None

    def run(self) -> BacktestResult:
        c = self.candles
        lb = self.strategy.lookback
        if len(c) < lb + 2:
            raise ValueError(f"Need at least {lb + 2} candles, got {len(c)}. "
                             "Use more days or smaller MA lengths.")

        times = c.index
        opens, lows, closes = (c[k].to_numpy() for k in ("open", "low", "close"))

        cash, coins = self.starting_cash, 0.0
        entry_price = entry_time = None
        cash_at_entry = 0.0
        fees_paid, bars_in_market = 0.0, 0
        trades: List[Trade] = []
        eq_values, eq_times = [], []

        def close_position(i, price, reason):
            nonlocal cash, coins, fees_paid
            proceeds = coins * price
            fee = proceeds * self.fee
            fees_paid += fee
            cash = proceeds - fee
            pnl = cash - cash_at_entry
            trades.append(Trade(entry_time, entry_price, times[i], price, coins,
                                reason, pnl, pnl / cash_at_entry))
            coins = 0.0

        for i in range(lb - 1, len(c) - 1):
            # 1) stop-loss during candle i
            if coins > 0 and self.stop_loss:
                stop_price = entry_price * (1 - self.stop_loss)
                if lows[i] <= stop_price:
                    # if the candle opened below the stop (a gap), we get the open
                    fill = min(opens[i], stop_price) * (1 - self.slip)
                    close_position(i, fill, "stop-loss")

            if coins > 0:
                bars_in_market += 1
            eq_values.append(cash + coins * closes[i])
            eq_times.append(times[i])

            # 2) decision at the close of candle i, using only past candles
            signal = self.strategy.generate_signal(self.signals.iloc[i - lb + 1:i + 1])

            # 3) fill at the next candle's open
            if signal == BUY and coins == 0:
                price = opens[i + 1] * (1 + self.slip)
                fee = cash * self.fee
                fees_paid += fee
                cash_at_entry = cash
                coins = (cash - fee) / price
                cash = 0.0
                entry_price, entry_time = price, times[i + 1]
            elif signal == SELL and coins > 0:
                close_position(i + 1, opens[i + 1] * (1 - self.slip), "signal")

        still_holding = coins > 0
        if still_holding:
            close_position(len(c) - 1, closes[-1] * (1 - self.slip), "end of test")
        eq_values.append(cash)
        eq_times.append(times[-1])

        equity = pd.Series(eq_values, index=eq_times, name="equity")
        stats = self._stats(trades, equity, cash, fees_paid, bars_in_market,
                            len(eq_values) - 1, opens[lb], closes[-1], still_holding)
        return BacktestResult(self.strategy.describe(), self.signal_data, trades, equity, stats)

    def _stats(self, trades, equity, final, fees_paid, bars_in, bars_total,
               first_open, last_close, still_holding):
        # buy & hold with the same fees and slippage, for a fair comparison
        bh_coins = self.starting_cash * (1 - self.fee) / (first_open * (1 + self.slip))
        bh_final = bh_coins * last_close * (1 - self.slip) * (1 - self.fee)
        wins = [t for t in trades if t.pnl > 0]
        pcts = [t.pnl_pct for t in trades]
        return {
            "period": f"{self.candles.index[0]:%Y-%m-%d} -> {self.candles.index[-1]:%Y-%m-%d}",
            "candles": len(self.candles),
            "starting cash": self.starting_cash,
            "final equity": final,
            "strategy return": final / self.starting_cash - 1,
            "buy & hold return": bh_final / self.starting_cash - 1,
            "trades": len(trades),
            "win rate": len(wins) / len(trades) if trades else None,
            "avg trade": sum(pcts) / len(pcts) if pcts else None,
            "best trade": max(pcts) if pcts else None,
            "worst trade": min(pcts) if pcts else None,
            "stop-loss exits": sum(t.reason == "stop-loss" for t in trades),
            "max drawdown": (equity / equity.cummax() - 1).min(),
            "fees paid": fees_paid,
            "time in market": bars_in / bars_total if bars_total else 0,
            "still holding at end": still_holding,
        }


def print_report(result: BacktestResult, quote: str = "USDT") -> None:
    s = result.stats
    pct = lambda v: "n/a" if v is None else f"{v:+.2%}"
    beat = s["strategy return"] > s["buy & hold return"]
    lines = [
        ("Strategy", result.strategy),
        ("Signals from", f"OKX {result.signal_data} price (orders on spot)"),
        ("Period", f"{s['period']}  ({s['candles']} candles)"),
        ("Starting cash", f"{s['starting cash']:.2f} {quote}"),
        ("Final equity", f"{s['final equity']:.2f} {quote}"),
        ("Strategy return", pct(s["strategy return"])),
        ("Buy & hold return", pct(s["buy & hold return"])),
        ("Beat buy & hold?", "YES" if beat else "no"),
        ("Max drawdown", pct(s["max drawdown"])),
        ("Trades", str(s["trades"])),
        ("Win rate", "n/a" if s["win rate"] is None else f"{s['win rate']:.1%}"),
        ("Avg / best / worst", f"{pct(s['avg trade'])} / {pct(s['best trade'])} / {pct(s['worst trade'])}"),
        ("Stop-loss exits", str(s["stop-loss exits"])),
        ("Fees paid", f"{s['fees paid']:.2f} {quote}"),
        ("Time in market", f"{s['time in market']:.1%}"),
    ]
    if s["still holding at end"]:
        lines.append(("Note", "position was open at the end; closed at last price"))
    print("\n" + "=" * 56)
    for label, value in lines:
        print(f"  {label:<20} {value}")
    print("=" * 56)


def save_results(result: BacktestResult, folder: str = "results") -> Path:
    path = Path(folder)
    path.mkdir(exist_ok=True)
    pd.DataFrame([asdict(t) for t in result.trades]).to_csv(path / "trades.csv", index=False)
    result.equity.to_csv(path / "equity.csv")
    return path
