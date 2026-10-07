"""
Run the MS structure strategy on OKX perpetual futures. DEMO trading unless --live.
Same options and defaults as run_backtest_ms.py (the Pine inputs).

Examples:
  python run_live_ms.py
  python run_live_ms.py --use-target --min-rr 0
  python run_live_ms.py --live            (real money, asks for confirmation)

Stop with Ctrl+C. Open positions keep their stop-loss (and take-profit) on OKX,
but the trailing stop only moves while the bot is running.
"""
import argparse
import logging

from dotenv import load_dotenv

from okx_ms_trader import OKXStructureTrader
from run_backtest_ms import add_strategy_args, exit_settings, make_strategy
from run_live import setup_logging


def main():
    p = argparse.ArgumentParser(description="Run the MS structure strategy on OKX perpetuals")
    add_strategy_args(p)
    g = p.add_argument_group("live")
    g.add_argument("--history-days", type=int, default=60, help="history loaded to build the structure")
    g.add_argument("--check-every", type=int, default=30, help="seconds between checks")
    g.add_argument("--live", action="store_true", help="trade with REAL money")
    args = p.parse_args()
    strategy = make_strategy(args)

    if args.live:
        print("\n  WARNING: LIVE MODE. REAL futures orders with REAL money and leverage.")
        print(f"  {strategy.describe()}\n  {exit_settings(args)}\n"
              f"  {args.base}/USDT perpetual {args.timeframe}\n")
        if input("  Type YES to continue: ").strip() != "YES":
            print("Cancelled.")
            return

    load_dotenv()
    setup_logging()
    trader = OKXStructureTrader(strategy, args.base, args.timeframe, signal_kind=args.signal_data,
                                exchanges=args.exchanges, risk_pct=args.risk,
                                max_leverage=args.max_leverage, use_target=args.use_target,
                                be_after_r=args.be_after_r, trail=not args.no_trail,
                                trail_after_r=args.trail_after_r, trail_candles=args.trail_candles,
                                stop_pct=args.stop_offset, live=args.live,
                                check_every=args.check_every, history_days=args.history_days)
    try:
        trader.run()
    except KeyboardInterrupt:
        logging.info("Stopped by you. Any open position keeps its stop on OKX "
                     "(the trailing stop only moves while the bot runs).")


if __name__ == "__main__":
    main()
