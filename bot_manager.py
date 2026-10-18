# ==========================================
# File: backend/bot_manager.py
# Modular Bot Loader & Unified Position Tracker (P5)
# ==========================================

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
import sys_logger

# Database Imports
from sqlalchemy import text
from database_pg import (
    get_db_session, session_scope, 
    Trade, BacktestResult, OpenPosition, 
    TradeState
)

# ==========================================
# UNIFIED POSITION TRACKER
# ==========================================

@dataclass
class Position:
    """Cross-bot position representation."""
    id: str
    bot_type: str  # HUNTER, TRADER, SNIPER
    symbol: str
    token_address: Optional[str]
    entry_price: float
    current_price: float
    amount: float
    entry_time: float
    unrealized_pnl: float = 0.0
    strategy: str = ""
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Position":
        return cls(**data)

class UnifiedPositionTracker:
    """Track positions across all bots using PostgreSQL OpenPosition table."""
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}
        # Initial load from DB
        self.load()
    
    def load(self):
        """Load positions from PostgreSQL database."""
        try:
            with session_scope() as session:
                db_positions = session.query(OpenPosition).all()
                self.positions = {}
                
                for db_pos in db_positions:
                    # Convert DB model to Position dataclass
                    # Note: OpenPosition table might not have all metadata fields, 
                    # so we map what is available.
                    pos = Position(
                        id=str(db_pos.id),
                        bot_type=db_pos.bot_type or "UNKNOWN",
                        symbol=db_pos.symbol,
                        token_address=None,  # Not currently in OpenPosition table
                        entry_price=float(db_pos.entry_price),
                        current_price=float(db_pos.current_price),
                        amount=float(db_pos.amount),
                        entry_time=db_pos.opened_at.timestamp(),
                        unrealized_pnl=float(db_pos.unrealized_pnl),
                        strategy="DB_LOADED",
                        metadata={}
                    )
                    self.positions[str(db_pos.id)] = pos
                    
            sys_logger.add_log(f"📂 Loaded {len(self.positions)} active positions from DB")
        except Exception as e:
            sys_logger.add_log(f"⚠️ Failed to load positions from DB: {e}")
            self.positions = {}
    
    def save(self):
        """
        No-op: Persistence is handled per-operation in PostgreSQL.
        Kept for backward compatibility if needed.
        """
        pass
    
    def add_position(self, position: Position):
        """Add or update a position in Database and Memory."""
        # 1. Update Memory
        self.positions[position.id] = position
        
        # 2. Update Database
        try:
            with session_scope() as session:
                # Check if exists (try to find by symbol and bot_type to avoid duplicates)
                # or by ID if it's numeric
                db_pos = None
                if position.id.isdigit():
                    db_pos = session.query(OpenPosition).filter_by(id=int(position.id)).first()
                
                if not db_pos:
                    # Try finding by symbol
                    db_pos = session.query(OpenPosition).filter_by(
                        symbol=position.symbol, 
                        bot_type=position.bot_type
                    ).first()

                if db_pos:
                    # Update existing
                    db_pos.current_price = position.current_price
                    db_pos.unrealized_pnl = position.unrealized_pnl
                    db_pos.amount = position.amount
                else:
                    # Insert new
                    new_pos = OpenPosition(
                        symbol=position.symbol,
                        amount=position.amount,
                        entry_price=position.entry_price,
                        current_price=position.current_price,
                        unrealized_pnl=position.unrealized_pnl,
                        bot_type=position.bot_type,
                        opened_at=datetime.fromtimestamp(position.entry_time)
                    )
                    session.add(new_pos)
                    # Flush to get ID if needed, though we primarily use symbol/bot_type for tracking
                    session.flush()
                    # Update ID in memory object if it was a temp ID
                    if not position.id.isdigit():
                        del self.positions[position.id]
                        position.id = str(new_pos.id)
                        self.positions[position.id] = position

        except Exception as e:
            sys_logger.add_log(f"⚠️ Failed to persist position {position.symbol}: {e}")
    
    def remove_position(self, position_id: str):
        """Remove a position from Database and Memory."""
        # 1. Remove from Memory
        if position_id in self.positions:
            del self.positions[position_id]
        
        # 2. Remove from Database
        try:
            if position_id.isdigit():
                with session_scope() as session:
                    session.query(OpenPosition).filter_by(id=int(position_id)).delete()
        except Exception as e:
            sys_logger.add_log(f"⚠️ Failed to remove position {position_id} from DB: {e}")
    
    def get_position(self, position_id: str) -> Optional[Position]:
        """Get a specific position."""
        return self.positions.get(position_id)
    
    def get_by_bot_type(self, bot_type: str) -> List[Position]:
        """Get all positions for a specific bot type."""
        return [p for p in self.positions.values() if p.bot_type == bot_type]
    
    def get_all(self) -> List[Position]:
        """Get all positions."""
        return list(self.positions.values())
    
    def count_by_bot(self) -> Dict[str, int]:
        """Count positions per bot type."""
        counts = {"HUNTER": 0, "TRADER": 0, "SNIPER": 0}
        for p in self.positions.values():
            if p.bot_type in counts:
                counts[p.bot_type] += 1
        return counts
    
    def total_unrealized_pnl(self) -> float:
        """Get total unrealized P&L across all bots."""
        return sum(p.unrealized_pnl for p in self.positions.values())
    
    def update_price(self, position_id: str, current_price: float):
        """Update current price and recalculate P&L in DB."""
        if position_id not in self.positions:
            return
        
        pos = self.positions[position_id]
        pos.current_price = current_price
        pos.unrealized_pnl = (current_price - pos.entry_price) * pos.amount
        
        # Persist update to DB
        self.add_position(pos)
    
    def get_summary(self) -> Dict:
        """Get summary of all positions."""
        positions_list = [p.to_dict() for p in self.positions.values()]
        return {
            "total_positions": len(self.positions),
            "by_bot": self.count_by_bot(),
            "total_unrealized_pnl": round(self.total_unrealized_pnl(), 2),
            "positions": positions_list,
        }

# Global tracker
position_tracker = UnifiedPositionTracker()

# ==========================================
# BASE BOT INTERFACE
# ==========================================

class BaseBot(ABC):
    """Abstract base class for all trading bots."""
    
    def __init__(self, name: str, bot_type: str):
        self.name = name
        self.bot_type = bot_type
        self.is_running = False
        self.config: Dict[str, Any] = {}
    
    @abstractmethod
    async def start(self):
        """Start the bot loop."""
        pass
    
    @abstractmethod
    def stop(self):
        """Stop the bot loop."""
        pass
    
    @abstractmethod
    async def run_cycle(self):
        """Run one iteration of bot logic."""
        pass
    
    def get_status(self) -> Dict:
        """Get bot status."""
        return {
            "name": self.name,
            "bot_type": self.bot_type,
            "is_running": self.is_running,
            "config": self.config,
        }
    
    def update_config(self, new_config: Dict):
        """Hot-reload config without restart."""
        self.config.update(new_config)
        sys_logger.add_log(f"🔄 {self.name} config updated: {list(new_config.keys())}")


# ==========================================
# BOT REGISTRY & LOADER
# ==========================================

class BotRegistry:
    """Registry for managing multiple bots."""
    
    def __init__(self):
        self.bots: Dict[str, BaseBot] = {}
        self._config_file = "bot_config.json"
        self._load_config()
    
    def _load_config(self):
        """Load configuration from file."""
        self.config = {}
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, "r") as f:
                    self.config = json.load(f)
            except:
                pass
    
    def _save_config(self):
        """Save configuration to file."""
        try:
            with open(self._config_file, "w") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            sys_logger.add_log(f"⚠️ Failed to save config: {e}")
    
    def register(self, bot: BaseBot):
        """Register a bot."""
        self.bots[bot.name] = bot
        
        # Apply saved config if exists
        if bot.name in self.config:
            bot.update_config(self.config[bot.name])
        
        sys_logger.add_log(f"📦 Registered bot: {bot.name} ({bot.bot_type})")
    
    def unregister(self, name: str):
        """Unregister a bot."""
        if name in self.bots:
            del self.bots[name]
    
    def get(self, name: str) -> Optional[BaseBot]:
        """Get a bot by name."""
        return self.bots.get(name)
    
    def get_all(self) -> List[BaseBot]:
        """Get all registered bots."""
        return list(self.bots.values())
    
    def get_by_type(self, bot_type: str) -> List[BaseBot]:
        """Get bots by type."""
        return [b for b in self.bots.values() if b.bot_type == bot_type]
    
    async def start(self, name: str):
        """Start a specific bot."""
        if name in self.bots:
            await self.bots[name].start()
    
    def stop(self, name: str):
        """Stop a specific bot."""
        if name in self.bots:
            self.bots[name].stop()
    
    async def start_all(self):
        """Start all bots."""
        for bot in self.bots.values():
            await bot.start()
    
    def stop_all(self):
        """Stop all bots."""
        for bot in self.bots.values():
            bot.stop()
    
    def update_bot_config(self, name: str, new_config: Dict):
        """Update config for a bot (hot-reload)."""
        if name in self.bots:
            self.bots[name].update_config(new_config)
            
            # Persist to file
            if name not in self.config:
                self.config[name] = {}
            self.config[name].update(new_config)
            self._save_config()
    
    def get_status(self) -> Dict:
        """Get status of all bots."""
        return {
            name: bot.get_status() for name, bot in self.bots.items()
        }
    
    def get_running_bots(self) -> List[str]:
        """Get names of running bots."""
        return [name for name, bot in self.bots.items() if bot.is_running]


# Global registry
bot_registry = BotRegistry()


# ==========================================
# LOG ROTATION & DB ARCHIVING (P5)
# ==========================================

def rotate_logs(max_entries: int = 1000):
    """Rotate system logs to keep only recent entries."""
    # sys_logger already keeps last 50, but we can extend this
    logs = sys_logger.get_logs()
    if len(logs) > max_entries:
        sys_logger.LOG_BUFFER = logs[-max_entries:]
        sys_logger.add_log(f"🔄 Logs rotated: kept last {max_entries} entries")


def archive_old_trades(days_old: int = 30):
    """Archive trades older than N days to a separate table using PostgreSQL."""
    try:
        with session_scope() as session:
            cutoff_date = datetime.now() - timedelta(days=days_old)
            
            # Ensure archive table exists (should be handled by migration, but checking here is safe)
            # Insert closed trades into archive
            archive_sql = text('''
                INSERT INTO trades_archive (
                    id, symbol, side, buy_price, amount, total_usdt, fee_paid, created_at, 
                    profit_usdt, status, closed_at, bot_type, archived_at
                )
                SELECT 
                    id, symbol, side, buy_price, amount, total_usdt, fee_paid, created_at, 
                    profit_usdt, status, closed_at, bot_type, :archived_at
                FROM trades 
                WHERE status = 'CLOSED' AND closed_at < :cutoff
                ON CONFLICT (id) DO NOTHING
            ''')
            
            result = session.execute(archive_sql, {
                "archived_at": datetime.now(), 
                "cutoff": cutoff_date
            })
            
            # Delete from main table
            delete_sql = text('''
                DELETE FROM trades 
                WHERE status = 'CLOSED' AND closed_at < :cutoff
            ''')
            
            del_result = session.execute(delete_sql, {"cutoff": cutoff_date})
            
            archived_count = del_result.rowcount
            if archived_count > 0:
                sys_logger.add_log(f"🗄️ Archived {archived_count} old trades to trades_archive")
            
            return archived_count
            
    except Exception as e:
        sys_logger.add_log(f"⚠️ Archive failed: {e}")
        return 0


def archive_old_backtest_results(keep_count: int = 50):
    """Keep only the most recent backtest results in PostgreSQL."""
    try:
        with session_scope() as session:
            # Check count
            count = session.query(BacktestResult).count()
            
            if count > keep_count:
                # Delete oldest, keeping top N sorted by creation
                delete_sql = text('''
                    DELETE FROM backtest_results 
                    WHERE id NOT IN (
                        SELECT id FROM backtest_results 
                        ORDER BY created_at DESC 
                        LIMIT :keep_count
                    )
                ''')
                
                result = session.execute(delete_sql, {"keep_count": keep_count})
                deleted = result.rowcount
                sys_logger.add_log(f"🗄️ Cleaned up {deleted} old backtest results")
                
    except Exception as e:
        sys_logger.add_log(f"⚠️ Backtest cleanup failed: {e}")


# ==========================================
# MAINTENANCE SCHEDULER
# ==========================================

async def run_maintenance():
    """Run periodic maintenance tasks."""
    while True:
        try:
            # Run every hour
            await asyncio.sleep(3600)
            
            rotate_logs(max_entries=500)
            archive_old_trades(days_old=30)
            archive_old_backtest_results(keep_count=50)
            
        except Exception as e:
            sys_logger.add_log(f"⚠️ Maintenance error: {e}")