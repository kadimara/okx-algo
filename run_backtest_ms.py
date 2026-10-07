"""
Backtest the MS structure strategy on OKX perpetual futures (same rules as the
"MS strategy" Pine script). Defaults = the Pine inputs you settled on.

Examples:
  python run_backtest_ms.py
  python run_backtest_ms.py --signal-data perp --maker-fee 0.05 --taker-fee 0.05 --slippage 0   (compare with TradingView)
  python run_backtest_ms.py --use-target --min-rr 0
  python run_backtest_ms.py --no-trail --use-target --be-after-r 2
"""
import argparse

import ccxt

from futures_backtester import print_report
from ms_backtester import MSBacktester, save_ms_results
from structure import bos_pivot_list
from strategies.ms_structure import MSStructure
from vwap_data import SIGNAL_KINDS, cached, fetch_perp, fetch_signal


def add_strategy_args(p):
    g = p.add_argument_group("market and data")
    g.add_argument("--base", default="BTC", help="coin, traded as <base>/USDT perpetual")
    g.add_argument("--timeframe", default="2h", help="15m 30m 1h 2h 4h 1d ...")
    g.add_argument("--signal-data", default="index", choices=SIGNAL_KINDS,
                   help="index = OKX index + aggregated volume, perp = perpetual price + own volume")
    g.add_argument("--exchanges", default=None, help="exchanges for aggregated volume (see aggregate_volume.py)")
    g = p.add_argument_group("structure and entries (same names as the Pine inputs)")
    g.add_argument("--swing-days", type=float, default=2.0, help="swing size in days")
    g.add_argument("--side", default="both", choices=["both", "long", "short"], help="take longs / shorts")
    g.add_argument("--entry-offset", type=float, default=0.35,
                   help="%% above the HL body bottom (long) / below the LH body top (short)")
    g.add_argument("--stop-offset", type=float, default=0.15,
                   help="%% beyond the HL low / LH high; also used for the trailing stop")
    g.add_argument("--min-rr", type=float, default=3.0, help="skip trades below this R, 0 = off")
    g.add_argument("--target-offset", type=float, default=0.0,
                   help="%% before the target (previous swing body edge)")
    g.add_argument("--risk", type=float, default=1.0, help="risk per trade in %% of equity")
    g.add_argument("--max-leverage", type=float, default=2.0, help="cap on position value / equity")
    g = p.add_argument_group("exits")
    g.add_argument("--use-target", action="store_true", help="place the take-profit (off by default)")
    g.add_argument("--be-after-r", type=float, default=0.0, help="move stop to entry after R, 0 = off")
    g.add_argument("--no-trail", action="store_true", help="switch the candle trailing stop off")
    g.add_argument("--trail-after-r", type=float, default=0.0, help="start trailing after R")
    g.add_argument("--trail-candles", type=int, default=12, help="trail behind the last N candles")


def make_strategy(args) -> MSStructure:
    return MSStructure(ccxt.Exchange.parse_timeframe(args.timeframe), swing_days=args.swing_days,
                       entry_pct=args.entry_offset, stop_pct=args.stop_offset,
                       target_pct=args.target_offset, min_rr=args.min_rr,
                       allow_long=args.side in ("both", "long"), allow_short=args.side in ("both", "short"))


def exit_settings(args) -> str:
    parts = ["target on" if args.use_target else "no target"]
    if args.be_after_r > 0:
        parts.append(f"break-even after {args.be_after_r}R")
    if not args.no_trail:
        parts.append(f"trail {args.trail_candles} candles from {args.trail_after_r}R")
    return ", ".join(parts) + f", risk {args.risk}%, max {args.max_leverage}x"


def main():
    p = argparse.ArgumentParser(description="Backtest the MS structure strategy on OKX perpetuals")
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

    name = f"{strategy.describe()} | {exit_settings(args)}"
    print(f"Analyzing ({name})...")
    plan = strategy.analyze(signal)
    warmup = max(int(args.warmup_days * 86400 / tf_sec), strategy.warmup_candles)
    if warmup >= len(plan):
        raise SystemExit(f"Not enough data: {len(plan)} candles, warm-up needs {warmup}. Use more --days.")

    bt = MSBacktester(plan, trade, starting_cash=args.cash, risk_pct=args.risk,
                      max_leverage=args.max_leverage, maker_fee_pct=args.maker_fee,
                      taker_fee_pct=args.taker_fee, slippage_pct=args.slippage,
                      funding_pct_8h=args.funding, use_target=args.use_target,
                      be_after_r=args.be_after_r, trail=not args.no_trail,
                      trail_after_r=args.trail_after_r, trail_candles=args.trail_candles,
                      stop_pct=args.stop_offset, warmup_candles=warmup, strategy_name=name)
    result = bt.run()
    print_report(result)
    folder = save_ms_results(result, plan)
    bos_pivot_list(signal, plan).to_csv(folder / "ms_pivots.csv", index=False)
    print(f"\nSaved in ./{folder}/: ms_trades.csv, ms_equity.csv, ms_structure.csv, ms_pivots.csv")


if __name__ == "__main__":
    main()
