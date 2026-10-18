# File: backend/routes/fee_routes.py
"""
Fee calculator endpoints
"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/fee", tags=["fee"])


class FeeCalcRequest(BaseModel):
    amount: float
    price: float
    is_maker: bool = False


class RoundTripRequest(BaseModel):
    amount: float
    buy_price: float
    sell_price: float = None
    is_maker: bool = False


class ProfitTargetRequest(BaseModel):
    amount: float
    entry_price: float
    target_profit_pct: float
    is_maker: bool = False


@router.post("/calculate-buy")
async def fee_calculate_buy(req: FeeCalcRequest):
    """Calculate buy fees."""
    from main import fee_calc
    order_type = 'limit' if req.is_maker else 'market'
    result = fee_calc.calculate_buy_cost(req.amount, req.price, order_type)
    return JSONResponse(content=result)


@router.post("/calculate-sell")
async def fee_calculate_sell(req: FeeCalcRequest):
    """Calculate sell fees."""
    from main import fee_calc
    order_type = 'limit' if req.is_maker else 'market'
    result = fee_calc.calculate_sell_proceeds(req.amount, req.price, order_type)
    return JSONResponse(content=result)


@router.post("/round-trip")
async def fee_round_trip(req: RoundTripRequest):
    """Calculate round-trip (buy+sell) fees and profit."""
    from main import fee_calc
    result = fee_calc.calculate_round_trip_cost(req.amount, req.buy_price)
    return JSONResponse(content=result)


@router.post("/profit-target")
async def fee_profit_target(req: ProfitTargetRequest):
    """Calculate price needed to hit profit target after fees."""
    from main import fee_calc
    result = fee_calc.calculate_profit_target(
        req.entry_price, req.amount, req.target_profit_pct
    )
    return JSONResponse(content=result)
