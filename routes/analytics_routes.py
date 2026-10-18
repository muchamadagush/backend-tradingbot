# File: backend/routes/analytics_routes.py
"""
Analytics endpoints
"""

from fastapi import APIRouter
import analytics

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary")
async def analytics_summary():
    """Get trading analytics summary."""
    return JSONResponse(content=analytics.get_analytics_summary())


@router.get("/performance")
async def analytics_performance():
    """Get performance metrics."""
    return JSONResponse(content=analytics.get_performance_metrics())


@router.get("/daily")
async def analytics_daily():
    """Get daily P&L breakdown."""
    return JSONResponse(content=analytics.get_daily_pnl())


# New: Solana analytics by timeframe
from fastapi import Query
from fastapi.responses import JSONResponse

@router.get("/solana")
async def analytics_solana(timeframe: str = Query("daily", enum=["daily", "weekly", "monthly", "yearly"])):
    """Get Solana analytics for a given timeframe."""
    sol_price = analytics.get_current_sol_price()
    data = analytics.calculate_solana_analytics(timeframe=timeframe, sol_to_usd=sol_price)
    return JSONResponse(content=data)


@router.get("/tokocrypto")
async def analytics_tokocrypto(timeframe: str = Query("daily", enum=["daily", "weekly", "monthly", "yearly"])):
    """Get Tokocrypto analytics for a given timeframe."""
    data = analytics.calculate_tokocrypto_analytics(timeframe=timeframe)
    return JSONResponse(content=data)
