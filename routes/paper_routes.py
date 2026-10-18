# File: backend/routes/paper_routes.py
"""
Paper trading endpoints
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from auth import require_trader, optional_auth, AuthUser
from rate_limiter import limiter, RATE_LIMIT_TRADING, RATE_LIMIT_READONLY

router = APIRouter(prefix="/api/paper", tags=["paper"])


class PaperBuyRequest(BaseModel):
    symbol: str
    amount_usdt: float


class PaperSellRequest(BaseModel):
    symbol: str
    percentage: float = 100


@router.get("/account")
@limiter.limit(RATE_LIMIT_READONLY)
async def paper_account(request: Request, user: AuthUser = Depends(optional_auth)):
    """Get paper trading account summary. Read-only access."""
    from main import exchange
    import paper_trading
    
    from fastapi.responses import JSONResponse
    prices = {}
    for s in paper_trading.paper_account.positions:
        try:
            ticker = await exchange.fetch_ticker(s)
            prices[s] = ticker["last"]
        except:
            pass
    summary = paper_trading.paper_account.get_account_summary(prices)
    return JSONResponse(content=summary)


@router.post("/buy")
@limiter.limit(RATE_LIMIT_TRADING)
async def paper_buy(request: Request, req: PaperBuyRequest, user: AuthUser = Depends(require_trader)):
    """Execute paper buy order. Requires trader role. Rate limited to 10/min."""
    from main import exchange, hunter_risk
    import paper_trading
    
    try:
        ticker = await exchange.fetch_ticker(req.symbol)
        price = ticker["last"]
    except Exception as e:
            return JSONResponse(content={"status": "failed", "reason": f"Failed to fetch price: {e}"})
    
    # Check risk before buy
    open_positions = len(paper_trading.paper_account.positions)
    wallet_balance = paper_trading.paper_account.balance
    risk_check = hunter_risk.can_open_position(open_positions, wallet_balance, req.amount_usdt)
    
    if not risk_check.get("approved", False):
            return JSONResponse(content={"status": "rejected", "reasons": risk_check.get("reasons", ["Risk check failed"])})
    
    result = paper_trading.paper_account.place_market_buy(
        req.symbol, req.amount_usdt, price
    )
    return JSONResponse(content=result)


@router.post("/sell")
@limiter.limit(RATE_LIMIT_TRADING)
async def paper_sell(request: Request, req: PaperSellRequest, user: AuthUser = Depends(require_trader)):
    """Execute paper sell order. Requires trader role. Rate limited to 10/min."""
    from main import exchange
    import paper_trading
    
    try:
        ticker = await exchange.fetch_ticker(req.symbol)
        price = ticker["last"]
    except Exception as e:
            return JSONResponse(content={"status": "failed", "reason": f"Failed to fetch price: {e}"})
    
    result = paper_trading.paper_account.place_market_sell(
        req.symbol, req.percentage, price
    )
    return JSONResponse(content=result)


@router.post("/reset")
@limiter.limit(RATE_LIMIT_TRADING)
async def paper_reset(request: Request, user: AuthUser = Depends(require_trader)):
    """Reset paper trading account. Requires trader role. Rate limited to 10/min."""
    from main import PAPER_INITIAL_BALANCE
    import paper_trading
    
    paper_trading.paper_account.reset_account(PAPER_INITIAL_BALANCE)
    return JSONResponse(content={"status": "reset", "balance": PAPER_INITIAL_BALANCE})


@router.get("/orders")
async def paper_orders():
    """Get paper trading order history."""
    import paper_trading
    return JSONResponse(content=paper_trading.paper_account.orders_history)


@router.get("/trades")
async def paper_trades():
    """Get paper trading trade history."""
    import paper_trading
    return JSONResponse(content=paper_trading.paper_account.trades_history)
