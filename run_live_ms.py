"""
Run the MS structure strategy on OKX perpetual futures. DEMO trading unless --live.
Same options and defaults as run_backtest_ms.py (the Pine inputs).

Examples:
  python run_live_ms.py
  python run_live_ms.py --use-target --min-rr 0
  python run_live_ms.py --live            (real money, asks for confirmation)

More coins: start one bot per coin, each in its own terminal (own state and log
file). Every bot risks --risk % of the whole account; --max-open caps how many
positions the bots hold together.
  python run_live_ms.py --base BTC
  python run_live_ms.py --base ETH

Stop with Ctrl+C. Open positions keep their stop-loss (and take-profit) on OKX,
but the trailing stop only moves while the bot is running.
"""
import argparse
import logging

from dotenv import load_dotenv

from okx_ms_trader import OKXStructureTrader
from run_backtest_ms import add_strategy_args, exit_settings, make_exits, make_strategy
from run_live import setup_logging


def main():
    p = argparse.ArgumentParser(description="Run the MS structure strategy on OKX perpetuals")
    add_strategy_args(p)
    g = p.add_argument_group("live")
    g.add_argument("--history-days", type=int, default=120,
                   help="history loaded to build the structure (the HTF trend needs several 7-day swings)")
    g.add_argument("--check-every", type=int, default=30, help="seconds between checks")
    g.add_argument("--max-open", type=int, default=3,
                   help="no new entry while this many positions are open on the account (all coins)")
    g.add_argument("--live", action="store_true", help="trade with REAL money")
    args = p.parse_args()
    strategy = make_strategy(args)
    exits = make_exits(args)

    if args.live:
        print("\n  WARNING: LIVE MODE. REAL futures orders with REAL money and leverage.")
        print(f"  {strategy.describe()}\n  {exit_settings(args)}\n"
              f"  {args.base}/USDT perpetual {args.timeframe}\n")
        if input("  Type YES to continue: ").strip() != "YES":
            print("Cancelled.")
            return

    load_dotenv()
    setup_logging(f"ms_{args.base}", args.base)
    trader = OKXStructureTrader(strategy, args.base, args.timeframe, signal_kind=args.signal_data,
                                exchanges=args.exchanges, risk_pct=args.risk,
                                max_leverage=args.max_leverage, long_exit=exits["long"],
                                short_exit=exits["short"], max_open=args.max_open, live=args.live,
                                check_every=args.check_every, history_days=args.history_days)
    try:
        trader.run()
    except KeyboardInterrupt:
        logging.info("Stopped by you. Any open position keeps its stop on OKX "
                     "(the trailing stop only moves while the bot runs).")


if __name__ == "__main__":
    main()
