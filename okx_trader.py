"""
Live/demo trading engine: runs a strategy against OKX in a loop.

Every cycle (default every 30 seconds) it:
  1. Checks the stop-loss if a position is open.
  2. When a new candle has closed: downloads the signal candles (OKX index or
     spot), asks the strategy for a signal, and buys or sells on SPOT if needed.
The stop-loss and all orders always use the tradable spot price.
Position and entry price are saved to a state file, so a restart doesn't
forget what the bot holds or buy twice.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ccxt

from market_data import fetch_recent, timeframe_ms
from strategies.base import BUY, SELL, Strategy

log = logging.getLogger("trader")


def create_exchange(live: bool) -> ccxt.okx:
    """Connect with demo keys (default) or live keys from the .env file."""
    prefix = "OKX_LIVE_" if live else "OKX_DEMO_"
    key, secret, passphrase = (os.getenv(prefix + n) for n in ("API_KEY", "SECRET", "PASSPHRASE"))
    if not all((key, secret, passphrase)):
        raise SystemExit(f"Missing {prefix}API_KEY, {prefix}SECRET or {prefix}PASSPHRASE "
                         "in your .env file (see .env.example).")
    exchange = ccxt.okx({"apiKey": key, "secret": secret, "password": passphrase,
                         "enableRateLimit": True})
    if not live:
        exchange.set_sandbox_mode(True)  # OKX demo trading: paper money
    return exchange


class OKXTrader:
    def __init__(self, strategy: Strategy, symbol: str, timeframe: str,
                 order_size: float, stop_loss_pct: Optional[float] = None,
                 live: bool = False, check_every: int = 30, signal_data: str = "spot"):
        self.strategy = strategy
        self.signal_data = signal_data
        self.symbol = symbol
        self.timeframe = timeframe
        self.order_size = order_size
        self.stop_loss = stop_loss_pct / 100 if stop_loss_pct else None
        self.live = live
        self.check_every = check_every

        self.exchange = create_exchange(live)
        self.exchange.load_markets()
        if symbol not in self.exchange.markets:
            raise SystemExit(f"Unknown symbol {symbol} on OKX.")
        market = self.exchange.market(symbol)
        self.base, self.quote = market["base"], market["quote"]
        self.min_amount = (market.get("limits", {}).get("amount", {}) or {}).get("min") or 0
        if order_size < self.min_amount:
            raise SystemExit(f"--size {order_size} is below OKX's minimum of "
                             f"{self.min_amount} {self.base} for {symbol}.")
        self.bar_ms = timeframe_ms(self.exchange, timeframe)

        mode = "live" if live else "demo"
        self.state_file = Path("state") / f"{symbol.replace('/', '-')}_{mode}.json"
        self.position = 0.0
        self.entry_price: Optional[float] = None
        self.entry_time: Optional[str] = None
        self.last_candle_time: Optional[int] = None

    # ---------------------------------------------------------------- main loop
    def run(self):
        log.info("Starting %s on %s %s | signals: %s price | size %s %s | stop-loss %s | mode: %s",
                 self.strategy.describe(), self.symbol, self.timeframe, self.signal_data,
                 self.order_size, self.base,
                 f"{self.stop_loss:.1%}" if self.stop_loss else "off",
                 "LIVE (real money)" if self.live else "DEMO")
        self._load_state()
        log.info("Balance: %.6f %s free, %.2f %s free",
                 self._free(self.base), self.base, self._free(self.quote), self.quote)

        while True:
            try:
                self.tick()
            except ccxt.NetworkError as e:
                log.warning("Network problem: %s. Retrying in 15s.", e)
                time.sleep(15)
                continue
            except ccxt.ExchangeError as e:
                log.error("Exchange error: %s. Retrying in 60s.", e)
                time.sleep(60)
                continue
            except Exception:
                log.exception("Unexpected error. Retrying in 60s.")
                time.sleep(60)
                continue
            time.sleep(self.check_every)

    def tick(self):
        if self.position > 0 and self.stop_loss:
            self._check_stop_loss()

        # The next candle closes at last_candle_time + 2 bars; don't fetch before that.
        now = self.exchange.milliseconds()
        if self.last_candle_time and now < self.last_candle_time + 2 * self.bar_ms + 3000:
            return

        candles = fetch_recent(self.exchange, self.symbol, self.timeframe,
                               self.strategy.lookback, price=self.signal_data)
        if len(candles) < self.strategy.lookback:
            log.warning("Only %d candles available, need %d. Waiting.",
                        len(candles), self.strategy.lookback)
            return
        newest = int(candles.index[-1].timestamp() * 1000)
        if newest == self.last_candle_time:
            return  # exchange hasn't published the new candle yet
        self.last_candle_time = newest

        signal = self.strategy.generate_signal(candles)
        log.info("Candle %s closed | %s close %.4f | signal: %s | holding: %s %s",
                 candles.index[-1].strftime("%Y-%m-%d %H:%M"), self.signal_data,
                 candles["close"].iloc[-1], signal or "none", f"{self.position:.6f}", self.base)

        if signal == BUY:
            if self.position > 0:
                log.info("Already holding, ignoring buy signal.")
            else:
                self._buy()
        elif signal == SELL:
            if self.position == 0:
                log.info("Nothing to sell, ignoring sell signal.")
            else:
                self._sell("signal")

    # ------------------------------------------------------------------ orders
    def _buy(self):
        ticker = self.exchange.fetch_ticker(self.symbol)
        price = ticker.get("ask") or ticker["last"]
        amount = float(self.exchange.amount_to_precision(self.symbol, self.order_size))
        cost = amount * price
        free_quote = self._free(self.quote)
        if free_quote < cost * 1.01:
            log.warning("Not enough %s to buy: need ~%.2f, have %.2f.", self.quote, cost, free_quote)
            return

        log.info("BUY %s %s at ~%.4f (~%.2f %s)", amount, self.base, price, cost, self.quote)
        # OKX sizes spot market buys in the quote currency; passing the price lets
        # ccxt convert amount * price into the right order cost.
        order = self.exchange.create_order(self.symbol, "market", "buy", amount, price)
        order = self._wait_for_fill(order)
        filled = self._net_filled(order)
        if filled <= 0:
            log.error("Buy order did not fill: %s", order.get("status"))
            return

        self.position = filled
        self.entry_price = order.get("average") or price
        self.entry_time = datetime.now(timezone.utc).isoformat()
        self._save_state()
        log.info("Bought %.6f %s at %.4f. Stop-loss at %s.", filled, self.base, self.entry_price,
                 f"{self.entry_price * (1 - self.stop_loss):.4f}" if self.stop_loss else "off")

    def _sell(self, reason: str):
        amount = min(self.position, self._free(self.base))
        try:
            amount = float(self.exchange.amount_to_precision(self.symbol, amount))
        except ccxt.BaseError:
            amount = 0.0
        if amount <= 0 or amount < self.min_amount:
            log.warning("Position (%.8f %s) is too small to sell. Clearing it.", amount, self.base)
            self._clear_position()
            return

        log.info("SELL %s %s (reason: %s)", amount, self.base, reason)
        order = self.exchange.create_order(self.symbol, "market", "sell", amount)
        order = self._wait_for_fill(order)
        price = order.get("average") or self.exchange.fetch_ticker(self.symbol)["last"]
        if self.entry_price:
            log.info("Sold at %.4f. Trade result: %+.2f%% (before fees).",
                     price, (price / self.entry_price - 1) * 100)
        self._clear_position()

    def _check_stop_loss(self):
        price = self.exchange.fetch_ticker(self.symbol)["last"]
        stop = self.entry_price * (1 - self.stop_loss)
        if price <= stop:
            log.warning("STOP-LOSS hit: price %.4f <= stop %.4f", price, stop)
            self._sell("stop-loss")

    def _wait_for_fill(self, order: dict, tries: int = 10) -> dict:
        latest = order
        for _ in range(tries):
            try:
                latest = self.exchange.fetch_order(order["id"], self.symbol)
            except ccxt.OrderNotFound:
                pass
            if latest.get("status") in ("closed", "canceled"):
                break
            time.sleep(1)
        return latest

    def _net_filled(self, order: dict) -> float:
        """Filled amount minus fees charged in the coin itself (OKX does this on buys)."""
        filled = float(order.get("filled") or 0)
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        for fee in fees:
            if fee and fee.get("currency") == self.base and fee.get("cost"):
                filled -= abs(float(fee["cost"]))
        return max(filled, 0.0)

    def _free(self, currency: str) -> float:
        balance = self.exchange.fetch_balance()
        return float((balance.get(currency) or {}).get("free") or 0)

    # ------------------------------------------------------------------- state
    def _load_state(self):
        if not self.state_file.exists():
            return
        state = json.loads(self.state_file.read_text())
        self.position = float(state.get("position", 0))
        self.entry_price = state.get("entry_price")
        self.entry_time = state.get("entry_time")
        if self.position > 0:
            free = self._free(self.base)
            if free < self.min_amount or free < self.position * 0.5:
                log.warning("Saved position %.6f %s but only %.6f in balance "
                            "(sold manually?). Resetting.", self.position, self.base, free)
                self._clear_position()
            else:
                self.position = min(self.position, free)
                log.info("Resumed open position: %.6f %s bought at %.4f.",
                         self.position, self.base, self.entry_price)

    def _save_state(self):
        self.state_file.parent.mkdir(exist_ok=True)
        self.state_file.write_text(json.dumps({
            "symbol": self.symbol, "position": self.position,
            "entry_price": self.entry_price, "entry_time": self.entry_time}, indent=2))

    def _clear_position(self):
        self.position, self.entry_price, self.entry_time = 0.0, None, None
        self._save_state()
