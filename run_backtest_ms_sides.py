"""
Backtest the "MS strategy long" and "MS strategy short" Pine scripts side by side:
each runs on its own (a long and a short can be open at the same time), as when
both scripts run on TradingView. Prints the report of each side, then the two
together: P&L added up (both size on their own 10,000), drawdown of the sum, the
most positions open at once and the result per year.

Settings: the same options as run_backtest_ms.py, --long-X / --short-X per side.
Defaults = the two Pine scripts (long: run_backtest_ms_long.py, short: swing 24 / HTF 84);
a structure option like --swing-candles applies to both sides.

Examples:
  python run_backtest_ms_sides.py
  python run_backtest_ms_sides.py --days 1825 --long-cont first2
"""
import pandas as pd

from futures_backtester import print_report
from run_backtest_ms import backtest, build_parser, load_data, save
from run_backtest_ms_long import LONG_SCRIPT
from run_backtest_ms_multi import max_open

SHORT_SCRIPT = dict(swing_candles=24, htf_candles=84)


def main():
    description = "Backtest the MS strategy long and short scripts side by side"
    per_side = {side: build_parser(description, side=side, **script).parse_args()
                for side, script in (("long", LONG_SCRIPT), ("short", SHORT_SCRIPT))}
    args = per_side["long"]
    signal, trade = load_data(args)

    results = {}
    for side in ("long", "short"):
        side_args = per_side[side]
        result, plan = backtest(side_args, signal, trade)
        print_report(result)
        save(result, plan, signal, prefix=f"ms_{side}")
        results[side] = result

    trades = pd.DataFrame([vars(t) for r in results.values() for t in r["trades"]])
    pnl = pd.concat([r["equity"].dropna() - args.cash for r in results.values()], axis=1)
    pnl = pnl.sort_index().ffill().fillna(0).sum(axis=1)
    equity = 2 * args.cash + pnl
    print("\n" + "=" * 64)
    print(f"  Long + short together (each on its own {args.cash:,.0f})")
    print(f"  Trades             {len(trades)} ({(trades['side'] == 'long').sum()} long, "
          f"{(trades['side'] == 'short').sum()} short), total {trades['r'].sum():+.1f}R")
    print(f"  P&L                {pnl.iloc[-1]:+,.0f} USDT ({pnl.iloc[-1] / (2 * args.cash):+.1%} on {2 * args.cash:,.0f})")
    print(f"  Max drawdown       {(equity / equity.cummax() - 1).min():.1%}")
    print(f"  Most open at once  {max_open(trades)}")
    print("=" * 64)

    if not trades.empty:
        trades["year"] = pd.to_datetime(trades["entry_time"]).dt.year
        yearly = trades.pivot_table(index="year", columns="side", values="r", aggfunc=["count", "sum"],
                                    fill_value=0)
        yearly.columns = [f"{side} {'trades' if what == 'count' else 'R'}" for what, side in yearly.columns]
        yearly["total R"] = trades.groupby("year")["r"].sum()
        print("\nPer year:")
        print(yearly.round(1).to_string())


if __name__ == "__main__":
    main()
