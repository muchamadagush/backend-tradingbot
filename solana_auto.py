import asyncio
import json
import os
import time
from typing import List, Dict, Optional

import aiohttp
import pandas as pd
import pandas_ta as ta
from solders.pubkey import Pubkey

from jupiter_handler import jupiter_bot, TOKENS
import solana_tracker
from solana_scanner import scanner
from solana_safety import safety_checker
from global_kill_switch import kill_switch
import sys_logger

from strategies.solana_trader import SolanaTraderStrategy
from risk_manager import RiskManager, BotType, BOT_RISK_PROFILES


# =============================
# CONFIG (HIGH-WIN-RATE v3 — TUNED FOR RP 200.000 / ~0.10 SOL CAPITAL)
# =============================
POSITIONS_FILE = "solana_positions.json"
MAX_POSITIONS = int(os.getenv("SOLANA_MAX_POSITIONS", "1"))  # Focused 1 position at a time for micro-capital
CANDLE_LIMIT = 120

# Position Sizing & Gas Reserve Config (Tuned for ~0.10 SOL Phantom Wallet)
POSITION_SIZE_PCT = float(os.getenv("SOLANA_POSITION_SIZE_PCT", "0.25"))  # 25% of SOL balance per focused trade
DEFAULT_SOL_BALANCE = 0.10  # Realistic fallback for Rp 200.000 capital (~0.10 SOL)
MIN_TRADE_SOL = float(os.getenv("SOLANA_MIN_TRADE_SOL", "0.025"))         # Optimal micro-capital trade size (0.025 SOL)
MAX_TRADE_SOL = float(os.getenv("SOLANA_MAX_TRADE_SOL", "0.10"))          # Safety cap per trade
RESERVE_GAS_SOL = float(os.getenv("SOLANA_RESERVE_GAS_SOL", "0.020"))     # Mandatory 0.020 SOL buffer for ATA rent & sell gas

# Micro-Capital Single Exit Mode (Saves 50% exit swap gas fees by avoiding 2-step partial sells)
MICRO_CAPITAL_SINGLE_EXIT = os.getenv("SOLANA_MICRO_SINGLE_EXIT", "true").lower() == "true"

# 3-Tier Fast Trailing Stop Config (High-Win-Rate v3)
TRAILING_STOP_ACTIVATION_PCT = 6.5   # Activate trailing stop at +6.5% profit (locks +4.5% minimum)
TRAILING_STOP_DISTANCE_PCT = 2.0     # Base trail distance: 2.0% (tightens to 1.5% at +10% and 1.2% at +15%)


# =============================
# SOLANA AUTO BOT
# =============================
class SolanaAutoBot:
    def __init__(self):
        self.is_running = False
        self.mode = "MANUAL_TARGET"
        self.strategy_name = "TRADER"

        self.target_token: Optional[str] = None
        self.target_address: Optional[str] = None
        self.target_pair_address: Optional[str] = None

        self.active_positions: List[Dict] = []
        self.recently_sold: List[str] = []

        self.trader_strategy = SolanaTraderStrategy()
        self.trader_strategy.MICRO_CAPITAL_SINGLE_EXIT = MICRO_CAPITAL_SINGLE_EXIT

        # Track SOL balance for percentage-based sizing
        self.cached_sol_balance = DEFAULT_SOL_BALANCE

        # Trailing stop state per token (uses PnL % instead of raw SOL so partial sells don't false-trigger!)
        # {token: {"high_water_mark_pnl_pct": float, "trailing_active": bool}}
        self.trailing_stop_state: Dict[str, Dict] = {}

        # Per-bot risk manager for Trader (scaled to SOL balance)
        self.risk_manager = RiskManager(
            initial_balance=DEFAULT_SOL_BALANCE,
            bot_type=BotType.TRADER
        )

        self.load_positions()

    # =============================
    # POSITION STORAGE
    # =============================
    def load_positions(self):
        if not os.path.exists(POSITIONS_FILE):
            self.active_positions = []
            return

        try:
            with open(POSITIONS_FILE, "r") as f:
                data = json.load(f)
                self.active_positions = data if isinstance(data, list) else []
            sys_logger.add_log(f"📂 Loaded {len(self.active_positions)} positions")
        except Exception as e:
            sys_logger.add_log(f"⚠️ Position load failed: {e}")
            self.active_positions = []

    def save_positions(self):
        with open(POSITIONS_FILE, "w") as f:
            json.dump(self.active_positions, f, indent=4)

    def add_position(self, pos: Dict):
        self.active_positions.append(pos)
        self.save_positions()

    def remove_position(self, token: str):
        self.active_positions = [
            p for p in self.active_positions if p["token"] != token
        ]
        self.save_positions()

    # =============================
    # SOL BALANCE & GAS-SAFE POSITION SIZING (v3)
    # =============================
    async def refresh_sol_balance(self) -> float:
        """Fetch current SOL balance and sync RiskManager capital."""
        try:
            balances = await jupiter_bot.get_token_balances()
            if balances and isinstance(balances, dict):
                sol_balance = float(balances.get("SOL", 0) or 0)
                if sol_balance > 0:
                    self.cached_sol_balance = sol_balance
                    if self.risk_manager.initial_balance == DEFAULT_SOL_BALANCE and sol_balance != DEFAULT_SOL_BALANCE:
                        self.risk_manager.initial_balance = sol_balance
                        self.risk_manager.current_balance = sol_balance
                        self.risk_manager.peak_balance = max(self.risk_manager.peak_balance, sol_balance)
                    return sol_balance
        except Exception as e:
            sys_logger.add_log(f"⚠️ Balance fetch failed: {e}")
        return self.cached_sol_balance

    def calculate_position_size(self, balance: float) -> float:
        """
        Calculate position size while strictly protecting RESERVE_GAS_SOL (0.020 SOL)
        so the wallet never runs out of SOL for ATA rent or sell transaction fees.
        """
        tradable_sol = max(0.0, balance - RESERVE_GAS_SOL)
        if tradable_sol < 0.01:
            return 0.0

        desired_size = balance * POSITION_SIZE_PCT
        clamped_size = max(MIN_TRADE_SOL, min(MAX_TRADE_SOL, desired_size))
        final_size = min(clamped_size, tradable_sol)
        return round(final_size, 6)

    # =============================
    # TOKEN DECIMALS (CRITICAL FIX)
    # =============================
    async def get_token_decimals(self, address: str) -> int:
        try:
            resp = await safety_checker.client.get_token_supply(
                Pubkey.from_string(address)
            )
            return int(resp.value.decimals)
        except Exception:
            return 6

    # =============================
    # MARKET DATA (GECKOTERMINAL)
    # =============================
    async def _fetch_candles(self, pool_id: str, limit: int = CANDLE_LIMIT):
        clean_pool = pool_id.replace("solana_", "") if pool_id else pool_id
        url = (
            "https://api.geckoterminal.com/api/v2/"
            f"networks/solana/pools/{clean_pool}/ohlcv/minute"
            f"?limit={limit}"
        )
        headers = {
            "Accept": "application/json;version=20230302",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        sys_logger.add_log(f"❌ Candle HTTP {resp.status} for pool {clean_pool}")
                        return None
                    data = await resp.json()

            rows = data["data"]["attributes"]["ohlcv_list"]
            df = pd.DataFrame(
                rows,
                columns=["ts", "open", "high", "low", "close", "volume"]
            )
            df.sort_values("ts", inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df

        except Exception as e:
            sys_logger.add_log(f"❌ Candle fetch error: {e}")
            return None

    # =============================
    # MAIN LOOP
    # =============================
    async def start(self, mode="MANUAL_TARGET", token_symbol=None):
        if self.is_running:
            return

        self.is_running = True
        self.mode = mode

        if token_symbol:
            self.target_token = token_symbol

        sys_logger.add_log(
            f"🚀 AUTO STARTED | Mode={mode} Strategy=SOLANA_TRADER_v3 (MaxPos={MAX_POSITIONS}, MinSOL={MIN_TRADE_SOL})"
        )

        while self.is_running:
            try:
                await self.logic_loop()
            except Exception as e:
                sys_logger.add_log(f"⚠️ Loop error: {e}")
            await asyncio.sleep(5)

    def stop(self):
        self.is_running = False
        sys_logger.add_log("🛑 AUTO STOPPED")

    # =============================
    # CORE LOGIC
    # =============================
    async def logic_loop(self):
        # ---- 1. Exit management always runs first (even if kill-switch or cooldown is active) ----
        if self.active_positions:
            await self.check_all_exit_conditions()

        # ---- 2. Global Kill Switch Check ----
        if not kill_switch.is_trading_allowed():
            sys_logger.add_log("⛔ Solana Trader blocked by Global Kill Switch")
            return

        # ---- 3. Per-Bot Risk & Cooldown Check ----
        if not self.risk_manager.can_trade_new():
            if self.risk_manager.is_in_cooldown():
                remaining = self.risk_manager.get_cooldown_remaining()
                sys_logger.add_log(f"⏸️ Trader cooldown: {remaining}s remaining")
            else:
                sys_logger.add_log("⚠️ Trader risk limit reached")
            return

        if len(self.active_positions) >= MAX_POSITIONS:
            return

        # ---- 4. Auto Discovery ----
        if self.mode in ["AUTO_DISCOVERY", "AUTO_HUNT"]:
            ignore = self.recently_sold + [
                p["token"] for p in self.active_positions
            ]

            gem = await scanner.find_gem(
                ignore_list=ignore,
                mode="TRADER"
            )

            if not gem:
                return

            self.target_token = gem["symbol"]
            self.target_address = gem["address"]
            self.target_pair_address = gem["pair_address"]

            TOKENS[self.target_token] = self.target_address

            await self.do_buy()

        # ---- 5. Manual Target ----
        elif self.mode == "MANUAL_TARGET" and not self.active_positions:
            await self.do_buy()
            self.stop()

    # =============================
    # BUY LOGIC
    # =============================
    async def do_buy(self):
        if not all([
            self.target_token,
            self.target_address,
            self.target_pair_address
        ]):
            sys_logger.add_log("❌ Target not fully defined")
            return

        sys_logger.add_log(f"⚔️ Analyzing {self.target_token}")

        # ---- SAFETY CHECK ----
        is_safe, reason, _ = await safety_checker.check_token(
            self.target_address
        )
        if not is_safe:
            sys_logger.add_log(f"🛑 Unsafe token: {reason}")
            return

        # ---- CANDLES ----
        candles = await self._fetch_candles(self.target_pair_address)
        if candles is None or candles.empty:
            return

        # ---- STRATEGY ----
        should_buy, reason = self.trader_strategy.should_buy({
            "token": self.target_token,
            "df": candles
        })

        if not should_buy:
            sys_logger.add_log(f"⏸️ Skip {self.target_token}: {reason}")
            return

        # Extract entry candle price and ATR so dynamic ATR SL in SolanaTraderStrategy works!
        entry_candle_price = float(candles["close"].iloc[-1])
        atr_series = ta.atr(
            pd.to_numeric(candles["high"], errors="coerce"),
            pd.to_numeric(candles["low"], errors="coerce"),
            pd.to_numeric(candles["close"], errors="coerce"),
            length=14
        )
        entry_atr = float(atr_series.iloc[-1]) if atr_series is not None and not pd.isna(atr_series.iloc[-1]) else 0.0

        # ---- GAS-SAFE POSITION SIZING (v3) ----
        sol_balance = await self.refresh_sol_balance()
        trade_amount = self.calculate_position_size(sol_balance)
        if trade_amount < 0.01:
            sys_logger.add_log(
                f"⚠️ Insufficient tradable SOL ({sol_balance:.4f} SOL total, keeping {RESERVE_GAS_SOL:.3f} SOL gas reserve)"
            )
            return

        sys_logger.add_log(
            f"💰 Position size: {trade_amount:.4f} SOL (Wallet: {sol_balance:.4f} SOL, Gas Reserve: {RESERVE_GAS_SOL:.3f} SOL)"
        )

        # ---- JUPITER QUOTE WITH FRESHNESS VALIDATION ----
        QUOTE_MAX_AGE_SECONDS = 5
        MAX_REQUOTE_ATTEMPTS = 3

        default_slippage = int(os.getenv("JUPITER_DEFAULT_SLIPPAGE_BPS", "50"))
        quote = None
        for attempt in range(MAX_REQUOTE_ATTEMPTS):
            quote = await jupiter_bot.get_quote(
                "SOL",
                self.target_address,
                trade_amount,
                slippage_bps=default_slippage
            )

            if "error" in quote:
                sys_logger.add_log(f"❌ Quote error: {quote['error']}")
                return

            quote_age = time.time() - quote.get("quote_timestamp", 0)
            if quote_age > QUOTE_MAX_AGE_SECONDS:
                sys_logger.add_log(f"⚠️ Quote expired ({quote_age:.1f}s > {QUOTE_MAX_AGE_SECONDS}s), re-quoting... ({attempt+1}/{MAX_REQUOTE_ATTEMPTS})")
                await asyncio.sleep(0.5)
                continue

            break
        else:
            sys_logger.add_log(f"❌ Failed to get fresh quote after {MAX_REQUOTE_ATTEMPTS} attempts")
            return

        effective_price = quote.get("effective_price", 0)
        sys_logger.add_log(f"📊 Quote: {trade_amount:.4f} SOL → {quote['output_estimate']} {self.target_token} (price: {effective_price:.6f})")

        # ---- EXECUTE BUY ----
        res = await jupiter_bot.execute_swap(quote["raw_data"])
        if res.get("status") != "success":
            sys_logger.add_log("❌ Buy failed")
            return

        output_amt = float(quote["output_estimate"].replace(",", ""))

        pos = {
            "token": self.target_token,
            "address": self.target_address,
            "pair_address": self.target_pair_address,
            "token_amount": output_amt,
            "entry_price_sol": trade_amount,
            "entry_price": entry_candle_price,
            "atr": entry_atr,
            "entry_time": time.time(),
            "strategy": "SOLANA_TRADER",
            "tp1_hit": False,
            "be_shield_active": False,
            "txid": res["txid"]
        }

        self.trader_strategy.on_buy(pos)
        self.add_position(pos)

        solana_tracker.save_trade(
            res["txid"],
            "SOL",
            self.target_token,
            trade_amount,
            output_amt,
            token_address=self.target_address
        )

        sys_logger.add_log(
            f"✅ BOUGHT {self.target_token} for {trade_amount:.4f} SOL | Dynamic SL: {pos.get('dynamic_sl_pct', -5.5):.2f}% | BE Shield @ +{self.trader_strategy.EARLY_BE_TRIGGER_PCT}%"
        )

    # =============================
    # SELL LOGIC (EARLY BREAKEVEN SHIELD + 3-TIER ADAPTIVE TRAILING STOP)
    # =============================
    async def check_all_exit_conditions(self):
        for pos in self.active_positions[:]:
            await self.check_single_exit(pos)

    async def check_single_exit(self, pos: Dict):
        token = pos["token"]
        address = pos["address"]
        amount = pos["token_amount"]

        default_slippage = int(os.getenv("JUPITER_DEFAULT_SLIPPAGE_BPS", "50"))
        quote = await jupiter_bot.get_quote(
            address,
            "SOL",
            amount,
            slippage_bps=default_slippage
        )

        if "error" in quote:
            return

        current_sol = float(quote["output_estimate"].replace(",", ""))
        entry = float(pos["entry_price_sol"])
        if entry <= 0:
            return

        pnl_pct = ((current_sol - entry) / entry) * 100.0
        pos["pnl_pct"] = pnl_pct

        # ---- STAGE 1: EARLY BREAKEVEN SHIELD (+4.2% -> Locks +1.0% Net Profit) ----
        if pnl_pct >= self.trader_strategy.EARLY_BE_TRIGGER_PCT and not pos.get("be_shield_active"):
            pos["be_shield_active"] = True
            pos["dynamic_sl_pct"] = max(
                float(pos.get("dynamic_sl_pct", self.trader_strategy.HARD_SL_PCT)),
                self.trader_strategy.EARLY_BE_LOCK_PCT
            )
            self.save_positions()
            sys_logger.add_log(
                f"🛡️ {token}: Early Breakeven Shield activated at +{pnl_pct:.1f}% (SL locked at +{pos['dynamic_sl_pct']:.1f}%)"
            )

        # ========== 3-TIER ADAPTIVE PNL-% TRAILING STOP LOGIC ==========
        trailing_exit = False
        trailing_reason = ""

        if token not in self.trailing_stop_state:
            self.trailing_stop_state[token] = {
                "high_water_mark_pnl_pct": pnl_pct,
                "trailing_active": False
            }

        ts_state = self.trailing_stop_state[token]

        # Update peak PnL %
        if pnl_pct > ts_state["high_water_mark_pnl_pct"]:
            ts_state["high_water_mark_pnl_pct"] = pnl_pct

        # Activate trailing stop when profit reaches threshold (+6.5%)
        if pnl_pct >= TRAILING_STOP_ACTIVATION_PCT and not ts_state["trailing_active"]:
            ts_state["trailing_active"] = True
            sys_logger.add_log(f"🎯 {token}: Fast Trailing Stop activated at +{pnl_pct:.1f}%")

        # Check 3-tier adaptive trailing stop distance
        if ts_state["trailing_active"]:
            hwm_pnl = ts_state["high_water_mark_pnl_pct"]
            if hwm_pnl >= 15.0:
                effective_trail_dist = 1.2
            elif hwm_pnl >= 10.0 or pos.get("tp1_hit"):
                effective_trail_dist = 1.5
            else:
                effective_trail_dist = TRAILING_STOP_DISTANCE_PCT

            trail_stop_pnl = max(hwm_pnl - effective_trail_dist, self.trader_strategy.EARLY_BE_LOCK_PCT)

            if pnl_pct <= trail_stop_pnl:
                trailing_exit = True
                trailing_reason = f"TRAILING_STOP (peak +{hwm_pnl:.1f}%, now +{pnl_pct:.1f}%, trail {effective_trail_dist:.1f}%)"

        # ========== STRATEGY-BASED EXIT ==========
        was_tp1_hit = bool(pos.get("tp1_hit", False))
        should_sell, reason = self.trader_strategy.should_sell(
            pos,
            market={}
        )
        if not was_tp1_hit and pos.get("tp1_hit"):
            self.save_positions()

        # Prioritize TP1 partial before trailing stop only if partial mode is enabled
        if should_sell and reason == "tp1 partial" and not MICRO_CAPITAL_SINGLE_EXIT:
            pass
        elif trailing_exit:
            should_sell = True
            reason = trailing_reason

        if not should_sell:
            return

        sys_logger.add_log(f"🚨 SELL {token}: {reason}")

        is_partial = (reason == "tp1 partial") and not MICRO_CAPITAL_SINGLE_EXIT
        sell_amount = (amount * 0.5) if is_partial else amount

        if is_partial:
            partial_quote = await jupiter_bot.get_quote(
                address,
                "SOL",
                sell_amount,
                slippage_bps=default_slippage
            )
            if "error" in partial_quote:
                sys_logger.add_log(f"❌ Partial sell quote error: {partial_quote['error']}")
                return
            swap_payload = partial_quote["raw_data"]
            sold_sol = float(partial_quote["output_estimate"].replace(",", ""))
        else:
            swap_payload = quote["raw_data"]
            sold_sol = current_sol

        res = await jupiter_bot.execute_swap(swap_payload)
        if res.get("status") != "success":
            sys_logger.add_log("❌ Sell failed")
            return

        cost_basis_slice = (entry * 0.5) if is_partial else entry
        pnl_sol = sold_sol - cost_basis_slice
        txid = res.get("txid", f"SOL-{int(time.time())}")

        solana_tracker.save_trade(
            txid,
            token,
            "SOL",
            sell_amount,
            sold_sol,
            status=reason,
            token_address=address
        )

        if is_partial:
            pos["token_amount"] = amount - sell_amount
            pos["entry_price_sol"] = entry - cost_basis_slice
            pos["tp1_hit"] = True
            pos["dynamic_sl_pct"] = self.trader_strategy.BREAKEVEN_LOCK_PCT
            self.save_positions()
            self.risk_manager.record_trade_pnl(pnl_sol)
            sys_logger.add_log(
                f"✅ PARTIAL SOLD (50%) {token} | Received: {sold_sol:.4f} SOL | PnL: +{pnl_sol:.4f} SOL | SL moved to +{pos['dynamic_sl_pct']}%"
            )
        else:
            self.trader_strategy.on_sell(pos)
            self.remove_position(token)

            if token in self.trailing_stop_state:
                del self.trailing_stop_state[token]

            self.risk_manager.record_trade_pnl(pnl_sol)

            self.recently_sold.append(token)
            self.recently_sold = self.recently_sold[-50:]

            sys_logger.add_log(f"✅ SOLD {token} | PnL: {pnl_sol:+.4f} SOL ({pnl_pct:+.2f}%)")


# =============================
# SINGLETON
# =============================
solana_auto = SolanaAutoBot()