# ==========================================
# File: backend/execution.py
# Execution utilities with retry, rate limit handling, and order book check
# ==========================================

import asyncio
from typing import Optional, Dict, Tuple
import sys_logger


# ==========================================
# ERROR CLASSIFICATION
# ==========================================

class ErrorType:
    """Error types for classification and handling."""
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    MARKET_CLOSED = "market_closed"
    INVALID_ORDER = "invalid_order"
    SLIPPAGE = "slippage"
    UNKNOWN = "unknown"


def classify_error(error: Exception) -> str:
    """Classify error for appropriate handling."""
    error_msg = str(error).lower()
    
    if any(x in error_msg for x in ["rate limit", "too many", "429", "throttle"]):
        return ErrorType.RATE_LIMIT
    elif any(x in error_msg for x in ["timeout", "connection", "network", "socket"]):
        return ErrorType.NETWORK
    elif any(x in error_msg for x in ["insufficient", "balance", "not enough"]):
        return ErrorType.INSUFFICIENT_BALANCE
    elif any(x in error_msg for x in ["market closed", "trading halted"]):
        return ErrorType.MARKET_CLOSED
    elif any(x in error_msg for x in ["invalid order", "min notional", "lot size"]):
        return ErrorType.INVALID_ORDER
    elif any(x in error_msg for x in ["slippage", "price moved"]):
        return ErrorType.SLIPPAGE
    else:
        return ErrorType.UNKNOWN


# ==========================================
# RETRY CONFIGURATION
# ==========================================

RETRY_CONFIG = {
    ErrorType.RATE_LIMIT: {"max_retries": 5, "base_delay": 10, "backoff": 2.0},
    ErrorType.NETWORK: {"max_retries": 3, "base_delay": 2, "backoff": 1.5},
    ErrorType.SLIPPAGE: {"max_retries": 2, "base_delay": 1, "backoff": 1.0},
    ErrorType.UNKNOWN: {"max_retries": 2, "base_delay": 1, "backoff": 1.5},
    # No retry for these
    ErrorType.INSUFFICIENT_BALANCE: {"max_retries": 0, "base_delay": 0, "backoff": 1.0},
    ErrorType.MARKET_CLOSED: {"max_retries": 0, "base_delay": 0, "backoff": 1.0},
    ErrorType.INVALID_ORDER: {"max_retries": 0, "base_delay": 0, "backoff": 1.0},
}


async def with_retry(func, *args, max_retries: int = 3, **kwargs):
    """
    Execute async function with intelligent retry based on error type.
    
    Returns:
        Tuple of (success: bool, result: any, error: str or None)
    """
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            result = await func(*args, **kwargs)
            return True, result, None
        except Exception as e:
            last_error = e
            error_type = classify_error(e)
            config = RETRY_CONFIG.get(error_type, RETRY_CONFIG[ErrorType.UNKNOWN])
            
            if attempt >= config["max_retries"]:
                sys_logger.add_log(f"❌ {error_type}: {e} (no more retries)")
                break
            
            delay = config["base_delay"] * (config["backoff"] ** attempt)
            sys_logger.add_log(f"⚠️ {error_type}: {e} - retry in {delay:.1f}s")
            await asyncio.sleep(delay)
    
    return False, None, str(last_error)


# ==========================================
# ORDER BOOK CHECK
# ==========================================

async def check_liquidity(exchange, symbol: str, trade_amount_usdt: float, 
                          side: str = "buy", max_slippage_pct: float = 0.5) -> Tuple[bool, Dict]:
    """
    Check order book for sufficient liquidity before placing order.
    
    Args:
        exchange: ccxt exchange instance
        symbol: Trading pair (e.g., "BTC/USDT")
        trade_amount_usdt: Amount in USDT to trade
        side: "buy" or "sell"
        max_slippage_pct: Maximum acceptable slippage percentage
    
    Returns:
        Tuple of (is_liquid: bool, analysis: dict)
    """
    try:
        orderbook = await exchange.fetch_order_book(symbol, limit=50)
        
        if side == "buy":
            book_side = orderbook.get("asks", [])
        else:
            book_side = orderbook.get("bids", [])
        
        if not book_side:
            return False, {"error": "Empty order book"}
        
        # Best price (top of book)
        best_price = book_side[0][0]
        
        # Calculate how much we can fill and at what weighted average price
        total_usdt = 0
        total_qty = 0
        filled_levels = []
        
        for price, qty in book_side:
            level_usdt = price * qty
            if total_usdt + level_usdt >= trade_amount_usdt:
                # Partial fill at this level
                remaining = trade_amount_usdt - total_usdt
                partial_qty = remaining / price
                total_usdt += remaining
                total_qty += partial_qty
                filled_levels.append((price, partial_qty, remaining))
                break
            else:
                total_usdt += level_usdt
                total_qty += qty
                filled_levels.append((price, qty, level_usdt))
        
        if total_usdt < trade_amount_usdt:
            return False, {
                "error": "Insufficient liquidity",
                "available_usdt": total_usdt,
                "requested_usdt": trade_amount_usdt,
            }
        
        # Calculate weighted average price
        vwap = trade_amount_usdt / total_qty if total_qty > 0 else best_price
        
        # Calculate slippage
        slippage_pct = abs((vwap - best_price) / best_price) * 100
        
        analysis = {
            "symbol": symbol,
            "side": side,
            "best_price": best_price,
            "vwap": round(vwap, 8),
            "slippage_pct": round(slippage_pct, 4),
            "levels_used": len(filled_levels),
            "total_qty": round(total_qty, 8),
            "total_usdt": round(total_usdt, 2),
        }
        
        is_liquid = slippage_pct <= max_slippage_pct
        
        if not is_liquid:
            analysis["warning"] = f"Slippage {slippage_pct:.2f}% exceeds max {max_slippage_pct}%"
            sys_logger.add_log(f"⚠️ Low liquidity for {symbol}: {slippage_pct:.2f}% slippage")
        
        return is_liquid, analysis
        
    except Exception as e:
        sys_logger.add_log(f"❌ Order book check failed: {e}")
        return False, {"error": str(e)}


# ==========================================
# EXECUTION WITH CHECKS
# ==========================================

async def safe_execute_order(exchange, symbol: str, side: str, amount_usdt: float,
                             amount_asset: Optional[float] = None,
                             max_slippage_pct: float = 0.5, 
                             check_liquidity_first: bool = True) -> Dict:
    """
    Execute order with pre-checks and retry logic.
    
    Args:
        exchange: ccxt exchange instance
        symbol: Trading pair
        side: "buy" or "sell"
        amount_usdt: Amount in USDT
        amount_asset: Direct asset quantity (crucial for sells to avoid rounding errors)
        max_slippage_pct: Max acceptable slippage
        check_liquidity_first: Whether to check order book before trading
    
    Returns:
        Order result dict
    """
    result = {
        "success": False,
        "symbol": symbol,
        "side": side,
        "amount_usdt": amount_usdt,
    }
    
    # Step 1: Check liquidity if enabled
    if check_liquidity_first:
        is_liquid, liquidity = await check_liquidity(
            exchange, symbol, amount_usdt, side, max_slippage_pct
        )
        result["liquidity_check"] = liquidity
        
        if not is_liquid:
            result["error"] = liquidity.get("error", liquidity.get("warning", "Low liquidity"))
            return result
    
    # Step 2: Get current price
    try:
        ticker = await exchange.fetch_ticker(symbol)
        price = ticker["last"]
        result["price"] = price
    except Exception as e:
        result["error"] = f"Failed to get price: {e}"
        return result
    
    # Step 3: Determine quantity and order function
    if side == "buy":
        qty = amount_usdt / price
        order_func = exchange.create_market_buy_order
    else:
        # Use exact token amount for sell if provided, avoiding price divergence
        qty = amount_asset if amount_asset is not None and amount_asset > 0 else (amount_usdt / price)
        order_func = exchange.create_market_sell_order
    
    # Format to exchange step size / precision
    try:
        if hasattr(exchange, 'amount_to_precision'):
            qty = float(exchange.amount_to_precision(symbol, qty))
    except Exception as prec_err:
        pass
    
    success, order, error = await with_retry(order_func, symbol, qty)
    
    if success:
        result["success"] = True
        result["order"] = order
        result["filled_qty"] = order.get("filled", qty)
        result["filled_price"] = order.get("average", price)
        sys_logger.add_log(f"✅ {side.upper()} {symbol} executed: {qty:.6f} @ {price:.4f}")
    else:
        result["error"] = error
        sys_logger.add_log(f"❌ {side.upper()} {symbol} failed: {error}")
    
    return result


# ==========================================
# SPREAD CHECK
# ==========================================

async def check_spread(exchange, symbol: str, max_spread_pct: float = 1.0) -> Tuple[bool, Dict]:
    """
    Check bid-ask spread before trading.
    
    Returns:
        Tuple of (is_acceptable: bool, spread_info: dict)
    """
    try:
        ticker = await exchange.fetch_ticker(symbol)
        bid = ticker.get("bid", 0)
        ask = ticker.get("ask", 0)
        
        if not bid or not ask:
            return False, {"error": "Missing bid/ask"}
        
        spread = ask - bid
        spread_pct = (spread / bid) * 100
        mid_price = (bid + ask) / 2
        
        info = {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "spread": round(spread, 8),
            "spread_pct": round(spread_pct, 4),
            "mid_price": round(mid_price, 8),
        }
        
        is_acceptable = spread_pct <= max_spread_pct
        
        if not is_acceptable:
            info["warning"] = f"Spread {spread_pct:.2f}% exceeds max {max_spread_pct}%"
        
        return is_acceptable, info
        
    except Exception as e:
        return False, {"error": str(e)}
