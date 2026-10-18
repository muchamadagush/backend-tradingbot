# File: backend/routes/bot_routes.py
"""
Bot control and trade history endpoints
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from auth import require_trader, require_admin, optional_auth, AuthUser
from rate_limiter import limiter, RATE_LIMIT_TRADING, RATE_LIMIT_READONLY

router = APIRouter(prefix="/api", tags=["bot"])

# These will be injected from main.py
_state = {}


def init_state(state: dict):
    """Initialize module state from main.py"""
    global _state
    _state = state


@router.get("/")
def home():
    return {"status": "ready"}


@router.post("/bot/toggle")
@limiter.limit(RATE_LIMIT_TRADING)
async def toggle_bot(request: Request, bg: BackgroundTasks, user: AuthUser = Depends(require_trader)):
    """Toggle the Hunter trading bot on/off. Requires trader role. Rate limited to 10/min."""
    from main import IS_BOT_RUNNING, auto_trading_loop, CURRENT_SCAN_TARGET
    from websocket_manager import ws_manager
    import main
    import asyncio
    
    main.IS_BOT_RUNNING = not main.IS_BOT_RUNNING

    from fastapi.responses import JSONResponse
    if main.IS_BOT_RUNNING:
        bg.add_task(auto_trading_loop)
        # Broadcast status change via WebSocket
        asyncio.create_task(ws_manager.broadcast_bot_status(True, main.CURRENT_SCAN_TARGET))
        return JSONResponse(content={"status": "running", "toggled_by": user.user_id})
    # Broadcast status change via WebSocket
    asyncio.create_task(ws_manager.broadcast_bot_status(False, "Stopped"))
    return JSONResponse(content={"status": "stopped", "toggled_by": user.user_id})


@router.get("/bot/status")
@limiter.limit(RATE_LIMIT_READONLY)
def bot_status(request: Request):
    """Get current bot status."""
    from main import IS_BOT_RUNNING, CURRENT_SCAN_TARGET
    from fastapi.responses import JSONResponse
    return JSONResponse(content={
        "is_running": IS_BOT_RUNNING,
        "current_activity": CURRENT_SCAN_TARGET,
        "running": IS_BOT_RUNNING,  # Legacy
        "activity": CURRENT_SCAN_TARGET,  # Legacy
    })


@router.get("/bot/mode")
async def bot_mode():
    """Get current bot mode (LIVE/PAPER/BACKTEST)."""
    from main import BOT_MODE
    return {"mode": BOT_MODE}


@router.get("/trade/history")
def trade_history():
    """Get recent trade history."""
    from database_pg import get_db_session, Trade
    
    session = get_db_session()
    try:
        trades = session.query(Trade).order_by(Trade.id.desc()).limit(50).all()
        return [
            {
                'id': t.id,
                'symbol': t.symbol,
                'side': t.side,
                'buy_price': t.buy_price,
                'sell_price': t.sell_price,
                'amount': t.amount,
                'total_usdt': t.total_usdt,
                'status': t.status,
                'profit_usdt': t.profit_usdt,
                'created_at': t.created_at.isoformat() if t.created_at else "-",
                'entry_time': t.created_at.isoformat() if t.created_at else "-",
                'exit_time': t.closed_at.isoformat() if t.closed_at else ("Open" if t.status in ("OPEN", "PENDING", "CLOSING") else "-"),
                'bot_type': t.bot_type,
                'mode': t.mode,
                'source': 'Hunter',
            }
            for t in trades
        ]
    finally:
        session.close()


@router.get("/trade/ledger")
def trade_ledger(trade_id: int = None, bot_type: str = None, limit: int = 100):
    """Get immutable trade ledger entries."""
    from database_pg import get_db_session, TradeLedger
    
    session = get_db_session()
    try:
        query = session.query(TradeLedger)
        if trade_id:
            query = query.filter(TradeLedger.trade_id == trade_id)
        if bot_type:
            query = query.filter(TradeLedger.bot_type == bot_type)
        entries = query.order_by(TradeLedger.timestamp.desc()).limit(limit).all()
        return [
            {
                'id': e.id,
                'trade_id': e.trade_id,
                'event_type': e.event_type,
                'old_state': e.old_state,
                'new_state': e.new_state,
                'price': e.price,
                'amount': e.amount,
                'pnl': e.pnl,
                'reason': e.reason,
                'timestamp': e.timestamp.isoformat() if e.timestamp else None,
            }
            for e in entries
        ]
    finally:
        session.close()


@router.get("/trade/ledger/{trade_id}")
def trade_ledger_by_id(trade_id: int):
    """Get all ledger entries for a specific trade."""
    from database_pg import get_db_session, TradeLedger
    
    session = get_db_session()
    try:
        entries = session.query(TradeLedger).filter(TradeLedger.trade_id == trade_id).order_by(TradeLedger.timestamp).all()
        return [
            {
                'id': e.id,
                'trade_id': e.trade_id,
                'event_type': e.event_type,
                'old_state': e.old_state,
                'new_state': e.new_state,
                'price': e.price,
                'amount': e.amount,
                'pnl': e.pnl,
                'reason': e.reason,
                'timestamp': e.timestamp.isoformat() if e.timestamp else None,
            }
            for e in entries
        ]
    finally:
        session.close()


@router.get("/toko/price/{symbol:path}")
async def toko_price(symbol: str):
    """Get current price from Tokocrypto/Binance."""
    from main import exchange
    try:
        ticker = await exchange.fetch_ticker(symbol)
        return {
            "symbol": symbol,
            "price": ticker["last"],
            "bid": ticker.get("bid"),
            "ask": ticker.get("ask"),
            "volume": ticker.get("baseVolume"),
        }
    except Exception as e:
        return {"error": str(e)}


# ==========================================
# POSITION MANAGEMENT ENDPOINTS
# ==========================================

class ClosePositionRequest(BaseModel):
    action: str = "warn"  # "warn", "force_close", "persist"


@router.get("/positions/active")
@limiter.limit(RATE_LIMIT_READONLY)
async def get_active_positions(request: Request, user: AuthUser = Depends(optional_auth)):
    """Get all currently active/open positions."""
    from main import get_all_active_trades, exchange
    
    active_trades = get_all_active_trades()
    if not active_trades:
        return JSONResponse(content={"positions": [], "count": 0, "total_value": 0})
    
    positions = []
    total_value = 0
    total_unrealized_pnl = 0
    
    for trade in active_trades:
        try:
            ticker = await exchange.fetch_ticker(trade.symbol)
            current_price = ticker["last"]
            current_value = trade.amount * current_price
            unrealized_pnl = (current_price - trade.buy_price) * trade.amount
            pnl_pct = ((current_price - trade.buy_price) / trade.buy_price) * 100
        except:
            current_price = None
            current_value = trade.total_usdt
            unrealized_pnl = 0
            pnl_pct = 0
        
        positions.append({
            "id": trade.id,
            "symbol": trade.symbol,
            "entry_price": trade.buy_price,
            "current_price": current_price,
            "amount": trade.amount,
            "entry_value": trade.total_usdt,
            "current_value": current_value,
            "unrealized_pnl": unrealized_pnl,
            "pnl_pct": pnl_pct,
            "status": trade.status.value if hasattr(trade.status, 'value') else str(trade.status),
            "created_at": trade.created_at.isoformat() if trade.created_at else None,
        })
        total_value += current_value
        total_unrealized_pnl += unrealized_pnl
    
    return JSONResponse(content={
        "positions": positions,
        "count": len(positions),
        "total_value": total_value,
        "total_unrealized_pnl": total_unrealized_pnl,
    })


@router.post("/positions/close-all")
@limiter.limit(RATE_LIMIT_TRADING)
async def close_all_positions(request: Request, req: ClosePositionRequest, user: AuthUser = Depends(require_admin)):
    """
    Close all open positions. Admin only.
    
    Actions:
    - "warn": Log warnings only (dry run)
    - "force_close": Force close all at market price
    - "persist": Mark for resume on restart
    """
    from main import cleanup_open_positions
    
    result = await cleanup_open_positions(action=req.action)
    return JSONResponse(content={
        "action": req.action,
        "result": result,
        "closed_by": user.user_id,
    })


@router.post("/positions/emergency-close")
@limiter.limit(RATE_LIMIT_TRADING)
async def emergency_close(request: Request, user: AuthUser = Depends(require_admin)):
    """Emergency close all positions immediately. Admin only."""
    from main import emergency_close_all_positions
    
    result = await emergency_close_all_positions()
    return JSONResponse(content={
        "action": "emergency_close",
        "result": result,
        "triggered_by": user.user_id,
    })


@router.post("/positions/{trade_id}/close")
@limiter.limit(RATE_LIMIT_TRADING)
async def close_single_position(request: Request, trade_id: int, user: AuthUser = Depends(require_trader)):
    """Manually close a specific position. Requires trader role."""
    from main import exchange, save_sell_trade, hunter_risk, BOT_MODE
    from database_pg import get_db_session, Trade, TradeState
    import execution
    
    session = get_db_session()
    try:
        trade = session.query(Trade).filter(
            Trade.id == trade_id,
            Trade.status.in_([TradeState.OPEN, TradeState.CLOSING])
        ).first()
        
        if not trade:
            return JSONResponse(content={"error": "Trade not found or already closed", "trade_id": trade_id})
        
        symbol = trade.symbol
        buy_price = trade.buy_price
        amount = trade.amount
    finally:
        session.close()
    
    try:
        ticker = await exchange.fetch_ticker(symbol)
        current_price = ticker["last"]
    except Exception as e:
        return JSONResponse(content={"error": f"Failed to fetch price: {e}", "trade_id": trade_id})
    
    profit = (current_price - buy_price) * amount
    pnl_pct = ((current_price - buy_price) / buy_price) * 100
    
    # Execute sell if in LIVE mode
    if BOT_MODE == "LIVE":
        sell_result = await execution.safe_execute_order(
            exchange, symbol, "sell", amount * current_price,
            amount_asset=amount,
            max_slippage_pct=1.0,
            check_liquidity_first=False
        )
        if not sell_result["success"]:
            return JSONResponse(content={"error": f"Sell failed: {sell_result.get('error')}", "trade_id": trade_id})
    
    # Update database
    save_sell_trade(trade_id, current_price, profit, reason="MANUAL_CLOSE")
    hunter_risk.record_trade_pnl(profit)
    
    return JSONResponse(content={
        "status": "closed",
        "trade_id": trade_id,
        "symbol": symbol,
        "entry_price": buy_price,
        "exit_price": current_price,
        "amount": amount,
        "profit": profit,
        "pnl_pct": pnl_pct,
        "closed_by": user.user_id,
    })
