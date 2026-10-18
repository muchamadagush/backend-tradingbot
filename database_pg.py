"""
PostgreSQL Database Layer using SQLAlchemy ORM
Replaces: database.py (SQLite)

Usage:
    from database_pg import engine, Base, get_db_session, init_pg_db
    
    # Initialize database
    init_pg_db()
    
    # Get session
    session = get_db_session()
    
    # Query
    trades = session.query(Trade).all()
    session.close()
"""

import os
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, List
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime,
    ForeignKey, Index, Text, Boolean, func, event
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, scoped_session
from sqlalchemy.pool import NullPool  # For multi-threaded FastAPI
from contextlib import contextmanager

# ===== CONFIGURATION =====
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/crypto_bot"
)

# Create engine with connection pooling
engine = create_engine(
    DATABASE_URL,
    echo=False,  # Set to True for SQL debugging
    pool_pre_ping=True,  # Verify connections before using
    pool_size=20,  # Max connections in pool
    max_overflow=0,  # Don't create extra connections
)

# Session factory
session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
SessionLocal = scoped_session(session_factory)

# Base for ORM models
Base = declarative_base()


# ===== ENUMS =====
class TradeState(str, PyEnum):
    """Trade state machine (P1: Immutable Trade Ledger)"""
    PENDING = "PENDING"      # Order submitted
    OPEN = "OPEN"            # Position active
    CLOSING = "CLOSING"      # Sell order submitted
    CLOSED = "CLOSED"        # Position fully closed
    CANCELLED = "CANCELLED"  # Order cancelled
    FAILED = "FAILED"        # Order failed


class BotType(str, PyEnum):
    """Bot types for risk management profiles"""
    HUNTER = "HUNTER"    # Aggressive: 3% risk, 3 positions, 5% daily loss
    TRADER = "TRADER"    # Moderate: 4% risk, 3 positions, 8% daily loss
    SNIPER = "SNIPER"    # Conservative: 2% risk, 1 position, 3% daily loss


# ===== MODELS =====
class Trade(Base):
    """
    Main trades table with state machine.
    Immutably tracked via TradeLedger (P1).
    """
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)  # BUY
    buy_price = Column(Float, nullable=False)
    amount = Column(Float, nullable=False)
    total_usdt = Column(Float, nullable=False)
    
    # State machine
    status = Column(String(20), default=TradeState.PENDING, nullable=False)
    sell_price = Column(Float)
    profit_usdt = Column(Float)
    
    # Metadata
    fee_paid = Column(Float, default=0)
    mode = Column(String(20), default='PAPER')  # LIVE/PAPER/BACKTEST
    bot_type = Column(String(20), default=BotType.HUNTER)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    opened_at = Column(DateTime)
    closing_at = Column(DateTime)
    closed_at = Column(DateTime)
    
    # Relationship
    ledger_entries = relationship("TradeLedger", back_populates="trade", cascade="all, delete-orphan")
    
    # Indexes for fast queries
    __table_args__ = (
        Index('idx_trades_symbol_status', 'symbol', 'status'),
        Index('idx_trades_bot_type', 'bot_type'),
        Index('idx_trades_created_at', 'created_at'),
        Index('idx_trades_mode', 'mode'),
    )
    
    def __repr__(self):
        return f"<Trade {self.id}: {self.symbol} {self.side} @ {self.buy_price}>"


class TradeLedger(Base):
    """
    Immutable audit log of all trade state changes (P1).
    Cannot be modified, only appended.
    Critical for financial compliance.
    """
    __tablename__ = "trade_ledger"

    id = Column(Integer, primary_key=True)
    trade_id = Column(Integer, ForeignKey('trades.id', ondelete='CASCADE'), nullable=False)
    
    # Event tracking
    event_type = Column(String(20), nullable=False)  # CREATED/OPENED/CLOSING/CLOSED/CANCELLED/FAILED
    old_state = Column(String(20))
    new_state = Column(String(20), nullable=False)
    
    # Snapshot of data at this moment
    price = Column(Float)
    amount = Column(Float)
    value_usdt = Column(Float)
    fee = Column(Float, default=0)
    pnl = Column(Float)
    
    # Metadata
    bot_type = Column(String(20))
    reason = Column(String(100))  # TP_HIT, SL_HIT, MANUAL, ERROR, etc.
    
    # Immutable timestamp
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    
    # Relationship (read-only)
    trade = relationship("Trade", back_populates="ledger_entries")
    
    # Indexes for auditing
    __table_args__ = (
        Index('idx_ledger_trade_id', 'trade_id'),
        Index('idx_ledger_timestamp', 'timestamp'),
        Index('idx_ledger_bot_type', 'bot_type'),
        Index('idx_ledger_event_type', 'event_type'),
    )
    
    def __repr__(self):
        return f"<TradeLedger trade_id={self.trade_id} {self.event_type} {self.new_state}>"


class BacktestResult(Base):
    """
    Historical backtest results for strategy analysis.
    """
    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    period_days = Column(Integer, nullable=False)
    
    initial_capital = Column(Float, nullable=False)
    final_capital = Column(Float, nullable=False)
    roi_pct = Column(Float, nullable=False)
    
    total_trades = Column(Integer, nullable=False)
    win_rate_pct = Column(Float, nullable=False)
    max_drawdown_pct = Column(Float, nullable=False)
    profit_factor = Column(Float, nullable=False)
    total_fees_paid = Column(Float, nullable=False)
    
    result_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_backtest_symbol_timeframe', 'symbol', 'timeframe'),
        Index('idx_backtest_created_at', 'created_at'),
    )
    
    def __repr__(self):
        return f"<BacktestResult {self.symbol} {self.timeframe} ROI={self.roi_pct}%>"


class PaperTradingSnapshot(Base):
    """
    Paper trading account snapshots for analytics.
    """
    __tablename__ = "paper_trading_snapshots"

    id = Column(Integer, primary_key=True)
    balance = Column(Float, nullable=False)
    total_equity = Column(Float, nullable=False)
    total_pnl = Column(Float, nullable=False)
    open_positions = Column(Integer, nullable=False)
    snapshot_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_snapshot_created_at', 'created_at'),
    )


class SystemFlag(Base):
    """
    Global system flags (kill switch, feature toggles, etc.).
    P0: Critical for trading control.
    """
    __tablename__ = "system_flags"

    key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<SystemFlag {self.key}={self.value}>"


class Account(Base):
    """
    Main trading account equity.
    Singleton table (id=1).
    """
    __tablename__ = "account"

    id = Column(Integer, primary_key=True)
    balance = Column(Float, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<Account balance={self.balance}>"


class OpenPosition(Base):
    """
    Unrealized positions for real-time portfolio tracking.
    """
    __tablename__ = "open_positions"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(20), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, nullable=False)
    bot_type = Column(String(20))
    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_position_symbol', 'symbol'),
        Index('idx_position_bot_type', 'bot_type'),
    )
    
    def __repr__(self):
        return f"<OpenPosition {self.symbol} {self.amount} @ {self.entry_price}>"


class RiskManagerState(Base):
    """
    Persisted risk manager state per bot type.
    Survives server restarts to maintain risk limits.
    """
    __tablename__ = "risk_manager_state"

    id = Column(Integer, primary_key=True)
    bot_type = Column(String(20), nullable=False, unique=True, index=True)
    
    # Daily tracking
    daily_pnl = Column(Float, default=0.0, nullable=False)
    trades_today = Column(Integer, default=0, nullable=False)
    last_reset_date = Column(String(10), nullable=False)  # YYYY-MM-DD format
    
    # Balance tracking
    peak_balance = Column(Float, default=1000.0, nullable=False)
    current_balance = Column(Float, default=1000.0, nullable=False)
    
    # Cooldown tracking
    last_loss_timestamp = Column(Float, default=0.0, nullable=False)
    consecutive_losses = Column(Integer, default=0, nullable=False)
    
    # Metadata
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<RiskManagerState {self.bot_type} daily_pnl={self.daily_pnl}>"


class SolanaTrade(Base):
    """
    Solana/Jupiter swap transactions.
    Replaces solana_trades.json for ACID compliance.
    """
    __tablename__ = "solana_trades"

    id = Column(Integer, primary_key=True)
    trade_id = Column(String(50), unique=True, nullable=False, index=True)  # e.g., SOL-1768311357
    timestamp = Column(DateTime, nullable=False, index=True)
    symbol = Column(String(30), nullable=False, index=True)  # e.g., SPX/SOL
    side = Column(String(10), nullable=False)  # BUY or SELL
    
    # Amounts
    amount_in = Column(Float, nullable=False)
    amount_out = Column(Float, nullable=False)
    price = Column(Float, default=0.0)  # Calculated price
    
    # Token info
    token_address = Column(String(100))  # Mint address
    txid = Column(String(100))  # Transaction signature
    
    # Status and source
    status = Column(String(50), default='Success')
    source = Column(String(20), default='Jupiter')
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    __table_args__ = (
        Index('idx_solana_trades_symbol', 'symbol'),
        Index('idx_solana_trades_timestamp', 'timestamp'),
        Index('idx_solana_trades_side', 'side'),
    )
    
    def __repr__(self):
        return f"<SolanaTrade {self.trade_id} {self.symbol} {self.side}>"
    
    def to_dict(self):
        """Convert to dict for API responses (backward compatible with JSON format)"""
        return {
            "id": self.trade_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "symbol": self.symbol,
            "side": self.side,
            "price": self.price,
            "amount_in": self.amount_in,
            "amount_out": self.amount_out,
            "token_address": self.token_address,
            "txid": self.txid,
            "status": self.status,
            "source": self.source
        }


# ===== DATABASE INITIALIZATION =====
def init_pg_db():
    """
    Initialize PostgreSQL database.
    Creates all tables defined in Base.metadata.
    
    Call once at startup:
        from database_pg import init_pg_db
        init_pg_db()
    """
    Base.metadata.create_all(bind=engine)
    
    # Initialize default values
    session = get_db_session()
    try:
        # Ensure system flags exist
        flags = session.query(SystemFlag).filter_by(key='TRADING_DISABLED').first()
        if not flags:
            session.add(SystemFlag(key='TRADING_DISABLED', value='false'))
        
        # Ensure account exists
        account = session.query(Account).filter_by(id=1).first()
        if not account:
            session.add(Account(id=1, balance=0.0))
        
        session.commit()
        print("✅ PostgreSQL database initialized")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Error initializing database: {e}")
        raise
    finally:
        session.close()


# ===== SESSION MANAGEMENT =====
def get_db_session():
    """
    Get a new database session.
    
    Usage:
        session = get_db_session()
        trades = session.query(Trade).all()
        session.close()
    
    Or with context manager (recommended):
        with session_scope() as session:
            trades = session.query(Trade).all()  # Auto-closed on exit
    """
    return SessionLocal()


@contextmanager
def session_scope():
    """
    Provide a transactional scope for database operations.
    Auto-commits on success, rolls back on exception.
    
    Usage:
        with session_scope() as session:
            session.add(trade)
            # Auto-commits here
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def transaction():
    """
    DEPRECATED: Use session_scope() instead.
    Kept for backward compatibility with old database.py.
    """
    with session_scope() as session:
        yield session


# ===== QUERY HELPERS =====
def get_trades(limit: int = 100) -> List[Trade]:
    """Get recent trades."""
    session = get_db_session()
    try:
        trades = session.query(Trade).order_by(Trade.created_at.desc()).limit(limit).all()
        return trades
    finally:
        session.close()


def get_trade_by_id(trade_id: int) -> Optional[Trade]:
    """Get trade by ID."""
    session = get_db_session()
    try:
        return session.query(Trade).filter_by(id=trade_id).first()
    finally:
        session.close()


def get_trades_by_symbol(symbol: str) -> List[Trade]:
    """Get all trades for a symbol."""
    session = get_db_session()
    try:
        return session.query(Trade).filter_by(symbol=symbol).order_by(Trade.created_at.desc()).all()
    finally:
        session.close()


def get_open_trades() -> List[Trade]:
    """Get all OPEN trades."""
    session = get_db_session()
    try:
        return session.query(Trade).filter_by(status=TradeState.OPEN).all()
    finally:
        session.close()


def get_account_balance() -> float:
    """Get current account balance."""
    session = get_db_session()
    try:
        account = session.query(Account).filter_by(id=1).first()
        return account.balance if account else 0.0
    finally:
        session.close()


def update_account_balance(balance: float):
    """Update account balance."""
    session = get_db_session()
    try:
        account = session.query(Account).filter_by(id=1).first()
        if account:
            account.balance = balance
            session.commit()
    finally:
        session.close()


def get_system_flag(key: str) -> Optional[str]:
    """Get system flag value."""
    session = get_db_session()
    try:
        flag = session.query(SystemFlag).filter_by(key=key).first()
        return flag.value if flag else None
    finally:
        session.close()


def set_system_flag(key: str, value: str):
    """Set system flag value."""
    session = get_db_session()
    try:
        flag = session.query(SystemFlag).filter_by(key=key).first()
        if flag:
            flag.value = value
        else:
            session.add(SystemFlag(key=key, value=value))
        session.commit()
    finally:
        session.close()


# ===== RISK MANAGER STATE PERSISTENCE =====
def get_risk_manager_state(bot_type: str) -> Optional[dict]:
    """Get persisted risk manager state for a bot type."""
    session = get_db_session()
    try:
        state = session.query(RiskManagerState).filter_by(bot_type=bot_type).first()
        if state:
            return {
                "bot_type": state.bot_type,
                "daily_pnl": state.daily_pnl,
                "trades_today": state.trades_today,
                "last_reset_date": state.last_reset_date,
                "peak_balance": state.peak_balance,
                "current_balance": state.current_balance,
                "last_loss_timestamp": state.last_loss_timestamp,
                "consecutive_losses": state.consecutive_losses,
            }
        return None
    finally:
        session.close()


def save_risk_manager_state(
    bot_type: str,
    daily_pnl: float,
    trades_today: int,
    last_reset_date: str,
    peak_balance: float,
    current_balance: float,
    last_loss_timestamp: float,
    consecutive_losses: int,
):
    """Save/update risk manager state for a bot type."""
    session = get_db_session()
    try:
        state = session.query(RiskManagerState).filter_by(bot_type=bot_type).first()
        if state:
            state.daily_pnl = daily_pnl
            state.trades_today = trades_today
            state.last_reset_date = last_reset_date
            state.peak_balance = peak_balance
            state.current_balance = current_balance
            state.last_loss_timestamp = last_loss_timestamp
            state.consecutive_losses = consecutive_losses
        else:
            state = RiskManagerState(
                bot_type=bot_type,
                daily_pnl=daily_pnl,
                trades_today=trades_today,
                last_reset_date=last_reset_date,
                peak_balance=peak_balance,
                current_balance=current_balance,
                last_loss_timestamp=last_loss_timestamp,
                consecutive_losses=consecutive_losses,
            )
            session.add(state)
        session.commit()
    finally:
        session.close()


# ===== SOLANA TRADES PERSISTENCE =====
def save_solana_trade(
    trade_id: str,
    timestamp: datetime,
    symbol: str,
    side: str,
    amount_in: float,
    amount_out: float,
    token_address: str = None,
    txid: str = None,
    status: str = "Success",
    source: str = "Jupiter",
    price: float = 0.0
) -> Optional[dict]:
    """Save a Solana trade to PostgreSQL."""
    session = get_db_session()
    try:
        trade = SolanaTrade(
            trade_id=trade_id,
            timestamp=timestamp,
            symbol=symbol,
            side=side,
            amount_in=amount_in,
            amount_out=amount_out,
            token_address=token_address,
            txid=txid,
            status=status,
            source=source,
            price=price
        )
        session.add(trade)
        session.commit()
        return trade.to_dict()
    except Exception as e:
        session.rollback()
        print(f"⚠️ Error saving Solana trade: {e}")
        return None
    finally:
        session.close()


def get_solana_trades(limit: int = 50) -> List[dict]:
    """Get recent Solana trades."""
    session = get_db_session()
    try:
        trades = session.query(SolanaTrade).order_by(SolanaTrade.timestamp.desc()).limit(limit).all()
        return [t.to_dict() for t in trades]
    finally:
        session.close()


def get_all_solana_trades() -> List[dict]:
    """Get all Solana trades for analytics."""
    session = get_db_session()
    try:
        trades = session.query(SolanaTrade).order_by(SolanaTrade.timestamp.desc()).all()
        return [t.to_dict() for t in trades]
    finally:
        session.close()


def migrate_solana_trades_from_json(json_path: str = "solana_trades.json") -> int:
    """
    Migrate existing Solana trades from JSON file to PostgreSQL.
    Returns number of trades migrated.
    """
    import json
    import os
    
    if not os.path.exists(json_path):
        print(f"ℹ️ No JSON file found at {json_path}")
        return 0
    
    try:
        with open(json_path, "r") as f:
            trades = json.load(f)
    except Exception as e:
        print(f"⚠️ Error reading JSON: {e}")
        return 0
    
    session = get_db_session()
    migrated = 0
    
    try:
        for t in trades:
            # Check if already exists
            existing = session.query(SolanaTrade).filter_by(trade_id=t.get("id")).first()
            if existing:
                continue
            
            # Parse timestamp
            ts_str = t.get("timestamp")
            try:
                timestamp = datetime.fromisoformat(ts_str) if ts_str else datetime.utcnow()
            except:
                timestamp = datetime.utcnow()
            
            trade = SolanaTrade(
                trade_id=t.get("id", f"SOL-{int(datetime.utcnow().timestamp())}"),
                timestamp=timestamp,
                symbol=t.get("symbol", "UNKNOWN/SOL"),
                side=t.get("side", "BUY"),
                amount_in=float(t.get("amount_in", 0)),
                amount_out=float(t.get("amount_out", 0)),
                token_address=t.get("token_address"),
                txid=t.get("txid"),
                status=t.get("status", "Success"),
                source=t.get("source", "Jupiter"),
                price=float(t.get("price", 0))
            )
            session.add(trade)
            migrated += 1
        
        session.commit()
        print(f"✅ Migrated {migrated} Solana trades from JSON to PostgreSQL")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Migration error: {e}")
    finally:
        session.close()
    
    return migrated


# ===== CONNECTION MANAGEMENT =====
def close_db():
    """Close all database connections."""
    SessionLocal.remove()
    engine.dispose()
    print("✅ Database connections closed")


if __name__ == "__main__":
    # Test connection
    try:
        with engine.connect() as conn:
            result = conn.execute("SELECT 1")
            print(f"✅ PostgreSQL connection successful: {result.fetchone()}")
        
        # Initialize database
        init_pg_db()
        
    except Exception as e:
        print(f"❌ PostgreSQL connection failed: {e}")
        print("\nMake sure PostgreSQL is running and DATABASE_URL is correct:")
        print(f"Current: {DATABASE_URL}")
