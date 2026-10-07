"""
Run the MS VWAP strategy on OKX perpetual futures. DEMO trading unless --live.

Examples:
  python run_live_vwap.py
  python run_live_vwap.py --timeframe 4h --target-r 3
  python run_live_vwap.py --live            (real money, asks for confirmation)

Stop with Ctrl+C. Open positions keep their stop-loss and take-profit on OKX.
"""
import argparse
import logging

from dotenv import load_dotenv

from okx_futures_trader import OKXFuturesTrader
from run_backtest_vwap import add_strategy_args, make_strategy
from run_live import setup_logging


def main():
    p = argparse.ArgumentParser(description="Run the MS VWAP strategy on OKX perpetuals")
    add_strategy_args(p)
    g = p.add_argument_group("live")
    g.add_argument("--history-days", type=int, default=90, help="history loaded to build the structure")
    g.add_argument("--check-every", type=int, default=30, help="seconds between checks")
    g.add_argument("--live", action="store_true", help="trade with REAL money")
    args = p.parse_args()
    strategy = make_strategy(args)

    if args.live:
        print("\n  WARNING: LIVE MODE. REAL futures orders with REAL money and leverage.")
        print(f"  {strategy.describe()}\n  {args.base}/USDT perpetual {args.timeframe}, "
              f"risk {args.risk}% per trade\n")
        if input("  Type YES to continue: ").strip() != "YES":
            print("Cancelled.")
            return

    load_dotenv()
    setup_logging()
    trader = OKXFuturesTrader(strategy, args.base, args.timeframe, signal_kind=args.signal_data,
                              exchanges=args.exchanges, risk_pct=args.risk,
                              be_after_r=args.be_after_r, be_offset_pct=args.be_offset,
                              re_arm_atr=args.re_arm_atr, max_per_trend=args.max_per_trend,
                              live=args.live, check_every=args.check_every,
                              history_days=args.history_days)
    try:
        trader.run()
    except KeyboardInterrupt:
        logging.info("Stopped by you. Any open position keeps its stop and target on OKX.")


if __name__ == "__main__":
    main()
