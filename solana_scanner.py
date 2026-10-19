import asyncio
import aiohttp
import time
import sys_logger

from utils.gecko_pool_resolver import resolve_best_pool


# =========================
# API CONFIG (MULTI-SOURCE DISCOVERY v3)
# =========================
PROFILES_API = "https://api.dexscreener.com/token-profiles/latest/v1"
BOOSTS_API = "https://api.dexscreener.com/token-boosts/latest/v1"
TOKENS_API = "https://api.dexscreener.com/latest/dex/tokens/"
WSOL_ADDRESS = "So11111111111111111111111111111111111111112"


class SolanaScanner:
    """
    High-Win-Rate Solana Token Scanner v3:
    - Multi-source discovery: combines DexScreener Latest Profiles + Latest Boosted Tokens
    - Dual Timeframe Order-Flow Filter: validates both 5-minute (m5) AND 1-hour (h1) buy/sell pressure
    - Anti-Distribution & Liquidity Depth Filter: prioritizes pools >= $20,000 USD liquidity
    """

    async def _fetch_json(self, session: aiohttp.ClientSession, url: str, headers: dict):
        try:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception:
            pass
        return []

    async def find_gem(self, ignore_list=None, mode="SNIPER"):
        ignore_list = ignore_list or []
        mode = (mode or "SNIPER").upper()

        sys_logger.add_log(f"📡 [SCANNER v3] Scanning Multi-Source Solana Gems ({mode})...")

        headers = {"User-Agent": "Mozilla/5.0"}

        BLACKLIST_SYMBOLS = {
            "USDC", "USDT", "USDH", "UXD", "PAI",
            "SOL", "WSOL", "USD1", "USDS",
            "MSOL", "JITOSOL", "BSOL", "JUP", "RAY"
        }

        try:
            async with aiohttp.ClientSession() as session:

                # =====================================================
                # STEP 1 — MULTI-SOURCE TOKEN DISCOVERY (PROFILES + BOOSTS)
                # =====================================================
                profiles_res, boosts_res = await asyncio.gather(
                    self._fetch_json(session, PROFILES_API, headers),
                    self._fetch_json(session, BOOSTS_API, headers),
                )

                raw_items = []
                if isinstance(profiles_res, list):
                    raw_items.extend(profiles_res)
                if isinstance(boosts_res, list):
                    raw_items.extend(boosts_res)

                if not raw_items:
                    sys_logger.add_log("❌ Scanner Discovery API returned empty list")
                    return None

                # =====================================================
                # STEP 2 — DEDUPLICATE SOLANA TOKEN ADDRESSES
                # =====================================================
                solana_addresses = []
                for item in raw_items:
                    if isinstance(item, dict) and item.get("chainId") == "solana":
                        addr = item.get("tokenAddress")
                        if addr and addr != WSOL_ADDRESS and addr not in solana_addresses:
                            solana_addresses.append(addr)

                solana_addresses = solana_addresses[:30]

                if not solana_addresses:
                    sys_logger.add_log("⚠️ No Solana tokens found")
                    return None

                # =====================================================
                # STEP 3 — FETCH PAIR METRICS & ORDER FLOW
                # =====================================================
                tokens_url = TOKENS_API + ",".join(solana_addresses)
                async with session.get(tokens_url, headers=headers, timeout=12) as resp:
                    if resp.status != 200:
                        sys_logger.add_log(f"❌ Tokens API Error: {resp.status}")
                        return None

                    data = await resp.json()
                    all_pairs = data.get("pairs", []) or []

                # =====================================================
                # STEP 4 — DUAL ORDER-FLOW (m5 + h1) & QUALITY SCORING
                # =====================================================
                candidates = []

                skipped_quote = 0
                skipped_age = 0
                skipped_fdv = 0
                skipped_stable = 0
                skipped_ignore = 0
                skipped_liquidity = 0
                skipped_volume = 0
                skipped_trend = 0
                skipped_orderflow = 0

                now_ms = int(time.time() * 1000)

                for p in all_pairs:
                    if p.get("chainId") != "solana":
                        continue

                    base = p.get("baseToken", {}) or {}
                    quote = p.get("quoteToken", {}) or {}

                    if quote.get("address") != WSOL_ADDRESS:
                        skipped_quote += 1
                        continue

                    symbol = (base.get("symbol") or "").upper()
                    address = base.get("address")

                    if not address:
                        continue

                    if symbol in BLACKLIST_SYMBOLS:
                        skipped_stable += 1
                        continue

                    if symbol in ignore_list or address in ignore_list:
                        skipped_ignore += 1
                        continue

                    pair_created_at = p.get("pairCreatedAt") or 0
                    age_ms = now_ms - pair_created_at if pair_created_at else 0
                    age_minutes = age_ms / 60_000
                    age_hours = age_minutes / 60.0

                    liquidity = float(p.get("liquidity", {}).get("usd", 0) or 0)
                    volume_24h = float(p.get("volume", {}).get("h24", 0) or 0)
                    volume_h1 = float(p.get("volume", {}).get("h1", 0) or 0)
                    volume_m5 = float(p.get("volume", {}).get("m5", 0) or 0)
                    fdv = float(p.get("fdv") or 0)
                    price_change_h1 = float(p.get("priceChange", {}).get("h1", 0) or 0)
                    price_change_m5 = float(p.get("priceChange", {}).get("m5", 0) or 0)

                    # Extract 1h and 5m buy/sell transaction counts for Dual Order Flow
                    txns = p.get("txns", {}) or {}
                    txns_h1 = txns.get("h1", {}) or {}
                    txns_m5 = txns.get("m5", {}) or {}

                    buys_h1 = int(txns_h1.get("buys", 0) or 0)
                    sells_h1 = int(txns_h1.get("sells", 0) or 0)
                    total_tx_h1 = buys_h1 + sells_h1
                    buy_sell_ratio_h1 = (buys_h1 / max(sells_h1, 1)) if total_tx_h1 > 0 else 1.0

                    buys_m5 = int(txns_m5.get("buys", 0) or 0)
                    sells_m5 = int(txns_m5.get("sells", 0) or 0)
                    total_tx_m5 = buys_m5 + sells_m5
                    buy_sell_ratio_m5 = (buys_m5 / max(sells_m5, 1)) if total_tx_m5 > 0 else 1.0

                    # =============================
                    # MODE LOGIC (HIGH-WIN-RATE v3)
                    # =============================
                    if mode == "SNIPER":
                        if age_minutes > 15:
                            skipped_age += 1
                            continue
                        if liquidity < 6000:
                            skipped_liquidity += 1
                            continue
                        if fdv > 500_000 or (fdv > 0 and liquidity / fdv < 0.04):
                            skipped_fdv += 1
                            continue
                        if buy_sell_ratio_m5 < 1.15 or buy_sell_ratio_h1 < 1.10:
                            skipped_orderflow += 1
                            continue
                    else:
                        # TRADER MODE v3: High-Win-Rate Institutional Quality Filter
                        if age_minutes < 45:
                            skipped_age += 1
                            continue
                        # Require minimum $12,000 liquidity (and strongly boost pools >= $20,000)
                        if liquidity < 12_000:
                            skipped_liquidity += 1
                            continue
                        # Require active 1h AND 5m volume so we never enter illiquid dead pools
                        if volume_h1 < 10_000 or volume_m5 < 500 or (volume_h1 / max(liquidity, 1.0)) < 0.30:
                            skipped_volume += 1
                            continue
                        # Healthy trend window: h1 up +3% to +140%, m5 not dumping (< -1.5%) and not parabolic (> +22%)
                        if price_change_h1 < 3.0 or price_change_h1 > 140.0 or price_change_m5 < -1.5 or price_change_m5 > 22.0:
                            skipped_trend += 1
                            continue
                        # Dual Order-Flow Pressure: both 1h and 5m must show net buyer accumulation
                        if total_tx_h1 < 20 or buy_sell_ratio_h1 < 1.15:
                            skipped_orderflow += 1
                            continue
                        if total_tx_m5 < 4 or buy_sell_ratio_m5 < 1.20:
                            skipped_orderflow += 1
                            continue
                        # Liquidity-to-FDV sanity check (prevent spoofed FDV honeypots)
                        if fdv > 0 and (liquidity / fdv) < 0.04:
                            skipped_fdv += 1
                            continue

                    pair_address = p.get("pairAddress")

                    # Composite Alpha Score v3:
                    # Rewards deep liquidity (>= $20k), strong m5 + h1 buyer dominance, and sustainable turnover
                    turnover_score = min(volume_h1 / max(liquidity, 1.0), 3.5)
                    orderflow_score = (min(buy_sell_ratio_m5, 3.0) * 0.6) + (min(buy_sell_ratio_h1, 2.5) * 0.4)
                    liquidity_bonus = 1.30 if liquidity >= 20_000 else 1.0
                    momentum_factor = 1.0 + max(0.0, min(price_change_h1, 50.0)) / 100.0

                    alpha_score = turnover_score * orderflow_score * liquidity_bonus * momentum_factor

                    candidates.append({
                        "symbol": symbol,
                        "address": address,
                        "pair_address": pair_address,
                        "liquidity": liquidity,
                        "volume_h1": volume_h1,
                        "volume_m5": volume_m5,
                        "volume": volume_24h,
                        "fdv": fdv,
                        "price_change_h1": price_change_h1,
                        "price_change_m5": price_change_m5,
                        "buy_sell_ratio": round(buy_sell_ratio_h1, 2),
                        "buy_sell_ratio_m5": round(buy_sell_ratio_m5, 2),
                        "alpha_score": round(alpha_score, 3),
                        "age_minutes": age_minutes,
                        "age_hours": age_hours,
                        "url": p.get("url")
                    })

                # =====================================================
                # STEP 5 — SORT BY ALPHA SCORE & PICK BEST
                # =====================================================
                if candidates:
                    # Prioritize candidates with >= $20k liquidity first, then highest alpha_score
                    candidates.sort(
                        key=lambda x: (1 if x["liquidity"] >= 20_000 else 0, x["alpha_score"], x["volume_h1"]),
                        reverse=True
                    )

                    for best in candidates[:5]:
                        if not best.get("pair_address"):
                            best["pair_address"] = await resolve_best_pool(best["address"], session=session)

                        if best.get("pair_address"):
                            age_label = f"{best['age_minutes']:.1f}m" if best["age_minutes"] < 60 else f"{best['age_hours']:.2f}h"
                            trend_h1 = best.get("price_change_h1", 0)
                            trend_m5 = best.get("price_change_m5", 0)

                            sys_logger.add_log(
                                f"💎 GEM ({mode}) {best['symbol']} | Age {age_label} | "
                                f"Liq ${best['liquidity']:,.0f} | Vol1h ${best['volume_h1']:,.0f} | "
                                f"B/S(m5/h1) {best['buy_sell_ratio_m5']}x/{best['buy_sell_ratio']}x | "
                                f"Trend(m5/h1) {trend_m5:+.1f}%/{trend_h1:+.1f}% | Score {best['alpha_score']}"
                            )
                            return best

                sys_logger.add_log(
                    f"⚠️ No Result ({mode}) | "
                    f"Quote={skipped_quote} Age={skipped_age} FDV={skipped_fdv} "
                    f"Stable={skipped_stable} Liq={skipped_liquidity} "
                    f"Vol={skipped_volume} Trend={skipped_trend} "
                    f"OrderFlow={skipped_orderflow} Ignored={skipped_ignore}"
                )
                return None

        except Exception as e:
            sys_logger.add_log(f"❌ Scanner Error: {e}")
            return None


scanner = SolanaScanner()