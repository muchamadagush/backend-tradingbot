# ==========================================
# File: backend/risk_manager.py
# ==========================================

from datetime import date
from typing import Dict, List, Optional
from pydantic import BaseModel
from enum import Enum
import time


# ==========================================
# BOT TYPE ENUM
# ==========================================

class BotType(str, Enum):
    HUNTER = "HUNTER"       # Tokocrypto spot trading
    TRADER = "TRADER"       # Solana swing trading
    SNIPER = "SNIPER"       # Solana quick snipe


# ==========================================
# RISK LIMIT CONFIG
# ==========================================

class RiskLimits(BaseModel):
    max_position_size_pct: float = 20.0
    max_portfolio_drawdown_pct: float = 15.0
    max_daily_loss_pct: float = 5.0
    stop_loss_pct: float = 3.0
    max_open_positions: int = 5
    risk_per_trade_pct: float = 2.0
    cooldown_after_loss_seconds: int = 0  # NEW: Cooldown after SL hit


# ==========================================
# PER-BOT RISK PROFILES (NEW)
# ==========================================

# Default profiles for each bot type
BOT_RISK_PROFILES: Dict[BotType, RiskLimits] = {
    BotType.HUNTER: RiskLimits(
        max_position_size_pct=15.0,       # Conservative
        max_portfolio_drawdown_pct=10.0,
        max_daily_loss_pct=3.0,           # Strict daily limit
        stop_loss_pct=3.0,
        max_open_positions=3,
        risk_per_trade_pct=1.5,
        cooldown_after_loss_seconds=300,  # 5 min cooldown after SL
    ),
    BotType.TRADER: RiskLimits(
        max_position_size_pct=10.0,       # Smaller positions
        max_portfolio_drawdown_pct=12.0,
        max_daily_loss_pct=4.0,
        stop_loss_pct=12.0,               # Wider SL for swing
        max_open_positions=3,             # Max 3 concurrent
        risk_per_trade_pct=1.5,
        cooldown_after_loss_seconds=600,  # 10 min cooldown
    ),
    BotType.SNIPER: RiskLimits(
        max_position_size_pct=5.0,        # Very small positions (high risk)
        max_portfolio_drawdown_pct=8.0,
        max_daily_loss_pct=2.0,           # Very strict
        stop_loss_pct=20.0,               # Wide SL (volatile)
        max_open_positions=1,             # Only 1 at a time!
        risk_per_trade_pct=0.5,           # Tiny risk per trade
        cooldown_after_loss_seconds=1800, # 30 min cooldown
    ),
}


# ==========================================
# RISK MANAGER CORE
# ==========================================

class RiskManager:
    """
    Centralized Risk Management Engine
    - Daily loss protection
    - Portfolio drawdown protection
    - Position sizing
    - Kelly criterion
    - Per-bot risk profiles (NEW)
    - State persistence to PostgreSQL (NEW)
    """

    def __init__(
        self, 
        initial_balance: float = 1000.0, 
        limits: RiskLimits = None,
        bot_type: Optional[BotType] = None,
        enable_persistence: bool = True
    ):
        self.initial_balance = float(initial_balance)
        self.bot_type = bot_type
        self.enable_persistence = enable_persistence
        
        # Use bot-specific limits if bot_type provided, else use passed limits or defaults
        if bot_type and bot_type in BOT_RISK_PROFILES:
            self.limits = limits or BOT_RISK_PROFILES[bot_type]
        else:
            self.limits = limits or RiskLimits()

        self.current_balance = self.initial_balance
        self.peak_balance = self.initial_balance

        self.daily_pnl = 0.0
        self.trades_today = 0
        self.last_reset_date = date.today()

        self.is_trading_allowed = True
        
        # NEW: Cooldown tracking
        self.last_loss_timestamp = 0.0
        self.consecutive_losses = 0
        
        # Load persisted state if available
        if enable_persistence and bot_type:
            self._load_state_from_db()

    # ======================================
    # PERSISTENCE METHODS (NEW)
    # ======================================

    def _load_state_from_db(self):
        """Load risk manager state from PostgreSQL on startup."""
        try:
            from database_pg import get_risk_manager_state
            
            bot_type_str = self.bot_type.value if self.bot_type else "DEFAULT"
            state = get_risk_manager_state(bot_type_str)
            
            if state:
                # Check if date changed - if so, reset daily counters
                saved_date = state.get("last_reset_date", "")
                today_str = date.today().isoformat()
                
                if saved_date == today_str:
                    # Same day - restore state
                    self.daily_pnl = state.get("daily_pnl", 0.0)
                    self.trades_today = state.get("trades_today", 0)
                else:
                    # New day - reset daily counters but keep balance tracking
                    self.daily_pnl = 0.0
                    self.trades_today = 0
                
                # Always restore balance tracking
                self.peak_balance = state.get("peak_balance", self.initial_balance)
                self.current_balance = state.get("current_balance", self.initial_balance)
                self.last_loss_timestamp = state.get("last_loss_timestamp", 0.0)
                self.consecutive_losses = state.get("consecutive_losses", 0)
                self.last_reset_date = date.today()
                
                print(f"✅ Loaded risk state for {bot_type_str}: daily_pnl=${self.daily_pnl:.2f}, trades={self.trades_today}")
        except Exception as e:
            print(f"⚠️ Could not load risk state: {e}")

    def _save_state_to_db(self):
        """Save current risk manager state to PostgreSQL."""
        if not self.enable_persistence or not self.bot_type:
            return
            
        try:
            from database_pg import save_risk_manager_state
            
            bot_type_str = self.bot_type.value if self.bot_type else "DEFAULT"
            save_risk_manager_state(
                bot_type=bot_type_str,
                daily_pnl=self.daily_pnl,
                trades_today=self.trades_today,
                last_reset_date=self.last_reset_date.isoformat(),
                peak_balance=self.peak_balance,
                current_balance=self.current_balance,
                last_loss_timestamp=self.last_loss_timestamp,
                consecutive_losses=self.consecutive_losses,
            )
        except Exception as e:
            print(f"⚠️ Could not save risk state: {e}")

    # ======================================
    # INTERNAL HELPERS
    # ======================================

    def _reset_daily_if_needed(self):
        """Reset daily PnL and counters when date changes"""
        today = date.today()
        if today > self.last_reset_date:
            self.daily_pnl = 0.0
            self.trades_today = 0
            self.last_reset_date = today
            self.is_trading_allowed = True

    # ======================================
    # CORE STATE UPDATES
    # ======================================

    def record_trade_pnl(self, pnl: float):
        """
        Called AFTER position is closed (SELL).
        Updates balances and checks risk limits.
        Persists state to DB after update.
        """
        self._reset_daily_if_needed()

        pnl = float(pnl)
        self.daily_pnl += pnl
        self.current_balance += pnl
        self.trades_today += 1

        # Track losses for cooldown
        if pnl < 0:
            self.last_loss_timestamp = time.time()
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        # Update peak balance (high-water mark)
        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance

        self._check_limits()
        
        # Persist state to database
        self._save_state_to_db()

    def is_in_cooldown(self) -> bool:
        """Check if bot is in cooldown period after a loss."""
        if self.limits.cooldown_after_loss_seconds <= 0:
            return False
            
        if self.last_loss_timestamp <= 0:
            return False
            
        elapsed = time.time() - self.last_loss_timestamp
        return elapsed < self.limits.cooldown_after_loss_seconds

    def get_cooldown_remaining(self) -> int:
        """Get remaining cooldown seconds."""
        if not self.is_in_cooldown():
            return 0
            
        elapsed = time.time() - self.last_loss_timestamp
        return max(0, int(self.limits.cooldown_after_loss_seconds - elapsed))

    # ======================================
    # LIMIT CHECKS
    # ======================================

    def _check_limits(self):
        """
        Enforce all global risk limits.
        """
        # ---- DAILY LOSS LIMIT ----
        if self.daily_pnl < 0:
            daily_loss_pct = abs(self.daily_pnl) / self.initial_balance * 100
            if daily_loss_pct >= self.limits.max_daily_loss_pct:
                self.is_trading_allowed = False
                return False, f"Daily loss limit hit ({daily_loss_pct:.2f}%)"

        # ---- PORTFOLIO DRAWDOWN ----
        if self.peak_balance > 0:
            drawdown_pct = (self.peak_balance - self.current_balance) / self.peak_balance * 100
            if drawdown_pct >= self.limits.max_portfolio_drawdown_pct:
                self.is_trading_allowed = False
                return False, f"Max drawdown hit ({drawdown_pct:.2f}%)"

        self.is_trading_allowed = True
        return True, "OK"

    # ======================================
    # PUBLIC CHECKS (USED BY main.py)
    # ======================================

    def can_trade_new(self) -> bool:
        """
        Called BEFORE scanning or placing trades.
        Includes cooldown check.
        """
        self._reset_daily_if_needed()
        
        # Check cooldown first
        if self.is_in_cooldown():
            return False
            
        ok, _ = self._check_limits()
        return ok

    def can_open_position(
        self,
        current_open_positions: int,
        wallet_balance: float,
        trade_amount: float,
    ) -> Dict:
        """
        Validate whether a new position can be opened.
        """
        reasons: List[str] = []
        
        # ---- COOLDOWN CHECK ----
        if self.is_in_cooldown():
            remaining = self.get_cooldown_remaining()
            reasons.append(f"Cooldown active ({remaining}s remaining)")
            return {"approved": False, "reasons": reasons}
        
        if not self.can_trade_new():
            return {
                "approved": False,
                "reasons": ["Trading halted (daily loss or drawdown limit)"],
            }

        # ---- MAX OPEN POSITIONS ----
        if current_open_positions >= self.limits.max_open_positions:
            reasons.append(
                f"Max positions reached ({current_open_positions}/{self.limits.max_open_positions})"
            )

        # ---- BALANCE CHECK ----
        if trade_amount > wallet_balance:
            reasons.append(
                f"Insufficient balance (${wallet_balance:.2f} < ${trade_amount:.2f})"
            )

        # ---- POSITION SIZE LIMIT ----
        if wallet_balance > 0:
            position_pct = (trade_amount / wallet_balance) * 100
            if position_pct > self.limits.max_position_size_pct:
                reasons.append(
                    f"Position size too large ({position_pct:.1f}% > {self.limits.max_position_size_pct}%)"
                )

        if reasons:
            return {"approved": False, "reasons": reasons}

        return {"approved": True, "reasons": []}

    # ======================================
    # DASHBOARD & ANALYTICS
    # ======================================

    def get_risk_summary(self, current_equity: float, open_positions: int) -> Dict:
        """
        Used by API to show risk dashboard.
        """
        self.current_balance = float(current_equity)
        self._check_limits()

        drawdown_pct = 0.0
        if self.peak_balance > 0:
            drawdown_pct = (
                (self.peak_balance - self.current_balance) / self.peak_balance * 100
            )

        if not self.is_trading_allowed:
            risk_level = "HALTED"
        elif drawdown_pct >= self.limits.max_portfolio_drawdown_pct * 0.5:
            risk_level = "HIGH"
        else:
            risk_level = "NORMAL"

        return {
            "can_trade": self.is_trading_allowed,
            "risk_level": risk_level,
            "daily_pnl": self.daily_pnl,
            "current_drawdown_pct": drawdown_pct,
            "open_positions": open_positions,
            "limits": self.limits.dict(),
        }

    # ======================================
    # POSITION SIZING & KELLY
    # ======================================

    def calculate_kelly_criterion(self, win_rate, avg_win, avg_loss, sample_size: int = 0) -> Dict:
        """
        Kelly Criterion calculator for frontend.
        Returns Half-Kelly (industry standard) for reduced volatility.
        """
        MIN_SAMPLE_SIZE = 30
        
        try:
            win_rate = float(win_rate) / 100.0
            avg_win = float(avg_win)
            avg_loss = float(avg_loss)
        except (TypeError, ValueError):
            return {"status": "error", "message": "Invalid inputs"}

        if avg_loss <= 0:
            return {"status": "error", "message": "avg_loss must be > 0"}

        b = avg_win / avg_loss
        p = max(0.0, min(1.0, win_rate))
        q = 1 - p

        # Full Kelly
        full_kelly = max(0.0, (b * p - q) / b)
        
        # Half-Kelly: Industry standard for reduced volatility
        half_kelly = full_kelly / 2
        half_kelly = min(half_kelly, 0.10)  # Cap at 10%
        
        half_kelly_pct = half_kelly * 100
        full_kelly_pct = full_kelly * 100

        result = {
            "status": "success",
            "kelly_pct": full_kelly_pct,
            "half_kelly_pct": half_kelly_pct,
            "recommended_pct": half_kelly_pct,
            "recommendation": f"Risk up to {half_kelly_pct:.2f}% of capital per trade (Half-Kelly)",
        }
        
        # Sample size warning
        if sample_size > 0 and sample_size < MIN_SAMPLE_SIZE:
            result["warning"] = f"Low sample size ({sample_size}/{MIN_SAMPLE_SIZE}). Results may be unreliable."
        
        return result

    def calculate_position_size(
        self,
        capital: float,
        entry_price: float,
        stop_loss_price: float,
    ) -> Dict:
        """
        Calculate optimal position size based on risk per trade.
        """
        try:
            capital = float(capital)
            entry_price = float(entry_price)
            stop_loss_price = float(stop_loss_price)
        except (TypeError, ValueError):
            return {"status": "error", "message": "Invalid inputs"}

        stop_distance = abs(entry_price - stop_loss_price)
        if stop_distance <= 0:
            return {"status": "error", "message": "Stop distance must be > 0"}

        risk_amount = capital * (self.limits.risk_per_trade_pct / 100.0)
        position_size = risk_amount / stop_distance
        position_value = position_size * entry_price

        return {
            "status": "success",
            "risk_amount": risk_amount,
            "position_size": position_size,
            "position_value_usdt": position_value,
            "risk_pct": self.limits.risk_per_trade_pct,
        }


# ==========================================
# BOT-AWARE RISK MANAGER REGISTRY (NEW)
# ==========================================

class BotRiskRegistry:
    """
    Manages per-bot RiskManager instances.
    Each bot type gets its own risk manager with appropriate limits.
    State is persisted to PostgreSQL for each bot type.
    """
    
    def __init__(self, initial_balance: float = 1000.0, enable_persistence: bool = True):
        self.initial_balance = initial_balance
        self.enable_persistence = enable_persistence
        self._managers: Dict[BotType, RiskManager] = {}
        
        # Initialize all bot managers (will load from DB if persistence enabled)
        for bot_type in BotType:
            self._managers[bot_type] = RiskManager(
                initial_balance=initial_balance,
                bot_type=bot_type,
                enable_persistence=enable_persistence
            )
    
    def get(self, bot_type: BotType) -> RiskManager:
        """Get risk manager for specific bot type."""
        if bot_type not in self._managers:
            self._managers[bot_type] = RiskManager(
                initial_balance=self.initial_balance,
                bot_type=bot_type,
                enable_persistence=self.enable_persistence
            )
        return self._managers[bot_type]
    
    def hunter(self) -> RiskManager:
        """Shortcut for Hunter bot risk manager."""
        return self.get(BotType.HUNTER)
    
    def trader(self) -> RiskManager:
        """Shortcut for Trader bot risk manager."""
        return self.get(BotType.TRADER)
    
    def sniper(self) -> RiskManager:
        """Shortcut for Sniper bot risk manager."""
        return self.get(BotType.SNIPER)
    
    def get_all_status(self) -> Dict:
        """Get status of all bot risk managers."""
        return {
            bot_type.value: {
                "can_trade": mgr.is_trading_allowed,
                "daily_pnl": mgr.daily_pnl,
                "trades_today": mgr.trades_today,
                "in_cooldown": mgr.is_in_cooldown(),
                "cooldown_remaining": mgr.get_cooldown_remaining(),
                "consecutive_losses": mgr.consecutive_losses,
                "limits": mgr.limits.dict()
            }
            for bot_type, mgr in self._managers.items()
        }
    
    def sync_balance(self, new_balance: float):
        """Sync balance across all bot managers and persist."""
        for mgr in self._managers.values():
            mgr.current_balance = new_balance
            if new_balance > mgr.peak_balance:
                mgr.peak_balance = new_balance
            mgr._save_state_to_db()
