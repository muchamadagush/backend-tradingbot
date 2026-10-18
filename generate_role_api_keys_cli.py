"""
CLI script to generate valid API keys for admin, trader, and readonly roles.
Usage:
    python generate_role_api_keys_cli.py [role] [--secret <hex_secret>]
If --secret is provided, use it as the API key for the role (for .env sync).
"""
import secrets
import sys

roles = ["admin", "trader", "readonly"]

def usage():
    print("Usage: python generate_role_api_keys_cli.py [role] [--secret <hex_secret>]")
    print("If no role is given, generates for all roles with random secrets.")
    print("If --secret is given, uses it as the API key for the role.")

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        for role in roles:
            api_key = secrets.token_hex(32)
            print(f"{role} API key: {api_key}")
    else:
        role = args[0]
        if role not in roles:
            usage()
            sys.exit(1)
        if len(args) == 3 and args[1] == "--secret":
            api_key = args[2]
        else:
            api_key = secrets.token_hex(32)
        print(f"{role} API key: {api_key}")
