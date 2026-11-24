"""
Backtest the MS structure strategy on several OKX perpetuals at once, with a
train / test split, to see which coins to run a bot on.

Per coin: the strategy is calculated on the full history (so the structure is
warmed up), then backtested separately on the train period (before --split) and
the test period (from --split). A coin passes if it makes money (R per year > 0)
in both periods with at least --min-test-trades trades in the test.

Portfolio: the passing coins together, as if one bot per coin runs on the same
account: P&L of all coins added up, with the most positions that were open at
the same time.

Examples:
  python run_backtest_ms_multi.py
  python run_backtest_ms_multi.py --coins BTC,ETH,SOL --split 2024-06-01
  python run_backtest_ms_multi.py --signal-data perp
"""
import argparse
from dataclasses import asdict
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

from ms_backtester import MSBacktester
from run_backtest_ms import add_strategy_args, exit_settings, make_exits, make_strategy
from vwap_data import cached, fetch_perp, fetch_signal


def period_stats(trades: list, equity: pd.Series, cash0: float) -> dict:
    r = np.array([t.r for t in trades]) if trades else np.zeros(0)
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    eq = equity.dropna()
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {"trades": len(r), "trades/yr": len(r) / years, "R": r.sum(), "R/yr": r.sum() / years,
            "pf": gains / losses if losses else np.nan, "win": (r > 0).mean() if len(r) else np.nan,
            "return": eq.iloc[-1] / cash0 - 1 if len(eq) else 0.0,
            "max_dd": (eq / eq.cummax() - 1).min() if len(eq) else 0.0}


def max_open(trades: pd.DataFrame) -> int:
    """The most positions open at the same time."""
    if trades.empty:
        return 0
    ev = pd.concat([pd.Series(1, index=pd.to_datetime(trades["entry_time"])),
                    pd.Series(-1, index=pd.to_datetime(trades["exit_time"]))]).sort_index(kind="stable")
    return int(ev.groupby(level=0).sum().cumsum().max())


def main():
    p = argparse.ArgumentParser(description="Backtest the MS structure strategy on several coins")
    add_strategy_args(p)
    g = p.add_argument_group("multi-coin backtest")
    g.add_argument("--coins", default="BTC,ETH,SOL,XRP,DOGE,BNB", help="comma separated, traded as <coin>/USDT perpetual")
    g.add_argument("--days", type=int, default=2400, help="days of history (as far as each perp goes)")
    g.add_argument("--split", default="2024-01-01", help="train before this date, test from it")
    g.add_argument("--warmup-days", type=int, default=60, help="history before the train period starts")
    g.add_argument("--min-test-trades", type=int, default=8, help="a coin needs this many test trades to pass")
    g.add_argument("--cash", type=float, default=10_000)
    g.add_argument("--maker-fee", type=float, default=0.1, help="%% on limit entries and targets")
    g.add_argument("--taker-fee", type=float, default=0.1, help="%% on stops")
    g.add_argument("--slippage", type=float, default=0.02, help="%% on stops")
    g.add_argument("--funding", type=float, default=0.01, help="%% per 8h; longs pay, shorts receive")
    g.add_argument("--refresh", action="store_true", help="re-download instead of using today's cache")
    args = p.parse_args()

    strategy, exits = make_strategy(args), make_exits(args)
    tf_sec = ccxt.Exchange.parse_timeframe(args.timeframe)
    since = ccxt.Exchange.milliseconds() - args.days * 86_400_000
    split = pd.Timestamp(args.split, tz="UTC")
    fees = dict(starting_cash=args.cash, risk_pct=args.risk, max_leverage=args.max_leverage,
                maker_fee_pct=args.maker_fee, taker_fee_pct=args.taker_fee, slippage_pct=args.slippage,
                funding_pct_8h=args.funding, long_exit=exits["long"], short_exit=exits["short"])
    print(f"{strategy.describe()} | {exit_settings(args)}")

    rows, all_trades, curves = [], [], {}
    for coin in [c.strip().upper() for c in args.coins.split(",") if c.strip()]:
        print(f"\n--- {coin}")
        tag = f"{coin}_{args.timeframe}_{args.days}d"
        try:
            signal = cached(f"{tag}_{args.signal_data}_signal",
                            lambda: fetch_signal(args.signal_data, coin, args.timeframe, since,
                                                 args.exchanges, verbose=True), args.refresh)
            trade = signal if args.signal_data == "perp" else cached(
                f"{tag}_perp", lambda: fetch_perp(coin, args.timeframe, since, verbose=True), args.refresh)
        except (Exception, SystemExit) as e:   # missing market / index on OKX
            print(f"  skipped: {str(e)[:150]}")
            continue
        plan = strategy.analyze(signal)
        start = max(signal.index[0], trade.index[0]) + pd.Timedelta(seconds=tf_sec * max(
            strategy.warmup_candles, args.warmup_days * 86400 // tf_sec))
        if start >= split:
            print(f"  skipped: history starts {start:%Y-%m-%d}, after the split")
            continue
        row = {"coin": coin, "from": f"{start:%Y-%m-%d}"}
        for label, a, b in (("train", start, split - pd.Timedelta(seconds=1)), ("test", split, None)):
            res = MSBacktester(plan.loc[a:b], trade.loc[a:b], **fees).run()
            st = period_stats(res["trades"], res["equity"], args.cash)
            row.update({f"{label} {k}": v for k, v in st.items()})
            for t in res["trades"]:
                all_trades.append({"coin": coin, "period": label, **asdict(t)})
            curves[(coin, label)] = res["equity"].dropna() - args.cash
        row["pass"] = (row["train R/yr"] > 0 and row["test R/yr"] > 0
                       and row["test trades"] >= args.min_test_trades)
        rows.append(row)
        print(f"  train {row['train trades']} trades {row['train R']:+.1f}R | "
              f"test {row['test trades']} trades {row['test R']:+.1f}R | {'PASS' if row['pass'] else 'fail'}")

    if not rows:
        raise SystemExit("No coins could be tested.")
    summary = pd.DataFrame(rows).set_index("coin")
    trades = pd.DataFrame(all_trades)

    show = summary[[f"{p} {k}" for p in ("train", "test") for k in ("trades/yr", "R/yr", "pf", "win", "max_dd")]
                   + ["from", "pass"]].copy()
    for c in show.columns:
        if c.endswith(("win", "max_dd")):
            show[c] = (show[c] * 100).round(0)
        elif c.endswith(("trades/yr", "R/yr", "pf")):
            show[c] = show[c].round(1)
    print("\n" + "=" * 100)
    print("Per coin (win and max_dd in %):")
    print(show.to_string())

    passed = summary.index[summary["pass"]].tolist()
    print("\nPortfolio of the passing coins:", ", ".join(passed) or "none")
    for label in ("train", "test"):
        sel = [curves[(c, label)] for c in passed if (c, label) in curves]
        if not sel:
            continue
        pnl = pd.concat(sel, axis=1).sort_index().ffill().fillna(0).sum(axis=1)
        eq = args.cash + pnl
        tp = trades[(trades["period"] == label) & trades["coin"].isin(passed)]
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        print(f"  {label:<5} {len(tp)} trades ({len(tp) / years:.1f}/yr) | {tp['r'].sum():+.1f}R "
              f"({tp['r'].sum() / years:+.1f}R/yr) | P&L {pnl.iloc[-1]:+.0f} USDT on {args.cash:.0f} | "
              f"max DD {(eq / eq.cummax() - 1).min():.1%} | most open at once: {max_open(tp)}")

    Path("results").mkdir(exist_ok=True)
    trades.to_csv("results/ms_multi_trades.csv", index=False)
    summary.to_csv("results/ms_multi_summary.csv")
    print("\nSaved in ./results/: ms_multi_trades.csv, ms_multi_summary.csv")


if __name__ == "__main__":
    main()
