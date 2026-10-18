# ==========================================
# File: strategies/solana_sniper.py
# Event-based SNIPER strategy v2 (Protected with Hard SL & Trailing Profit Lock)
# ==========================================

import time
from typing import Dict, Tuple


class SolanaSniperStrategy:
    """
    Event-based sniper v2:
    - Strict liquidity & launch velocity validation
    - Hard Stop-Loss (-15%) to prevent holding rugs to -90%
    - Fast Trailing Profit Stop (activates at +25%, trails by 8% of peak PnL)
    - Stagnation Early Exit (60s if momentum stalls < +3%)
    """

    HARD_SL_PCT = -15.0
    TRAIL_ACTIVATION_PCT = 25.0
    TRAIL_DRAWDOWN_PCT = 8.0
    FAST_TP_PCT = 80.0
    STAGNATION_SECONDS = 60
    MAX_HOLD_SECONDS = 120

    def __init__(self):
        self.cooldown_tokens = {}
        self.cooldown_seconds = 3600  # 1 jam

    # =========================
    # ENTRY DECISION
    # =========================
    def should_snipe(self, event: Dict) -> Tuple[bool, str]:
        """
        event = {
            token,
            liquidity_sol,
            first_price,
            current_price,
            launch_time
        }
        """
        token = event["token"]
        now = time.time()

        # ---- cooldown ----
        if token in self.cooldown_tokens:
            if now - self.cooldown_tokens[token] < self.cooldown_seconds:
                return False, "Cooldown active"

        # ---- liquidity check (raised from 5 SOL to 15 SOL to avoid micro-rugs) ----
        if event.get("liquidity_sol", 0) < 15:
            return False, "Low liquidity (<15 SOL)"

        # ---- delay confirm (anti-block-0 bundle dump) ----
        if now - event["launch_time"] < 5:
            return False, "Too early (<5s)"

        # ---- price stability & positive initial momentum ----
        first_price = max(float(event.get("first_price", 0)), 1e-12)
        current_price = float(event.get("current_price", 0))
        price_change = ((current_price - first_price) / first_price) * 100.0

        # Reject tokens already dumping below launch price or already pumped > 22%
        if price_change < -3.0:
            return False, f"Initial dump detected ({price_change:.1f}%)"
        if price_change > 22.0:
            return False, f"Price already pumped ({price_change:.1f}%)"

        return True, "SNIPE_OK"

    # =========================
    # POSITION INIT
    # =========================
    def on_buy(self, position: Dict):
        position["sniper_entry_time"] = time.time()
        position["max_price"] = position.get("entry_price_sol", 0)
        position["peak_pnl_pct"] = 0.0

    # =========================
    # EXIT DECISION
    # =========================
    def should_exit(self, position: Dict, market: Dict) -> Tuple[bool, str]:
        """
        Exit logic v2:
        1. Liquidity collapse (< 5 SOL)
        2. Hard Stop-Loss (<= -15%)
        3. Trailing Profit Lock (activates at +25%, exits on 8% pullback from peak)
        4. Hard TP (>= +80%)
        5. Stagnation exit (60s with PnL < +3%) & Max Time exit (120s)
        """
        now = time.time()
        hold_time = now - float(position.get("sniper_entry_time", now))
        pnl = float(position.get("pnl_pct", 0.0))

        # Track peak PnL percentage for trailing stop
        peak_pnl = max(float(position.get("peak_pnl_pct", 0.0)), pnl)
        position["peak_pnl_pct"] = peak_pnl

        # ---- 1. Liquidity rug protection ----
        if market.get("liquidity_sol", 999) < 5:
            return True, "SNIPER_LIQUIDITY_DROP"

        # ---- 2. Hard Stop-Loss (Critical missing protection in v1) ----
        if pnl <= self.HARD_SL_PCT:
            return True, f"SNIPER_HARD_SL ({pnl:.1f}%)"

        # ---- 3. Hard Fast TP ----
        if pnl >= self.FAST_TP_PCT:
            return True, f"SNIPER_TP_FAST (+{pnl:.1f}%)"

        # ---- 4. Trailing Profit Stop ----
        if peak_pnl >= self.TRAIL_ACTIVATION_PCT:
            if (peak_pnl - pnl) >= self.TRAIL_DRAWDOWN_PCT:
                return True, f"SNIPER_TRAILING_PROFIT (Peak +{peak_pnl:.1f}%, Now +{pnl:.1f}%)"

        # ---- 5. Early Stagnation Exit ----
        if hold_time > self.STAGNATION_SECONDS and pnl < 3.0:
            return True, f"SNIPER_STAGNATION_EXIT ({int(hold_time)}s, PnL {pnl:.1f}%)"

        # ---- 6. Max Hold Time Exit ----
        if hold_time > self.MAX_HOLD_SECONDS:
            return True, "SNIPER_TIME_EXIT"

        return False, "HOLD"

    # =========================
    # POST SELL
    # =========================
    def on_sell(self, position: Dict):
        self.cooldown_tokens[position["token"]] = time.time()
