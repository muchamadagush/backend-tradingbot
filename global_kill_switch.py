from datetime import date, timedelta
from sqlalchemy import func
from database_pg import get_db_session, TradeLedger, Account, SystemFlag, OpenPosition
import sys_logger


class GlobalKillSwitch:
    """
    PORTFOLIO LEVEL HARD STOP
    
    Protects against:
    - Daily loss exceeding threshold
    - Weekly loss exceeding threshold  
    - Unrealized loss exceeding threshold
    - Manual kill switch activation
    """

    MAX_DAILY_LOSS_PCT = 0.05       # 5% portfolio daily circuit breaker
    MAX_WEEKLY_LOSS_PCT = 0.10      # 10% weekly loss circuit breaker
    MAX_UNREALIZED_LOSS_PCT = 0.06  # 6% unrealized drawdown circuit breaker

    def __init__(self):
        self._last_check_reason = None

    # ================= PUBLIC =================

    def is_trading_allowed(self) -> bool:
        """
        Main entry point - checks all kill conditions.
        Returns False if ANY condition triggers.
        """
        if self._manual_kill():
            self._last_check_reason = "MANUAL_DISABLE"
            sys_logger.add_log("🛑 GLOBAL KILL SWITCH: MANUAL DISABLE")
            return False

        if self._daily_loss_exceeded():
            self._last_check_reason = "DAILY_LOSS_LIMIT"
            sys_logger.add_log("🛑 GLOBAL KILL SWITCH: DAILY LOSS LIMIT")
            return False

        if self._weekly_loss_exceeded():
            self._last_check_reason = "WEEKLY_LOSS_LIMIT"
            sys_logger.add_log("🛑 GLOBAL KILL SWITCH: WEEKLY LOSS LIMIT")
            return False

        if self._unrealized_loss_exceeded():
            self._last_check_reason = "UNREALIZED_LOSS_LIMIT"
            sys_logger.add_log("🛑 GLOBAL KILL SWITCH: UNREALIZED LOSS LIMIT")
            return False

        self._last_check_reason = None
        return True

    def get_status(self) -> dict:
        """Get current kill switch status for API/monitoring."""
        equity = self._get_equity()
        daily_pnl = self._get_daily_pnl()
        weekly_pnl = self._get_weekly_pnl()
        unrealized_pnl = self._get_unrealized_pnl()
        
        return {
            "trading_allowed": self.is_trading_allowed(),
            "last_block_reason": self._last_check_reason,
            "equity": equity,
            "daily_pnl": daily_pnl,
            "daily_limit": -self.MAX_DAILY_LOSS_PCT * equity,
            "weekly_pnl": weekly_pnl,
            "weekly_limit": -self.MAX_WEEKLY_LOSS_PCT * equity,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_limit": -self.MAX_UNREALIZED_LOSS_PCT * equity,
            "manual_kill_active": self._manual_kill()
        }

    def set_manual_kill(self, enabled: bool) -> bool:
        """Enable or disable manual kill switch."""
        try:
            session = get_db_session()
            try:
                flag = session.query(SystemFlag).filter_by(key='TRADING_DISABLED').first()
                if flag:
                    flag.value = "true" if enabled else "false"
                else:
                    session.add(SystemFlag(key='TRADING_DISABLED', value="true" if enabled else "false"))
                session.commit()
                sys_logger.add_log(f"⚠️ MANUAL KILL SWITCH: {'ENABLED' if enabled else 'DISABLED'}")
                return True
            finally:
                session.close()
        except Exception as e:
            sys_logger.add_log(f"❌ Failed to set manual kill: {e}")
            return False

    # ================= CHECKS =================

    def _manual_kill(self) -> bool:
        session = get_db_session()
        try:
            flag = session.query(SystemFlag).filter_by(key='TRADING_DISABLED').first()
            return bool(flag and flag.value == "true")
        finally:
            session.close()

    def _daily_loss_exceeded(self) -> bool:
        daily_pnl = self._get_daily_pnl()
        equity = self._get_equity()
        
        if equity <= 0:
            return False  # Can't calculate without equity
            
        limit = -self.MAX_DAILY_LOSS_PCT * equity
        return daily_pnl <= limit

    def _weekly_loss_exceeded(self) -> bool:
        """Check if weekly loss limit exceeded."""
        weekly_pnl = self._get_weekly_pnl()
        equity = self._get_equity()
        
        if equity <= 0:
            return False
            
        limit = -self.MAX_WEEKLY_LOSS_PCT * equity
        return weekly_pnl <= limit

    def _unrealized_loss_exceeded(self) -> bool:
        unrealized = self._get_unrealized_pnl()
        equity = self._get_equity()
        
        if equity <= 0:
            return False
            
        limit = -self.MAX_UNREALIZED_LOSS_PCT * equity
        return unrealized <= limit

    # ================= DATA HELPERS =================

    def _get_daily_pnl(self) -> float:
        """Get sum of realized PnL for today from trade_ledger (immutable source)."""
        today = date.today().isoformat()
        session = get_db_session()
        try:
            result = session.query(func.coalesce(func.sum(TradeLedger.pnl), 0)).filter(
                func.date(TradeLedger.timestamp) == today,
                TradeLedger.event_type == 'CLOSED'
            ).scalar()
            return float(result)
        finally:
            session.close()

    def _get_weekly_pnl(self) -> float:
        """Get sum of realized PnL for current week from trade_ledger."""
        today = date.today()
        week_start = (today - timedelta(days=today.weekday())).isoformat()
        
        session = get_db_session()
        try:
            result = session.query(func.coalesce(func.sum(TradeLedger.pnl), 0)).filter(
                func.date(TradeLedger.timestamp) >= week_start,
                TradeLedger.event_type == 'CLOSED'
            ).scalar()
            return float(result)
        finally:
            session.close()

    def _get_unrealized_pnl(self) -> float:
        """Calculate unrealized PnL from open trades."""
        session = get_db_session()
        try:
            from database_pg import Trade, TradeState
            open_trades = session.query(Trade).filter(
                Trade.status.in_([TradeState.OPEN, TradeState.CLOSING])
            ).all()
            if not open_trades:
                return 0.0
            
            total_unrealized = 0.0
            for t in open_trades:
                if t.profit_usdt is not None:
                    total_unrealized += float(t.profit_usdt)
            return total_unrealized
        except Exception:
            return 0.0
        finally:
            session.close()

    def _get_equity(self) -> float:
        """Get current account equity/balance with safe fallback."""
        session = get_db_session()
        try:
            account = session.query(Account).filter_by(id=1).first()
            if account and account.balance > 0:
                return float(account.balance)
        finally:
            session.close()
        
        import os
        try:
            return float(os.getenv("PAPER_INITIAL_BALANCE", "1000.0"))
        except Exception:
            return 1000.0



# ================= SINGLETON =================
kill_switch = GlobalKillSwitch()
