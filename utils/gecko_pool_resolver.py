import aiohttp
from typing import Optional

GECKO_API = "https://api.geckoterminal.com/api/v2"

async def resolve_best_pool(token_address: str, session: Optional[aiohttp.ClientSession] = None) -> Optional[str]:
    """
    Resolve BEST GeckoTerminal pool_id for a Solana token asynchronously.
    Returns pool_id or None without blocking the event loop.
    """
    url = f"{GECKO_API}/networks/solana/tokens/{token_address}/pools"

    try:
        if session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
        else:
            async with aiohttp.ClientSession() as new_session:
                async with new_session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        return None
                    data = await r.json()

        pools = data.get("data", [])
        if not pools:
            return None

        # Sort by liquidity desc
        pools.sort(
            key=lambda p: float(
                p.get("attributes", {}).get("reserve_in_usd", 0) or 0
            ),
            reverse=True
        )

        best = pools[0]
        # GeckoTerminal pool id is 'solana_<pool_address>', attributes.address is '<pool_address>'
        pool_address = best.get("attributes", {}).get("address")
        if not pool_address:
            raw_id = best.get("id", "")
            pool_address = raw_id.replace("solana_", "") if raw_id else None
        return pool_address

    except Exception:
        return None
