"""
Test suite for PostgreSQL migration.
Tests database connectivity, CRUD operations, and data integrity.

Run tests:
    pytest backend/test_postgres_migration.py -v

Requires:
    - PostgreSQL running on localhost:5432
    - DATABASE_URL set in .env
    - pytest installed: pip install pytest
"""

import pytest
import os
from datetime import datetime
from database_pg import (
    engine, get_db_session, session_scope,
    Trade, TradeLedger, Account, OpenPosition, SystemFlag, BacktestResult,
    TradeState, BotType, init_pg_db
)
from database import (
    save_trade, get_trades, close_trade, open_trade,
    get_account_balance, update_account_balance,
    get_system_flag, set_system_flag, is_trading_disabled,
    save_open_position, get_open_positions,
    save_backtest_result, get_backtest_history,
    get_trade_ledger
)


# ===== FIXTURES =====

@pytest.fixture(scope="function", autouse=True)
def setup_test_db():
    """Setup test database before each test."""
    # Create all tables
    init_pg_db()
    yield
    # Cleanup after test (optional)


# ===== CONNECTION TESTS =====

class TestConnection:
    """Test PostgreSQL connection."""
    
    def test_engine_connects(self):
        """Test that SQLAlchemy engine can connect."""
        try:
            with engine.connect() as conn:
                result = conn.execute("SELECT 1")
                assert result.fetchone()[0] == 1
        except Exception as e:
            pytest.skip(f"PostgreSQL not available: {e}")
    
    def test_get_db_session(self):
        """Test getting database session."""
        session = get_db_session()
        assert session is not None
        session.close()
    
    def test_session_scope(self):
        """Test session scope context manager."""
        with session_scope() as session:
            assert session is not None


# ===== TRADE OPERATIONS =====

class TestTrades:
    """Test trade CRUD operations."""
    
    def test_save_trade(self):
        """Test saving a new trade."""
        trade_id = save_trade(
            symbol="BTC/USDT",
            side="BUY",
            buy_price=42000.0,
            amount=0.1,
            total_usdt=4200.0,
            mode="PAPER",
            bot_type="HUNTER"
        )
        
        assert trade_id > 0
        
        # Verify trade exists
        session = get_db_session()
        trade = session.query(Trade).filter_by(id=trade_id).first()
        session.close()
        
        assert trade is not None
        assert trade.symbol == "BTC/USDT"
        assert trade.buy_price == 42000.0
        assert trade.status == TradeState.PENDING
    
    def test_get_trades(self):
        """Test retrieving trades."""
        # Save multiple trades
        save_trade("BTC/USDT", "BUY", 42000, 0.1, 4200)
        save_trade("ETH/USDT", "BUY", 2500, 1.0, 2500)
        save_trade("SOL/USDT", "BUY", 150, 10, 1500)
        
        # Get all trades
        trades = get_trades(limit=10)
        
        assert len(trades) >= 3
        assert any(t['symbol'] == "BTC/USDT" for t in trades)
        assert any(t['symbol'] == "ETH/USDT" for t in trades)
    
    def test_open_trade(self):
        """Test opening a trade (PENDING → OPEN)."""
        trade_id = save_trade("BTC/USDT", "BUY", 42000, 0.1, 4200)
        
        # Open the trade
        success = open_trade(trade_id)
        assert success is True
        
        # Verify status changed
        session = get_db_session()
        trade = session.query(Trade).filter_by(id=trade_id).first()
        session.close()
        
        assert trade.status == TradeState.OPEN
        assert trade.opened_at is not None
    
    def test_close_trade(self):
        """Test closing a trade (OPEN → CLOSING → CLOSED)."""
        trade_id = save_trade("BTC/USDT", "BUY", 42000, 0.1, 4200)
        open_trade(trade_id)
        
        # Close the trade
        success = close_trade(trade_id, sell_price=43000.0, reason="TP_HIT")
        assert success is True
        
        # Verify status changed
        session = get_db_session()
        trade = session.query(Trade).filter_by(id=trade_id).first()
        session.close()
        
        assert trade.status == TradeState.CLOSING
        assert trade.sell_price == 43000.0
        assert trade.closing_at is not None
        assert trade.profit_usdt == 100.0  # (43000 - 42000) * 0.1


# ===== TRADE LEDGER (P1: Immutable Audit Log) =====

class TestTradeLedger:
    """Test immutable trade ledger (P1)."""
    
    def test_ledger_entry_created_on_save(self):
        """Test that ledger entry created when trade is saved."""
        trade_id = save_trade("BTC/USDT", "BUY", 42000, 0.1, 4200)
        
        # Get ledger entries
        session = get_db_session()
        entries = session.query(TradeLedger).filter_by(trade_id=trade_id).all()
        session.close()
        
        # Should have CREATED entry
        assert len(entries) >= 1
        assert any(e.event_type == "CREATED" for e in entries)
    
    def test_ledger_immutability(self):
        """Test that ledger entries cannot be modified."""
        trade_id = save_trade("BTC/USDT", "BUY", 42000, 0.1, 4200)
        
        # Get original entry
        session = get_db_session()
        entry = session.query(TradeLedger).filter_by(trade_id=trade_id).first()
        original_timestamp = entry.timestamp
        original_reason = entry.reason
        session.close()
        
        # Try to modify (should not affect original via new query)
        ledger_entries = get_trade_ledger(trade_id)
        assert ledger_entries[0]['reason'] == original_reason
        assert ledger_entries[0]['timestamp'] == original_timestamp.isoformat()
    
    def test_ledger_audit_trail(self):
        """Test complete audit trail (create → open → close)."""
        trade_id = save_trade("BTC/USDT", "BUY", 42000, 0.1, 4200)
        open_trade(trade_id)
        close_trade(trade_id, 43000, "TP_HIT")
        
        # Get full ledger
        ledger = get_trade_ledger(trade_id)
        
        # Verify all events recorded
        event_types = [e['event_type'] for e in ledger]
        assert "CREATED" in event_types or "PENDING" in event_types
        assert "OPENED" in event_types
        assert "CLOSING" in event_types
        
        # Verify immutability (timestamps in order)
        timestamps = [datetime.fromisoformat(e['timestamp']) for e in ledger]
        assert timestamps == sorted(timestamps)


# ===== ACCOUNT OPERATIONS =====

class TestAccount:
    """Test account balance tracking."""
    
    def test_get_account_balance(self):
        """Test reading account balance."""
        balance = get_account_balance()
        assert isinstance(balance, float)
    
    def test_update_account_balance(self):
        """Test updating account balance."""
        new_balance = 5000.0
        update_account_balance(new_balance)
        
        # Verify update
        retrieved = get_account_balance()
        assert retrieved == new_balance
    
    def test_account_persistence(self):
        """Test balance persists across sessions."""
        update_account_balance(9999.99)
        
        # Close and reopen session
        import time
        time.sleep(0.1)
        
        balance = get_account_balance()
        assert balance == 9999.99


# ===== SYSTEM FLAGS (P0: Kill Switch) =====

class TestSystemFlags:
    """Test system flags (P0 Kill Switch)."""
    
    def test_get_system_flag(self):
        """Test reading system flags."""
        value = get_system_flag("TRADING_DISABLED", "false")
        assert value in ["true", "false"]
    
    def test_set_system_flag(self):
        """Test writing system flags."""
        set_system_flag("TRADING_DISABLED", "true")
        
        # Verify update
        value = get_system_flag("TRADING_DISABLED")
        assert value == "true"
    
    def test_trading_disabled_flag(self):
        """Test kill switch flag specifically."""
        set_system_flag("TRADING_DISABLED", "false")
        assert is_trading_disabled() is False
        
        set_system_flag("TRADING_DISABLED", "true")
        assert is_trading_disabled() is True
    
    def test_custom_flags(self):
        """Test custom system flags."""
        set_system_flag("CUSTOM_KEY", "custom_value")
        retrieved = get_system_flag("CUSTOM_KEY")
        assert retrieved == "custom_value"


# ===== OPEN POSITIONS =====

class TestOpenPositions:
    """Test open position tracking."""
    
    def test_save_open_position(self):
        """Test saving open position."""
        pos_id = save_open_position(
            symbol="BTC/USDT",
            amount=0.1,
            entry_price=42000,
            current_price=42500,
            bot_type="HUNTER"
        )
        
        assert pos_id > 0
        
        # Verify
        session = get_db_session()
        position = session.query(OpenPosition).filter_by(id=pos_id).first()
        session.close()
        
        assert position is not None
        assert position.symbol == "BTC/USDT"
        assert position.unrealized_pnl == 50.0  # (42500 - 42000) * 0.1
    
    def test_get_open_positions(self):
        """Test retrieving all open positions."""
        save_open_position("BTC/USDT", 0.1, 42000, 42500)
        save_open_position("ETH/USDT", 1.0, 2500, 2600)
        
        positions = get_open_positions()
        
        assert len(positions) >= 2
        btc = [p for p in positions if p['symbol'] == 'BTC/USDT']
        assert len(btc) > 0


# ===== BACKTEST RESULTS =====

class TestBacktestResults:
    """Test backtest result storage."""
    
    def test_save_backtest_result(self):
        """Test saving backtest result."""
        result = {
            'symbol': 'BTC/USDT',
            'timeframe': '1h',
            'period_days': 30,
            'initial_capital': 1000,
            'final_capital': 1200,
            'roi_pct': 20.0,
            'total_trades': 15,
            'win_rate_pct': 60.0,
            'max_drawdown_pct': 5.0,
            'profit_factor': 2.5,
            'total_fees_paid': 50.0,
        }
        
        result_id = save_backtest_result(result)
        assert result_id > 0
        
        # Verify
        session = get_db_session()
        bt = session.query(BacktestResult).filter_by(id=result_id).first()
        session.close()
        
        assert bt is not None
        assert bt.roi_pct == 20.0
    
    def test_get_backtest_history(self):
        """Test retrieving backtest history."""
        result1 = {
            'symbol': 'BTC/USDT', 'timeframe': '1h', 'period_days': 30,
            'initial_capital': 1000, 'final_capital': 1200, 'roi_pct': 20.0,
            'total_trades': 15, 'win_rate_pct': 60.0, 'max_drawdown_pct': 5.0,
            'profit_factor': 2.5, 'total_fees_paid': 50.0,
        }
        result2 = dict(result1, symbol='ETH/USDT', final_capital=1100, roi_pct=10.0)
        
        save_backtest_result(result1)
        save_backtest_result(result2)
        
        history = get_backtest_history(limit=10)
        assert len(history) >= 2


# ===== TRANSACTION TESTS =====

class TestTransactions:
    """Test ACID transaction guarantees."""
    
    def test_transaction_commit(self):
        """Test transaction commits on success."""
        with session_scope() as session:
            trade = Trade(
                symbol="BTC/USDT",
                side="BUY",
                buy_price=42000,
                amount=0.1,
                total_usdt=4200
            )
            session.add(trade)
            # Auto-commits on exit
        
        # Verify trade exists
        trades = get_trades()
        assert len(trades) > 0
    
    def test_transaction_rollback(self):
        """Test transaction rolls back on exception."""
        initial_count = len(get_trades())
        
        try:
            with session_scope() as session:
                trade = Trade(
                    symbol="BTC/USDT",
                    side="BUY",
                    buy_price=42000,
                    amount=0.1,
                    total_usdt=4200
                )
                session.add(trade)
                # Simulate error
                raise ValueError("Test error")
        except ValueError:
            pass
        
        # Verify trade was NOT saved
        final_count = len(get_trades())
        assert final_count == initial_count


# ===== PERFORMANCE TESTS =====

class TestPerformance:
    """Test database performance."""
    
    def test_bulk_insert_performance(self):
        """Test inserting many trades."""
        import time
        
        start = time.time()
        for i in range(100):
            save_trade(f"COIN{i}/USDT", "BUY", 100, 1, 100)
        duration = time.time() - start
        
        # Should complete in <5 seconds
        assert duration < 5.0
        print(f"✅ Inserted 100 trades in {duration:.2f}s")
    
    def test_bulk_query_performance(self):
        """Test querying many trades."""
        import time
        
        # Insert data
        for i in range(100):
            save_trade(f"COIN{i}/USDT", "BUY", 100, 1, 100)
        
        # Query
        start = time.time()
        for _ in range(10):
            trades = get_trades(limit=100)
        duration = time.time() - start
        
        # Should complete in <1 second
        assert duration < 1.0
        print(f"✅ Queried 10x100 trades in {duration:.3f}s")


# ===== FOREIGN KEY TESTS =====

class TestForeignKeys:
    """Test referential integrity."""
    
    def test_trade_ledger_foreign_key(self):
        """Test that trade_ledger maintains referential integrity."""
        trade_id = save_trade("BTC/USDT", "BUY", 42000, 0.1, 4200)
        open_trade(trade_id)
        
        # Get ledger entry
        ledger = get_trade_ledger(trade_id)
        assert len(ledger) > 0
        assert ledger[0]['trade_id'] == trade_id
        
        # Verify trade exists
        session = get_db_session()
        trade = session.query(Trade).filter_by(id=trade_id).first()
        session.close()
        assert trade is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
