# File: backend/routes/__init__.py
"""
FastAPI Routers for TradingBot API
Extracted from main.py to follow Single Responsibility Principle
"""

from .bot_routes import router as bot_router
from .paper_routes import router as paper_router
from .solana_routes import router as solana_router
from .risk_routes import router as risk_router
from .backtest_routes import router as backtest_router
from .analytics_routes import router as analytics_router
from .fee_routes import router as fee_router
from .system_routes import router as system_router

__all__ = [
    "bot_router",
    "paper_router",
    "solana_router",
    "risk_router",
    "backtest_router",
    "analytics_router",
    "fee_router",
    "system_router",
]
