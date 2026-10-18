# File: backend/solana_safety.py
import asyncio
import aiohttp
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
import sys_logger
import os

# Rugpull guard thresholds
MAX_HOLDER_PCT = 30.0  # Any single non-LP holder above this is rejected

class SafetyChecker:
    """
    Hybrid Safety Checker: RugCheck API + Local RPC Fallback
    
    Logika:
    1. Coba RugCheck API terlebih dahulu
    2. Jika error/404, fallback ke pengecekan local via Solana RPC
    3. Cek mintAuthority dan freezeAuthority harus None (renounced)
    """
    
    def __init__(self):
        # Gunakan RPC endpoint dari env atau default
        self.rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        self.client = AsyncClient(self.rpc_url)
    
    async def check_token(self, token_address: str):
        """
        Main entry point untuk safety check.
        Returns: (is_safe: bool, reason: str, source: str)
        source = "RugCheck API" atau "Local RPC"
        """
        # Langkah 1: Coba RugCheck API
        api_result = await self._check_rugcheck_api(token_address)
        if api_result is not None:
            is_safe, reason = api_result
            sys_logger.add_log(f"🔍 Safety Check (RugCheck API): {reason}")
            return is_safe, reason, "RugCheck API"
        
        # Langkah 2: Fallback ke Local RPC Check
        sys_logger.add_log(f"⚠️ RugCheck API unavailable, using Local RPC fallback...")
        local_result = await self._check_local_rpc(token_address)
        is_safe, reason = local_result
        sys_logger.add_log(f"🔍 Safety Check (Local RPC): {reason}")
        return is_safe, reason, "Local RPC"
    
    async def _check_rugcheck_api(self, token_address: str):
        """
        Check RugCheck API. Returns (is_safe, reason) or None if API unavailable.
        """
        url = f"https://api.rugcheck.xyz/v1/tokens/{token_address}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=8) as resp:
                    if resp.status == 404:
                        # Token belum diindex, return None untuk trigger fallback
                        return None
                    if resp.status != 200:
                        # Error lain, trigger fallback
                        return None
                    
                    data = await resp.json()
                    
                    # Parse RugCheck response
                    mint_auth_enabled = data.get("mintAuthorityEnabled", True)
                    freeze_auth_enabled = data.get("freezeAuthorityEnabled", True)
                    holders = data.get("topHolders", [])
                    
                    # Holder concentration check
                    risky_holder = None
                    for holder in holders:
                        if holder.get("isLp"):
                            continue
                        pct = float(holder.get("percentage", 0) * 100)
                        if pct > MAX_HOLDER_PCT:
                            risky_holder = pct
                            break
                    
                    if mint_auth_enabled:
                        return False, "Mint authority active"
                    if freeze_auth_enabled:
                        return False, "Freeze authority active"
                    if risky_holder:
                        return False, f"Top holder {risky_holder:.1f}% > {MAX_HOLDER_PCT}%"
                    
                    return True, "RugCheck passed"
        
        except asyncio.TimeoutError:
            return None  # Timeout, trigger fallback
        except Exception as e:
            return None  # Error, trigger fallback
    
    async def _check_local_rpc(self, token_address: str):
        """
        Fallback: Check token authorities directly via Solana RPC.
        Returns (is_safe, reason).
        """
        try:
            pubkey = Pubkey.from_string(token_address)
            
            # Fetch account info
            response = await self.client.get_account_info_json_parsed(pubkey)
            
            if not response.value:
                return False, "Token account not found"
            
            account_data = response.value.data
            
            # Parse mint authorities from parsed data
            if hasattr(account_data, 'parsed'):
                parsed = account_data.parsed
                if isinstance(parsed, dict):
                    info = parsed.get('info', {})
                    mint_authority = info.get('mintAuthority')
                    freeze_authority = info.get('freezeAuthority')
                    
                    # Check authorities are renounced (None)
                    if mint_authority is not None:
                        return False, "Mint authority NOT renounced"
                    if freeze_authority is not None:
                        return False, "Freeze authority NOT renounced"
                    
                    return True, "Authorities renounced (Local check)"
            
            # If we can't parse, be conservative
            return False, "Unable to parse token data"
        
        except Exception as e:
            # If local check fails, reject token
            return False, f"Local RPC error: {str(e)}"
    
    async def close(self):
        """Close RPC connection."""
        await self.client.close()

# Global instance
safety_checker = SafetyChecker()
