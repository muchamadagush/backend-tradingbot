#!/usr/bin/env python3
"""
CLI script to manage API keys for TradingBot

Usage:
    python seed_user.py create-admin           # Create first admin user
    python seed_user.py create <user_id> <role>  # Create user with role
    python seed_user.py list                   # List all users
    python seed_user.py revoke <user_id>       # Revoke user's API keys
"""

import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from auth import api_key_store, UserRole


def create_admin():
    """Create the first admin user."""
    print("\n🔐 Creating Admin API Key")
    print("=" * 40)
    
    # Check if admin already exists
    users = api_key_store.list_users()
    admins = [u for u in users if u["role"] == "admin"]
    
    if admins:
        print(f"⚠️  Admin user(s) already exist: {[a['user_id'] for a in admins]}")
        response = input("Create another admin? (y/N): ").strip().lower()
        if response != 'y':
            print("Cancelled.")
            return
    
    user_id = input("Enter admin username (default: admin): ").strip() or "admin"
    api_key = api_key_store.generate_key(user_id, UserRole.ADMIN)
    
    print("\n✅ Admin user created successfully!")
    print(f"   User ID: {user_id}")
    print(f"   Role: admin")
    print(f"\n   🔑 API Key: {api_key}")
    print("\n⚠️  IMPORTANT: Save this API key securely!")
    print("   It cannot be retrieved again.")
    print("\n📝 Add to your .env file:")
    print(f"   BOT_API_KEY={api_key}")
    print(f"   AUTH_MODE=api_key")


def create_user(user_id: str, role_str: str):
    """Create a new user with specified role."""
    try:
        role = UserRole(role_str.lower())
    except ValueError:
        print(f"❌ Invalid role: {role_str}")
        print(f"   Valid roles: {[r.value for r in UserRole]}")
        return
    
    api_key = api_key_store.generate_key(user_id, role)
    
    print(f"\n✅ User created successfully!")
    print(f"   User ID: {user_id}")
    print(f"   Role: {role.value}")
    print(f"\n   🔑 API Key: {api_key}")
    print("\n⚠️  Save this API key securely - it cannot be retrieved again.")


def list_users():
    """List all registered users."""
    users = api_key_store.list_users()
    
    if not users:
        print("\n📋 No users registered yet.")
        print("   Run: python seed_user.py create-admin")
        return
    
    print(f"\n📋 Registered Users ({len(users)})")
    print("=" * 50)
    print(f"{'User ID':<20} {'Role':<10} {'Key Prefix'}")
    print("-" * 50)
    for u in users:
        print(f"{u['user_id']:<20} {u['role']:<10} {u['key_prefix']}")


def revoke_user(user_id: str):
    """Revoke all API keys for a user."""
    count = api_key_store.revoke_by_user_id(user_id)
    
    if count == 0:
        print(f"❌ User '{user_id}' not found")
        return
    
    print(f"✅ Revoked {count} API key(s) for user '{user_id}'")


def show_help():
    """Show help message."""
    print(__doc__)
    print("\nRoles:")
    for role in UserRole:
        print(f"  - {role.value}")


def main():
    if len(sys.argv) < 2:
        show_help()
        return
    
    command = sys.argv[1].lower()
    
    if command == "create-admin":
        create_admin()
    elif command == "create":
        if len(sys.argv) < 4:
            print("Usage: python seed_user.py create <user_id> <role>")
            return
        create_user(sys.argv[2], sys.argv[3])
    elif command == "list":
        list_users()
    elif command == "revoke":
        if len(sys.argv) < 3:
            print("Usage: python seed_user.py revoke <user_id>")
            return
        revoke_user(sys.argv[2])
    elif command in ("help", "-h", "--help"):
        show_help()
    else:
        print(f"Unknown command: {command}")
        show_help()


if __name__ == "__main__":
    main()
