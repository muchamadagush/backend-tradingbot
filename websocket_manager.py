# File: backend/websocket_manager.py
"""
WebSocket Manager for real-time updates
Replaces polling with push-based notifications
"""

import asyncio
import json
from typing import Set, Dict, Any
from fastapi import WebSocket
from datetime import datetime
import sys_logger


class ConnectionManager:
    """
    Manages WebSocket connections and broadcasts updates to all clients.
    Thread-safe using asyncio locks.
    """
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._broadcast_queue: asyncio.Queue = None
        self._broadcast_task: asyncio.Task = None
    
    async def connect(self, websocket: WebSocket):
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        sys_logger.add_log(f"📡 WebSocket client connected ({len(self.active_connections)} total)")
    
    async def disconnect(self, websocket: WebSocket):
        """Unregister a WebSocket connection."""
        async with self._lock:
            self.active_connections.discard(websocket)
        sys_logger.add_log(f"📡 WebSocket client disconnected ({len(self.active_connections)} total)")
    
    async def send_personal(self, message: Dict[str, Any], websocket: WebSocket):
        """Send message to a specific client."""
        try:
            await websocket.send_json(message)
        except Exception:
            await self.disconnect(websocket)
    
    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all connected clients."""
        if not self.active_connections:
            return
        
        # Add timestamp to message
        message["_timestamp"] = datetime.utcnow().isoformat()
        
        # Copy set to avoid modification during iteration
        async with self._lock:
            connections = set(self.active_connections)
        
        # Send to all, collect failed ones
        failed = []
        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception:
                failed.append(connection)
        
        # Remove failed connections
        if failed:
            async with self._lock:
                for conn in failed:
                    self.active_connections.discard(conn)
    
    async def broadcast_bot_status(self, is_running: bool, activity: str):
        """Broadcast bot status update."""
        await self.broadcast({
            "type": "bot_status",
            "data": {
                "is_running": is_running,
                "current_activity": activity,
                "running": is_running,  # Legacy
                "activity": activity,   # Legacy
            }
        })
    
    async def broadcast_trade_update(self, trade: Dict[str, Any]):
        """Broadcast new trade or trade update."""
        await self.broadcast({
            "type": "trade_update",
            "data": trade
        })
    
    async def broadcast_log(self, log_message: str):
        """Broadcast system log."""
        await self.broadcast({
            "type": "system_log",
            "data": {"message": log_message}
        })
    
    async def broadcast_balance(self, balances: Dict[str, Any]):
        """Broadcast balance update."""
        await self.broadcast({
            "type": "balance_update",
            "data": balances
        })
    
    async def broadcast_solana_status(self, status: Dict[str, Any]):
        """Broadcast Solana auto-bot status."""
        await self.broadcast({
            "type": "solana_status",
            "data": status
        })
    
    async def broadcast_price(self, symbol: str, price: float, bid: float = None, ask: float = None):
        """Broadcast price update."""
        await self.broadcast({
            "type": "price_update",
            "data": {
                "symbol": symbol,
                "price": price,
                "bid": bid,
                "ask": ask,
            }
        })
    
    @property
    def connection_count(self) -> int:
        """Get number of active connections."""
        return len(self.active_connections)


# Singleton instance
ws_manager = ConnectionManager()


# Helper to integrate with sys_logger
class WebSocketLogHandler:
    """
    Intercepts logs and broadcasts them via WebSocket.
    """
    
    def __init__(self, manager: ConnectionManager):
        self.manager = manager
        self._original_add_log = None
    
    def install(self):
        """Install the WebSocket log handler."""
        import sys_logger as sl
        self._original_add_log = sl.add_log
        
        def new_add_log(msg: str):
            # Call original
            self._original_add_log(msg)
            # Broadcast via WebSocket (non-blocking)
            try:
                asyncio.create_task(self.manager.broadcast_log(msg))
            except RuntimeError:
                # No event loop running, skip broadcast
                pass
        
        sl.add_log = new_add_log
    
    def uninstall(self):
        """Restore original log handler."""
        if self._original_add_log:
            import sys_logger as sl
            sl.add_log = self._original_add_log


# Create but don't install yet (will be installed on startup)
ws_log_handler = WebSocketLogHandler(ws_manager)
