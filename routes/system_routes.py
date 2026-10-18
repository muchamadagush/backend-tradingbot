# File: backend/routes/system_routes.py
"""
System, maintenance, and bot manager endpoints
"""

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
import sys_logger
import strategy
from auth import require_admin, require_trader, optional_auth, AuthUser

router = APIRouter(prefix="/api", tags=["system"])


class BotConfigUpdate(BaseModel):
    config: dict


@router.get("/system/logs")
async def system_logs(user: AuthUser = Depends(optional_auth)):
    """Get recent system logs. Read-only access."""
    return {"logs": sys_logger.get_logs()}


@router.get("/bots")
async def list_bots(user: AuthUser = Depends(optional_auth)):
    """Get status of all registered bots. Read-only access."""
    from bot_manager import bot_registry
    return {
        "bots": bot_registry.get_status(),
        "running": bot_registry.get_running_bots(),
    }


@router.post("/bots/{name}/start")
async def start_bot(name: str, background_tasks: BackgroundTasks, user: AuthUser = Depends(require_trader)):
    """Start a specific bot. Requires trader role."""
    from bot_manager import bot_registry
    
    bot = bot_registry.get(name)
    if not bot:
        return {"error": f"Bot '{name}' not found"}
    
    if bot.is_running:
        return {"error": f"Bot '{name}' is already running"}
    
    background_tasks.add_task(bot.start)
    return {"status": "starting", "bot": name, "started_by": user.user_id}


@router.post("/bots/{name}/stop")
async def stop_bot(name: str, user: AuthUser = Depends(require_trader)):
    """Stop a specific bot. Requires trader role."""
    from bot_manager import bot_registry
    
    bot = bot_registry.get(name)
    if not bot:
        return {"error": f"Bot '{name}' not found"}
    
    bot.stop()
    return {"status": "stopped", "bot": name, "stopped_by": user.user_id}


@router.post("/bots/{name}/config")
async def update_bot_config(name: str, update: BotConfigUpdate, user: AuthUser = Depends(require_admin)):
    """Hot-reload bot configuration without restart. Requires admin role."""
    from bot_manager import bot_registry
    
    bot = bot_registry.get(name)
    if not bot:
        return {"error": f"Bot '{name}' not found"}
    
    bot_registry.update_bot_config(name, update.config)
    return {"status": "updated", "bot": name, "config": update.config, "updated_by": user.user_id}


@router.get("/positions")
async def unified_positions(user: AuthUser = Depends(optional_auth)):
    """Get all positions across all bots. Read-only access."""
    from bot_manager import position_tracker
    return position_tracker.get_summary()


@router.get("/positions/{bot_type}")
async def positions_by_bot(bot_type: str, user: AuthUser = Depends(optional_auth)):
    """Get positions for a specific bot type. Read-only access."""
    from bot_manager import position_tracker
    
    positions = position_tracker.get_by_bot_type(bot_type.upper())
    return {
        "bot_type": bot_type.upper(),
        "count": len(positions),
        "positions": [p.to_dict() for p in positions],
    }


@router.post("/maintenance/logs")
async def run_log_rotation(user: AuthUser = Depends(require_admin)):
    """Manually trigger log rotation. Requires admin role."""
    from bot_manager import rotate_logs
    rotate_logs(max_entries=500)
    return {"status": "success", "message": "Logs rotated", "triggered_by": user.user_id}


@router.post("/maintenance/archive")
async def run_archive(user: AuthUser = Depends(require_admin)):
    """Manually trigger trade archiving. Requires admin role."""
    from bot_manager import archive_old_trades
    count = archive_old_trades(days_old=30)
    return {"status": "success", "archived_trades": count, "triggered_by": user.user_id}


@router.get("/strategy/quality")
async def strategy_quality():
    """Get signal quality statistics for all strategies."""
    return strategy.get_signal_quality_stats()
