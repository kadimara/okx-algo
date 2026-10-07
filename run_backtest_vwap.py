"""
Backtest the MS VWAP strategy on OKX perpetual futures (same rules as the Pine strategy).

Examples:
  python run_backtest_vwap.py
  python run_backtest_vwap.py --signal-data perp --maker-fee 0.05 --slippage 0   (compare with TradingView)
  python run_backtest_vwap.py --timeframe 4h --days 365 --target-r 3
  python run_backtest_vwap.py --anchor trend --side long
"""
import argparse

import ccxt

from futures_backtester import FuturesBacktester, print_report, save_results
from structure import pivot_list
from strategies.ms_vwap import MSVWAP
from vwap_data import SIGNAL_KINDS, cached, fetch_perp, fetch_signal


def add_strategy_args(p):
    g = p.add_argument_group("market and data")
    g.add_argument("--base", default="BTC", help="coin, traded as <base>/USDT perpetual")
    g.add_argument("--timeframe", default="2h", help="15m 30m 1h 2h 4h 1d ...")
    g.add_argument("--signal-data", default="index", choices=SIGNAL_KINDS,
                   help="index = OKX index + aggregated volume, perp = perpetual price + own volume")
    g.add_argument("--exchanges", default=None, help="exchanges for aggregated volume (see aggregate_volume.py)")
    g = p.add_argument_group("strategy (same names as the Pine inputs)")
    g.add_argument("--swing-days", type=float, default=7.0, help="swing size in days")
    g.add_argument("--atr-length", type=int, default=14)
    g.add_argument("--anchor", default="swing_low", choices=["swing_low", "trend"],
                   help="swing_low = last swing low, trend = trend origin (last LL / HH)")
    g.add_argument("--sma-days", type=float, default=7.0, help="SMA length in days")
    g.add_argument("--slope-days", type=float, default=1.25, help="SMA slope lookback in days")
    g.add_argument("--side", default="both", choices=["both", "long", "short"])
    g.add_argument("--risk", type=float, default=1.0, help="risk per trade in %% of equity")
    g.add_argument("--stop-atr", type=float, default=2.0, help="stop distance in x ATR")
    g.add_argument("--target-r", type=float, default=4.0, help="target in R")
    g.add_argument("--be-after-r", type=float, default=1.0, help="move stop to entry after R, 0 = off")
    g.add_argument("--be-offset", type=float, default=1.0, help="break-even offset in %%")
    g.add_argument("--re-arm-atr", type=float, default=2.0, help="anti-chop re-arm distance in x ATR, 0 = off")
    g.add_argument("--max-per-trend", type=int, default=2, help="max trades per VWAP anchor, 0 = unlimited")


def make_strategy(args) -> MSVWAP:
    return MSVWAP(ccxt.Exchange.parse_timeframe(args.timeframe), swing_days=args.swing_days,
                  atr_len=args.atr_length, anchor_mode=args.anchor, sma_days=args.sma_days,
                  slope_days=args.slope_days, stop_atr=args.stop_atr, target_r=args.target_r,
                  allow_long=args.side in ("both", "long"), allow_short=args.side in ("both", "short"))


def main():
    p = argparse.ArgumentParser(description="Backtest the MS VWAP strategy on OKX perpetuals")
    add_strategy_args(p)
    g = p.add_argument_group("backtest")
    g.add_argument("--days", type=int, default=365, help="days to trade in the test")
    g.add_argument("--warmup-days", type=int, default=30, help="extra history before the test")
    g.add_argument("--cash", type=float, default=10_000)
    g.add_argument("--maker-fee", type=float, default=0.02, help="%% on limit entries and targets")
    g.add_argument("--taker-fee", type=float, default=0.05, help="%% on stops")
    g.add_argument("--slippage", type=float, default=0.02, help="%% on stops")
    g.add_argument("--funding", type=float, default=0.01, help="%% per 8h; longs pay, shorts receive")
    g.add_argument("--refresh", action="store_true", help="re-download instead of using today's cache")
    args = p.parse_args()

    strategy = make_strategy(args)
    tf_sec = ccxt.Exchange.parse_timeframe(args.timeframe)
    total_days = args.days + args.warmup_days
    since = ccxt.Exchange.milliseconds() - total_days * 86_400_000
    tag = f"{args.base}_{args.timeframe}_{total_days}d"

    signal = cached(f"{tag}_{args.signal_data}_signal",
                    lambda: fetch_signal(args.signal_data, args.base, args.timeframe, since,
                                         args.exchanges, verbose=True), args.refresh)
    trade = signal if args.signal_data == "perp" else cached(
        f"{tag}_perp", lambda: fetch_perp(args.base, args.timeframe, since, verbose=True), args.refresh)

    print(f"Analyzing ({strategy.describe()})...")
    plan = strategy.analyze(signal)
    warmup = max(int(args.warmup_days * 86400 / tf_sec), strategy.warmup_candles)
    if warmup >= len(plan):
        raise SystemExit(f"Not enough data: {len(plan)} candles, warm-up needs {warmup}. Use more --days.")

    bt = FuturesBacktester(plan, trade, starting_cash=args.cash, risk_pct=args.risk,
                           maker_fee_pct=args.maker_fee, taker_fee_pct=args.taker_fee,
                           slippage_pct=args.slippage, funding_pct_8h=args.funding,
                           be_after_r=args.be_after_r, be_offset_pct=args.be_offset,
                           re_arm_atr=args.re_arm_atr, max_per_trend=args.max_per_trend,
                           warmup_candles=warmup, strategy_name=strategy.describe())
    result = bt.run()
    print_report(result)
    folder = save_results(result, plan)
    pivot_list(signal, plan, strategy.n).to_csv(folder / "vwap_pivots.csv", index=False)
    print(f"\nSaved in ./{folder}/: vwap_trades.csv, vwap_equity.csv, vwap_structure.csv, vwap_pivots.csv")


if __name__ == "__main__":
    main()
