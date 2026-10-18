# File: backend/routes/risk_routes.py
"""
Risk management endpoints
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from auth import require_admin, require_trader, optional_auth, AuthUser

router = APIRouter(prefix="/api", tags=["risk"])


class PositionSizeRequest(BaseModel):
    entry_price: float
    stop_loss_price: float
    portfolio_value: float = None


@router.get("/killswitch/status")
def killswitch_status(user: AuthUser = Depends(optional_auth)):
    """Get current kill switch status and limits. Read-only access."""
    from global_kill_switch import kill_switch
    return kill_switch.get_status()


@router.post("/killswitch/toggle")
def killswitch_toggle(enabled: bool = True, user: AuthUser = Depends(require_admin)):
    """Enable or disable manual kill switch. Requires admin role."""
    from global_kill_switch import kill_switch
    success = kill_switch.set_manual_kill(enabled)
    return {
        "success": success,
        "manual_kill_enabled": enabled,
        "trading_allowed": kill_switch.is_trading_allowed(),
        "toggled_by": user.user_id
    }


@router.get("/killswitch/check")
def killswitch_check(user: AuthUser = Depends(optional_auth)):
    """Quick check if trading is allowed. Read-only access."""
    from global_kill_switch import kill_switch
    return {"trading_allowed": kill_switch.is_trading_allowed()}


@router.get("/risk/bots")
def risk_all_bots(user: AuthUser = Depends(optional_auth)):
    """Get risk status for all bots (Hunter, Trader, Sniper). Read-only access."""
    from main import bot_risk
    return bot_risk.get_all_status()


@router.get("/risk/hunter")
def risk_hunter(user: AuthUser = Depends(optional_auth)):
    """Get Hunter bot risk status. Read-only access."""
    from main import hunter_risk
    return {
        "bot_type": "HUNTER",
        "can_trade": hunter_risk.is_trading_allowed,
        "daily_pnl": hunter_risk.daily_pnl,
        "trades_today": hunter_risk.trades_today,
        "in_cooldown": hunter_risk.is_in_cooldown(),
        "cooldown_remaining": hunter_risk.get_cooldown_remaining(),
        "consecutive_losses": hunter_risk.consecutive_losses,
        "limits": hunter_risk.limits.dict()
    }


@router.get("/risk/trader")
def risk_trader(user: AuthUser = Depends(optional_auth)):
    """Get Trader bot risk status. Read-only access."""
    from main import trader_risk
    return {
        "bot_type": "TRADER",
        "can_trade": trader_risk.is_trading_allowed,
        "daily_pnl": trader_risk.daily_pnl,
        "trades_today": trader_risk.trades_today,
        "in_cooldown": trader_risk.is_in_cooldown(),
        "cooldown_remaining": trader_risk.get_cooldown_remaining(),
        "consecutive_losses": trader_risk.consecutive_losses,
        "limits": trader_risk.limits.dict()
    }


@router.get("/risk/sniper")
def risk_sniper():
    """Get Sniper bot risk status."""
    from main import sniper_risk
    return {
        "bot_type": "SNIPER",
        "can_trade": sniper_risk.is_trading_allowed,
        "daily_pnl": sniper_risk.daily_pnl,
        "trades_today": sniper_risk.trades_today,
        "in_cooldown": sniper_risk.is_in_cooldown(),
        "cooldown_remaining": sniper_risk.get_cooldown_remaining(),
        "consecutive_losses": sniper_risk.consecutive_losses,
        "limits": sniper_risk.limits.dict()
    }


@router.post("/risk/position-size")
async def risk_position_size(req: PositionSizeRequest):
    """Calculate optimal position size based on risk."""
    from main import hunter_risk
    
    portfolio = req.portfolio_value or hunter_risk.current_balance
    size = hunter_risk.calculate_position_size(
        portfolio, req.entry_price, req.stop_loss_price
    )
    return {
        "position_size": size,
        "portfolio_value": portfolio,
        "risk_per_trade_pct": hunter_risk.limits.risk_per_trade_pct,
    }


@router.get("/risk/summary")
async def risk_summary():
    """Get current risk summary."""
    from main import exchange, hunter_risk
    import paper_trading
    
    try:
        prices = {}
        for s in paper_trading.paper_account.positions:
            try:
                prices[s] = (await exchange.fetch_ticker(s))["last"]
            except:
                pass
        summary = paper_trading.paper_account.get_account_summary(prices)
        current_equity = summary.get("total_equity", hunter_risk.current_balance)
    except:
        current_equity = hunter_risk.current_balance
    
    open_positions = len(paper_trading.paper_account.positions) if paper_trading.paper_account else 0
    return hunter_risk.get_risk_summary(current_equity, open_positions)


@router.get("/risk/kelly")
async def risk_kelly():
    """Calculate Kelly criterion position size (Half-Kelly for safety)."""
    from database_pg import get_db_session, Trade, TradeState
    
    MIN_SAMPLE_SIZE = 30
    
    session = get_db_session()
    try:
        trades_obj = session.query(Trade).filter(Trade.status == TradeState.CLOSED).order_by(Trade.id.desc()).limit(100).all()
        trades = [{'profit_usdt': t.profit_usdt} for t in trades_obj]
    finally:
        session.close()
    
    if not trades:
        return {"kelly_pct": 0, "half_kelly_pct": 0, "message": "No trade history", "warning": "Insufficient data"}
    
    sample_warning = None
    if len(trades) < MIN_SAMPLE_SIZE:
        sample_warning = f"Low sample size ({len(trades)}/{MIN_SAMPLE_SIZE}). Results may be unreliable."
    
    wins = sum(1 for t in trades if t.get("profit_usdt", 0) > 0)
    win_rate = wins / len(trades) if trades else 0.5
    
    winning_trades = [t for t in trades if t.get("profit_usdt", 0) > 0]
    losing_trades = [t for t in trades if t.get("profit_usdt", 0) < 0]
    
    avg_win = sum(t.get("profit_usdt", 0) for t in winning_trades) / len(winning_trades) if winning_trades else 1
    avg_loss = abs(sum(t.get("profit_usdt", 0) for t in losing_trades) / len(losing_trades)) if losing_trades else 1
    
    win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 1
    
    full_kelly = win_rate - ((1 - win_rate) / win_loss_ratio) if win_loss_ratio > 0 else 0
    full_kelly = max(0, full_kelly)
    
    half_kelly = full_kelly / 2
    half_kelly = min(half_kelly, 0.10)
    
    result = {
        "kelly_pct": round(full_kelly * 100, 2),
        "half_kelly_pct": round(half_kelly * 100, 2),
        "recommended_pct": round(half_kelly * 100, 2),
        "win_rate": round(win_rate * 100, 2),
        "win_loss_ratio": round(win_loss_ratio, 2),
        "trades_analyzed": len(trades),
        "min_sample_size": MIN_SAMPLE_SIZE,
    }
    
    if sample_warning:
        result["warning"] = sample_warning
    
    return result
