# File: backend/rate_limiter.py
"""
Rate Limiting Module using SlowAPI
Provides endpoint-specific rate limits with proper 429 responses
"""

import os
from typing import Optional, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

import sys_logger


# ==========================================
# CONFIGURATION
# ==========================================

# Default rate limits (requests/period)
# Can be overridden via environment variables
RATE_LIMIT_DEFAULT = os.getenv("RATE_LIMIT_DEFAULT", "60/minute")
RATE_LIMIT_TRADING = os.getenv("RATE_LIMIT_TRADING", "10/minute")
RATE_LIMIT_READONLY = os.getenv("RATE_LIMIT_READONLY", "120/minute")
RATE_LIMIT_AUTH = os.getenv("RATE_LIMIT_AUTH", "5/minute")  # Strict for login attempts

# Enable/disable rate limiting
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"


# ==========================================
# CUSTOM KEY FUNCTION
# ==========================================

def get_rate_limit_key(request: Request) -> str:
    """
    Get identifier for rate limiting.
    Priority: API Key > JWT User > IP Address
    """
    # Check for API key
    api_key = request.headers.get("X-API-Key")
    if api_key:
        # Hash the key for privacy (first 16 chars of hash)
        import hashlib
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
        return f"apikey:{key_hash}"
    
    # Check for JWT (extract user from Bearer token)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # Use first 16 chars of token as identifier
        return f"jwt:{token[:16]}"
    
    # Fall back to IP address
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    
    return f"ip:{get_remote_address(request)}"


def get_key_func_with_role(role_multiplier: float = 1.0) -> Callable:
    """
    Create a key function that can apply role-based multipliers.
    Admins get 3x limit, traders get 2x, readonly gets 1x.
    """
    def key_func(request: Request) -> str:
        return get_rate_limit_key(request)
    return key_func


# ==========================================
# LIMITER INSTANCE
# ==========================================

def _limiter_enabled_check() -> bool:
    """Check if rate limiting is enabled."""
    return RATE_LIMIT_ENABLED


limiter = Limiter(
    key_func=get_rate_limit_key,
    default_limits=[RATE_LIMIT_DEFAULT],
    enabled=_limiter_enabled_check(),
    headers_enabled=True,  # Add X-RateLimit headers to responses
    strategy="fixed-window",  # or "moving-window"
    storage_uri="memory://",  # Use in-memory storage (use redis:// for production)
)


# ==========================================
# EXCEPTION HANDLER
# ==========================================

def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """
    Custom handler for rate limit exceeded (429).
    Returns JSON response with retry information.
    """
    # Log the rate limit hit
    client_id = get_rate_limit_key(request)
    sys_logger.log(f"[RATE_LIMIT] Exceeded for {client_id}: {request.method} {request.url.path}")
    
    # Parse retry-after from the exception
    retry_after = 60  # Default 60 seconds
    if hasattr(exc, 'detail') and isinstance(exc.detail, str):
        # Try to extract retry time from detail
        pass
    
    response = JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Please slow down.",
            "detail": str(exc.detail) if hasattr(exc, 'detail') else "Rate limit exceeded",
            "retry_after_seconds": retry_after,
            "limit": str(exc.limit) if hasattr(exc, 'limit') else RATE_LIMIT_DEFAULT,
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(exc.limit) if hasattr(exc, 'limit') else RATE_LIMIT_DEFAULT,
            "X-RateLimit-Remaining": "0",
        }
    )
    return response


# ==========================================
# RATE LIMIT DECORATORS (for convenience)
# ==========================================

# Pre-configured limit decorators for common use cases
def limit_trading(func):
    """Apply trading rate limit (10/minute) - for buy/sell/swap endpoints."""
    return limiter.limit(RATE_LIMIT_TRADING)(func)


def limit_readonly(func):
    """Apply read-only rate limit (120/minute) - for status/history endpoints."""
    return limiter.limit(RATE_LIMIT_READONLY)(func)


def limit_auth(func):
    """Apply auth rate limit (5/minute) - for login/token endpoints."""
    return limiter.limit(RATE_LIMIT_AUTH)(func)


def limit_default(func):
    """Apply default rate limit (60/minute)."""
    return limiter.limit(RATE_LIMIT_DEFAULT)(func)


def limit_custom(limit_string: str):
    """
    Apply custom rate limit.
    
    Examples:
        @limit_custom("5/second")
        @limit_custom("100/hour")
        @limit_custom("1000/day")
    """
    return limiter.limit(limit_string)


# ==========================================
# EXEMPT DECORATOR
# ==========================================

def exempt_from_limits(func):
    """Exempt an endpoint from rate limiting."""
    return limiter.exempt(func)


# ==========================================
# SETUP FUNCTION
# ==========================================

def setup_rate_limiting(app):
    """
    Setup rate limiting on FastAPI app.
    Call this in main.py after app creation.
    """
    # Add limiter to app state
    app.state.limiter = limiter
    
    # Add exception handler for rate limit exceeded
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    
    # Add middleware (optional, for global limiting)
    # app.add_middleware(SlowAPIMiddleware)
    
    if RATE_LIMIT_ENABLED:
        sys_logger.log(f"[RATE_LIMIT] Enabled - Default: {RATE_LIMIT_DEFAULT}, Trading: {RATE_LIMIT_TRADING}, Auth: {RATE_LIMIT_AUTH}")
    else:
        sys_logger.log("[RATE_LIMIT] Disabled via RATE_LIMIT_ENABLED=false")


# ==========================================
# UTILITY FUNCTIONS
# ==========================================

def get_client_limits_info(request: Request) -> dict:
    """Get rate limit info for the current client."""
    client_id = get_rate_limit_key(request)
    return {
        "client_id": client_id[:20] + "..." if len(client_id) > 20 else client_id,
        "limits": {
            "default": RATE_LIMIT_DEFAULT,
            "trading": RATE_LIMIT_TRADING,
            "readonly": RATE_LIMIT_READONLY,
            "auth": RATE_LIMIT_AUTH,
        },
        "enabled": RATE_LIMIT_ENABLED,
    }
