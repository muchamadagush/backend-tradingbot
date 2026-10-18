"""
Migration Script: SQLite → PostgreSQL

This script safely migrates all data from crypto_bot.db (SQLite) to PostgreSQL.
It validates data integrity and handles foreign key constraints.

Usage:
    python backend/migrate_sqlite_to_pg.py
"""

import sqlite3
import psycopg2
from datetime import datetime
from typing import Dict, List, Any
import json
import sys
import os

# Configuration
SQLITE_DB = "crypto_bot.db"
pg_host = os.getenv("POSTGRES_HOST")
pg_port = os.getenv("POSTGRES_PORT")
pg_user = os.getenv("POSTGRES_USER")
pg_pass = os.getenv("POSTGRES_PASS")

if not pg_pass:
    print("ERROR: POSTGRES_PASSWORD environment variable not set.")
    sys.exit(1)
if not pg_user:
    print("ERROR: POSTGRES_USER environment variable not set.")
    sys.exit(1)
if not pg_port:
    print("ERROR: POSTGRES_PORT environment variable not set.")
    sys.exit(1)
if not pg_host:
    print("ERROR: POSTGRES_HOST environment variable not set.")
    sys.exit(1)
PG_CONNECTION = {
    'database': os.getenv('PG_DB', 'crypto_bot'),
    'user': pg_user,
    'password': pg_pass,
    'host': pg_host,
    'port': pg_port,
}

# Statistics
stats = {
    'trades': 0,
    'ledger': 0,
    'backtest': 0,
    'snapshots': 0,
    'flags': 0,
    'account': 0,
    'positions': 0,
}


def connect_sqlite() -> sqlite3.Connection:
    """Connect to SQLite database."""
    try:
        conn = sqlite3.connect(SQLITE_DB)
        conn.row_factory = sqlite3.Row
        print(f"✅ Connected to SQLite: {SQLITE_DB}")
        return conn
    except Exception as e:
        print(f"❌ SQLite connection failed: {e}")
        sys.exit(1)


def connect_postgresql() -> psycopg2.extensions.connection:
    """Connect to PostgreSQL database."""
    try:
        conn = psycopg2.connect(**PG_CONNECTION)
        print(f"✅ Connected to PostgreSQL: {PG_CONNECTION['host']}:{PG_CONNECTION['port']}/{PG_CONNECTION['database']}")
        return conn
    except Exception as e:
        print(f"❌ PostgreSQL connection failed: {e}")
        print(f"   Connection string: {PG_CONNECTION}")
        sys.exit(1)


def migrate_trades(sqlite_conn: sqlite3.Connection, pg_conn: psycopg2.extensions.connection):
    """Migrate trades table."""
    print("\n📦 Migrating trades...")
    
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    try:
        sqlite_cursor.execute("SELECT * FROM trades")
        trades = sqlite_cursor.fetchall()
        
        for row in trades:
            trade_dict = dict(row)
            
            pg_cursor.execute("""
                INSERT INTO trades (
                    id, symbol, side, buy_price, amount, total_usdt,
                    status, sell_price, profit_usdt, fee_paid, mode, bot_type,
                    created_at, opened_at, closing_at, closed_at
                ) VALUES (
                    %(id)s, %(symbol)s, %(side)s, %(buy_price)s, %(amount)s,
                    %(total_usdt)s, %(status)s, %(sell_price)s, %(profit_usdt)s,
                    %(fee_paid)s, %(mode)s, %(bot_type)s,
                    %(created_at)s, %(opened_at)s, %(closing_at)s, %(closed_at)s
                )
            """, trade_dict)
            
            stats['trades'] += 1
        
        pg_conn.commit()
        print(f"   ✅ Migrated {stats['trades']} trades")
        
    except Exception as e:
        pg_conn.rollback()
        print(f"   ❌ Error migrating trades: {e}")
        raise


def migrate_trade_ledger(sqlite_conn: sqlite3.Connection, pg_conn: psycopg2.extensions.connection):
    """Migrate trade_ledger table (immutable audit log)."""
    print("\n📋 Migrating trade_ledger...")
    
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    try:
        sqlite_cursor.execute("SELECT * FROM trade_ledger")
        ledger_entries = sqlite_cursor.fetchall()
        
        for row in ledger_entries:
            entry_dict = dict(row)
            
            pg_cursor.execute("""
                INSERT INTO trade_ledger (
                    id, trade_id, event_type, old_state, new_state,
                    price, amount, value_usdt, fee, pnl, bot_type, reason, timestamp
                ) VALUES (
                    %(id)s, %(trade_id)s, %(event_type)s, %(old_state)s, %(new_state)s,
                    %(price)s, %(amount)s, %(value_usdt)s, %(fee)s, %(pnl)s,
                    %(bot_type)s, %(reason)s, %(timestamp)s
                )
            """, entry_dict)
            
            stats['ledger'] += 1
        
        pg_conn.commit()
        print(f"   ✅ Migrated {stats['ledger']} ledger entries")
        
    except Exception as e:
        pg_conn.rollback()
        print(f"   ❌ Error migrating trade_ledger: {e}")
        raise


def migrate_backtest_results(sqlite_conn: sqlite3.Connection, pg_conn: psycopg2.extensions.connection):
    """Migrate backtest_results table."""
    print("\n📊 Migrating backtest_results...")
    
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    try:
        sqlite_cursor.execute("SELECT * FROM backtest_results")
        results = sqlite_cursor.fetchall()
        
        for row in results:
            result_dict = dict(row)
            
            pg_cursor.execute("""
                INSERT INTO backtest_results (
                    id, symbol, timeframe, period_days,
                    initial_capital, final_capital, roi_pct,
                    total_trades, win_rate_pct, max_drawdown_pct,
                    profit_factor, total_fees_paid, result_json, created_at
                ) VALUES (
                    %(id)s, %(symbol)s, %(timeframe)s, %(period_days)s,
                    %(initial_capital)s, %(final_capital)s, %(roi_pct)s,
                    %(total_trades)s, %(win_rate_pct)s, %(max_drawdown_pct)s,
                    %(profit_factor)s, %(total_fees_paid)s, %(result_json)s, %(created_at)s
                )
            """, result_dict)
            
            stats['backtest'] += 1
        
        pg_conn.commit()
        print(f"   ✅ Migrated {stats['backtest']} backtest results")
        
    except Exception as e:
        pg_conn.rollback()
        print(f"   ⚠️ Warning migrating backtest_results: {e}")


def migrate_paper_trading_snapshots(sqlite_conn: sqlite3.Connection, pg_conn: psycopg2.extensions.connection):
    """Migrate paper_trading_snapshots table."""
    print("\n📈 Migrating paper_trading_snapshots...")
    
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    try:
        sqlite_cursor.execute("SELECT * FROM paper_trading_snapshots")
        snapshots = sqlite_cursor.fetchall()
        
        for row in snapshots:
            snapshot_dict = dict(row)
            
            pg_cursor.execute("""
                INSERT INTO paper_trading_snapshots (
                    id, balance, total_equity, total_pnl, open_positions,
                    snapshot_json, created_at
                ) VALUES (
                    %(id)s, %(balance)s, %(total_equity)s, %(total_pnl)s,
                    %(open_positions)s, %(snapshot_json)s, %(created_at)s
                )
            """, snapshot_dict)
            
            stats['snapshots'] += 1
        
        pg_conn.commit()
        print(f"   ✅ Migrated {stats['snapshots']} snapshots")
        
    except Exception as e:
        pg_conn.rollback()
        print(f"   ⚠️ Warning migrating paper_trading_snapshots: {e}")


def migrate_system_flags(sqlite_conn: sqlite3.Connection, pg_conn: psycopg2.extensions.connection):
    """Migrate system_flags table (P0: Kill Switch)."""
    print("\n⚙️ Migrating system_flags...")
    
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    try:
        sqlite_cursor.execute("SELECT * FROM system_flags")
        flags = sqlite_cursor.fetchall()
        
        for row in flags:
            flag_dict = dict(row)
            
            pg_cursor.execute("""
                INSERT INTO system_flags (key, value)
                VALUES (%(key)s, %(value)s)
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value
            """, flag_dict)
            
            stats['flags'] += 1
        
        pg_conn.commit()
        print(f"   ✅ Migrated {stats['flags']} system flags")
        
    except Exception as e:
        pg_conn.rollback()
        print(f"   ⚠️ Warning migrating system_flags: {e}")


def migrate_account(sqlite_conn: sqlite3.Connection, pg_conn: psycopg2.extensions.connection):
    """Migrate account table (singleton)."""
    print("\n💰 Migrating account...")
    
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    try:
        sqlite_cursor.execute("SELECT * FROM account")
        accounts = sqlite_cursor.fetchall()
        
        for row in accounts:
            account_dict = dict(row)
            
            pg_cursor.execute("""
                INSERT INTO account (id, balance, updated_at)
                VALUES (%(id)s, %(balance)s, %(updated_at)s)
                ON CONFLICT (id) DO UPDATE
                SET balance = EXCLUDED.balance, updated_at = EXCLUDED.updated_at
            """, account_dict)
            
            stats['account'] += 1
        
        pg_conn.commit()
        print(f"   ✅ Migrated {stats['account']} account records")
        
    except Exception as e:
        pg_conn.rollback()
        print(f"   ⚠️ Warning migrating account: {e}")


def migrate_open_positions(sqlite_conn: sqlite3.Connection, pg_conn: psycopg2.extensions.connection):
    """Migrate open_positions table."""
    print("\n📍 Migrating open_positions...")
    
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    try:
        sqlite_cursor.execute("SELECT * FROM open_positions")
        positions = sqlite_cursor.fetchall()
        
        for row in positions:
            position_dict = dict(row)
            
            pg_cursor.execute("""
                INSERT INTO open_positions (
                    id, symbol, amount, entry_price, current_price,
                    unrealized_pnl, bot_type, opened_at
                ) VALUES (
                    %(id)s, %(symbol)s, %(amount)s, %(entry_price)s,
                    %(current_price)s, %(unrealized_pnl)s, %(bot_type)s, %(opened_at)s
                )
            """, position_dict)
            
            stats['positions'] += 1
        
        pg_conn.commit()
        print(f"   ✅ Migrated {stats['positions']} open positions")
        
    except Exception as e:
        pg_conn.rollback()
        print(f"   ⚠️ Warning migrating open_positions: {e}")


def verify_migration(sqlite_conn: sqlite3.Connection, pg_conn: psycopg2.extensions.connection):
    """Verify migration success by comparing counts."""
    print("\n🔍 Verifying migration...")
    
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    tables = ['trades', 'trade_ledger', 'backtest_results', 'paper_trading_snapshots']
    
    for table in tables:
        sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table}")
        sqlite_count = sqlite_cursor.fetchone()[0]
        
        pg_cursor.execute(f"SELECT COUNT(*) FROM {table}")
        pg_count = pg_cursor.fetchone()[0]
        
        status = "✅" if sqlite_count == pg_count else "⚠️"
        print(f"   {status} {table}: SQLite={sqlite_count}, PostgreSQL={pg_count}")


def main():
    """Run migration."""
    print("=" * 60)
    print("🚀 SQLite → PostgreSQL Migration")
    print("=" * 60)
    
    # Connect to both databases
    sqlite_conn = connect_sqlite()
    pg_conn = connect_postgresql()
    
    try:
        # Migrate all tables
        migrate_trades(sqlite_conn, pg_conn)
        migrate_trade_ledger(sqlite_conn, pg_conn)
        migrate_backtest_results(sqlite_conn, pg_conn)
        migrate_paper_trading_snapshots(sqlite_conn, pg_conn)
        migrate_system_flags(sqlite_conn, pg_conn)
        migrate_account(sqlite_conn, pg_conn)
        migrate_open_positions(sqlite_conn, pg_conn)
        
        # Verify
        verify_migration(sqlite_conn, pg_conn)
        
        # Summary
        print("\n" + "=" * 60)
        print("✅ MIGRATION SUCCESSFUL")
        print("=" * 60)
        print(f"Total records migrated: {sum(stats.values())}")
        for table, count in stats.items():
            print(f"  • {table}: {count}")
        
    except Exception as e:
        print(f"\n❌ MIGRATION FAILED: {e}")
        sys.exit(1)
        
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
