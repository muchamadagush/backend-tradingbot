"""
CLI script to initialize PostgreSQL tables for TradingBot
Usage:
    python init_pg_db_cli.py
"""
from database_pg import init_pg_db

if __name__ == "__main__":
    init_pg_db()
    print("✅ Database tables created and initialized.")
