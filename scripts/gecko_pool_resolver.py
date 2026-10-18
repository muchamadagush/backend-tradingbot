# scripts/gecko_pool_resolver.py

import asyncio
import aiohttp
import sys
from typing import Optional, Dict

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
NETWORK = "solana"


# =============================
# RESOLVE POOL BY TOKEN ADDRESS
# =============================
async def resolve_pool_by_token(token_address: str) -> Optional[Dict]:
    """
    Cari pool paling liquid untuk token Solana
    Return:
      {
        pool_id,
        dex,
        base_token,
        quote_token,
        volume_24h,
        liquidity_usd
      }
    """
    url = (
        f"{GECKO_BASE}/networks/{NETWORK}/tokens/"
        f"{token_address}/pools"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=15) as resp:
            if resp.status != 200:
                print(f"❌ HTTP ERROR {resp.status}")
                try:
                    print(await resp.text())
                except Exception:
                    pass
                return None

            data = await resp.json()

    if "data" not in data or not data["data"]:
        print("❌ No pool data found")
        return None

    best_pool = None
    best_score = 0

    for pool in data["data"]:
        attrs = pool.get("attributes", {})
        if not attrs:
            continue

        liquidity = float(attrs.get("reserve_in_usd") or 0)
        volume = float(attrs.get("volume_usd", {}).get("h24") or 0)

        # score sederhana: liquiditas + volume
        score = liquidity * 0.7 + volume * 0.3

        if score > best_score:
            best_score = score
            best_pool = {
                "pool_id": pool["id"],
                "dex": attrs.get("dex_id"),
                "base_token": attrs.get("base_token", {}).get("symbol"),
                "quote_token": attrs.get("quote_token", {}).get("symbol"),
                "liquidity_usd": liquidity,
                "volume_24h": volume
            }

    return best_pool


# =============================
# MAIN
# =============================
async def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/gecko_pool_resolver.py <TOKEN_ADDRESS>")
        sys.exit(1)

    token_address = sys.argv[1]

    print("🔍 Resolving pool for token:")
    print(f"• Address : {token_address}")

    pool = await resolve_pool_by_token(token_address)

    if not pool:
        print("❌ No valid pool found")
        sys.exit(1)

    print("\n✅ BEST POOL FOUND")
    print(f"• Pool ID      : {pool['pool_id']}")
    print(f"• DEX          : {pool['dex']}")
    print(f"• Pair         : {pool['base_token']} / {pool['quote_token']}")
    print(f"• Liquidity $  : {pool['liquidity_usd']:.2f}")
    print(f"• Volume 24h $ : {pool['volume_24h']:.2f}")

    print("\n➡️  Use this pool_id for load_candles.py")


if __name__ == "__main__":
    asyncio.run(main())
