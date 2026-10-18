# File: backend/routes/solana_routes.py
"""
Solana/Jupiter trading endpoints
"""

from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel
import solana_tracker
from auth import require_trader, optional_auth, AuthUser
from rate_limiter import limiter, RATE_LIMIT_TRADING, RATE_LIMIT_READONLY
from jupiter_handler import jupiter_bot
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/solana", tags=["solana"])


class QuoteRequest(BaseModel):
    input_token: str = "SOL"
    output_token: str
    amount: float
    slippage_bps: int = 50


class SwapRequest(BaseModel):
    quote_response: Optional[dict] = None
    amount: Optional[float] = None
    input_token: Optional[str] = "SOL"
    output_token: Optional[str] = "USDC"
    slippage_bps: Optional[int] = 50


class AutoToggleRequest(BaseModel):
    mode: Optional[str] = "AUTO_DISCOVERY"
    token: Optional[str] = None
    strategy: Optional[str] = "TRADER"


@router.get("/balance")
@limiter.limit(RATE_LIMIT_READONLY)
async def sol_balance(request: Request, user: AuthUser = Depends(optional_auth)):
    """Get Solana token balances. Read-only access."""
    balances = await jupiter_bot.get_token_balances()
    return JSONResponse(content=balances)


@router.get("/history")
def sol_history(user: AuthUser = Depends(optional_auth)):
    """Get Solana trade history. Read-only access."""
    return JSONResponse(content=solana_tracker.load_history())


@router.get("/quote")
async def solana_quote_get(
    amount: float,
    to_token: Optional[str] = None,
    output_token: Optional[str] = None,
    from_token: Optional[str] = "SOL",
    input_token: Optional[str] = "SOL",
    slippage_bps: int = 50,
    user: AuthUser = Depends(optional_auth)
):
    """Get Jupiter swap quote via GET query parameters for frontend compatibility."""
    out_token = to_token or output_token or "USDC"
    in_token = from_token if from_token and from_token != "SOL" else (input_token or "SOL")
    quote = await jupiter_bot.get_quote(
        in_token, out_token, amount, slippage_bps
    )
    return JSONResponse(content=quote)


@router.post("/quote")
async def solana_quote(req: QuoteRequest, user: AuthUser = Depends(optional_auth)):
    """Get Jupiter swap quote via POST JSON body."""
    quote = await jupiter_bot.get_quote(
        req.input_token, req.output_token, req.amount, req.slippage_bps
    )
    return JSONResponse(content=quote)


@router.post("/swap")
@limiter.limit(RATE_LIMIT_TRADING)
async def solana_swap(request: Request, req: SwapRequest, user: AuthUser = Depends(require_trader)):
    """
    Execute Jupiter swap. Requires trader role. Rate limited to 10/min.
    Supports either pre-fetched quote_response or direct amount/token payload.
    """
    if req.quote_response:
        quote_payload = req.quote_response.get("raw_data", req.quote_response)
        result = await jupiter_bot.execute_swap(quote_payload)
        return JSONResponse(content=result)
    
    if req.amount and req.amount > 0:
        # Fetch fresh quote with safe slippage
        quote = await jupiter_bot.get_quote(
            req.input_token or "SOL",
            req.output_token or "USDC",
            req.amount,
            req.slippage_bps or 50
        )
        if "error" in quote:
            return JSONResponse(content={"error": f"Failed to get swap quote: {quote['error']}"}, status_code=400)
        
        result = await jupiter_bot.execute_swap(quote["raw_data"])
        return JSONResponse(content=result)

    return JSONResponse(content={"error": "Must provide quote_response or amount"}, status_code=422)


@router.post("/auto/toggle")
async def solana_auto_toggle(
    background_tasks: BackgroundTasks, 
    body: Optional[AutoToggleRequest] = None, 
    user: AuthUser = Depends(require_trader)
):
    """Toggle Solana auto trading bot. Requires trader role."""
    from solana_auto import solana_auto
    
    if solana_auto.is_running:
        solana_auto.stop()
        return JSONResponse(content={"status": "stopped", "toggled_by": user.user_id})
    else:
        mode = body.mode if body and body.mode else "AUTO_DISCOVERY"
        token = body.token if body else None
        background_tasks.add_task(solana_auto.start, mode, token)
        return JSONResponse(content={"status": "started", "mode": mode, "toggled_by": user.user_id})


@router.get("/auto/status")
async def solana_auto_status(user: AuthUser = Depends(optional_auth)):
    """Get Solana auto bot status. Read-only access."""
    from solana_auto import solana_auto
    
    return JSONResponse(content={
        "is_running": solana_auto.is_running,
        "mode": solana_auto.mode,
        "target_token": solana_auto.target_token,
        "active_positions": len(solana_auto.active_positions),
        "positions": solana_auto.active_positions,
    })