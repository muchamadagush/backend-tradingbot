"""
Quick script to create the crypto_bot database
"""
import psycopg2
import os
import sys

def create_database():
    try:
        db_host = os.getenv("POSTGRES_HOST")
        db_port = os.getenv("POSTGRES_PORT")
        db_user = os.getenv("POSTGRES_USER")
        db_pass = os.getenv("POSTGRES_PASS")

        if not db_pass:
            print("ERROR: POSTGRES_PASSWORD environment variable not set.")
            sys.exit(1)
        if not db_user:
            print("ERROR: POSTGRES_USER environment variable not set.")
            sys.exit(1)
        if not db_port:
            print("ERROR: POSTGRES_PORT environment variable not set.")
            sys.exit(1)
        if not db_host:
            print("ERROR: POSTGRES_HOST environment variable not set.")
            sys.exit(1)

        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_pass
        )
        conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Check if database exists
        cursor.execute("SELECT 1 FROM pg_database WHERE datname='crypto_bot'")
        exists = cursor.fetchone()
        
        if exists:
            print("✅ Database 'crypto_bot' already exists")
        else:
            # Create database
            cursor.execute("CREATE DATABASE crypto_bot")
            print("✅ Database 'crypto_bot' created successfully")
        
        cursor.close()
        conn.close()
        
        print("\nNow initializing tables...")
        from database_pg import init_pg_db
        init_pg_db()
    except Exception as e:
        print(f"❌ Error: {e}")
        print("\nMake sure PostgreSQL is running and credentials are correct:")
        print(f"  Host: {db_host}")
        print(f"  Port: {db_port}")
        print(f"  User: {db_user}")
        print(f"  Pass: {db_pass}")