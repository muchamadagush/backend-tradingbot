import aiohttp
import time
import sys_logger

from utils.gecko_pool_resolver import resolve_best_pool


# =========================
# API CONFIG
# =========================
PROFILES_API = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKENS_API = "https://api.dexscreener.com/latest/dex/tokens/"
WSOL_ADDRESS = "So11111111111111111111111111111111111111112"


class SolanaScanner:
    async def find_gem(self, ignore_list=None, mode="SNIPER"):
        ignore_list = ignore_list or []
        mode = (mode or "SNIPER").upper()

        sys_logger.add_log(f"📡 [SCANNER] Scanning Latest Profiles ({mode})...")

        headers = {"User-Agent": "Mozilla/5.0"}

        BLACKLIST_SYMBOLS = [
            "USDC", "USDT", "USDH", "UXD", "PAI",
            "SOL", "WSOL", "USD1", "USDS",
            "MSOL", "JITOSOL", "JUP", "RAY"
        ]

        try:
            async with aiohttp.ClientSession() as session:

                # =====================================================
                # STEP 1 — FETCH LATEST TOKEN PROFILES
                # =====================================================
                async with session.get(PROFILES_API, headers=headers) as resp:
                    if resp.status != 200:
                        sys_logger.add_log(f"❌ Profiles API Error: {resp.status}")
                        return None

                    profiles = await resp.json()
                    profiles = profiles if isinstance(profiles, list) else []

                # =====================================================
                # STEP 2 — FILTER SOLANA TOKENS
                # =====================================================
                solana_addresses = []
                for p in profiles:
                    if p.get("chainId") == "solana":
                        addr = p.get("tokenAddress")
                        if addr and addr != WSOL_ADDRESS and addr not in solana_addresses:
                            solana_addresses.append(addr)

                solana_addresses = solana_addresses[:30]

                if not solana_addresses:
                    sys_logger.add_log("⚠️ No Solana tokens found")
                    return None

                # =====================================================
                # STEP 3 — FETCH TOKEN DETAILS
                # =====================================================
                tokens_url = TOKENS_API + ",".join(solana_addresses)
                async with session.get(tokens_url, headers=headers) as resp:
                    if resp.status != 200:
                        sys_logger.add_log(f"❌ Tokens API Error: {resp.status}")
                        return None

                    data = await resp.json()
                    all_pairs = data.get("pairs", [])

                # =====================================================
                # STEP 4 — FILTERING & ORDER-FLOW QUALITY SCORING
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

                    base = p.get("baseToken", {})
                    quote = p.get("quoteToken", {})

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

                    if symbol in ignore_list:
                        skipped_ignore += 1
                        continue

                    pair_created_at = p.get("pairCreatedAt") or 0
                    age_ms = now_ms - pair_created_at if pair_created_at else 0
                    age_minutes = age_ms / 60_000
                    age_hours = age_minutes / 60

                    liquidity = float(p.get("liquidity", {}).get("usd", 0) or 0)
                    volume_24h = float(p.get("volume", {}).get("h24", 0) or 0)
                    volume_h1 = float(p.get("volume", {}).get("h1", 0) or 0)
                    fdv = float(p.get("fdv") or 0)
                    price_change_h1 = float(p.get("priceChange", {}).get("h1", 0) or 0)
                    price_change_m5 = float(p.get("priceChange", {}).get("m5", 0) or 0)

                    # Extract 1h buy/sell transaction counts for Order Flow pressure
                    txns_h1 = p.get("txns", {}).get("h1", {}) or {}
                    buys_h1 = int(txns_h1.get("buys", 0) or 0)
                    sells_h1 = int(txns_h1.get("sells", 0) or 0)
                    buy_sell_ratio = (buys_h1 / max(sells_h1, 1)) if (buys_h1 + sells_h1) > 0 else 1.0

                    # =============================
                    # MODE LOGIC (ENHANCED ANTI-RUG & ANTI-FOMO)
                    # =============================
                    if mode == "SNIPER":
                        if age_minutes > 15:
                            skipped_age += 1
                            continue
                        if liquidity < 4000:
                            skipped_liquidity += 1
                            continue
                        if fdv > 500_000 or (fdv > 0 and liquidity / fdv < 0.04):
                            skipped_fdv += 1
                            continue
                        if buy_sell_ratio < 1.10:
                            skipped_orderflow += 1
                            continue
                    else:
                        # TRADER MODE: Require healthy liquidity, active volume, positive order-flow, and non-exhausted trend
                        if age_minutes < 30:
                            skipped_age += 1
                            continue
                        if liquidity < 10_000:
                            skipped_liquidity += 1
                            continue
                        if volume_h1 < 8_000 or (volume_h1 / max(liquidity, 1.0)) < 0.25:
                            skipped_volume += 1
                            continue
                        # Avoid dead coins (<= 2% h1) AND avoid chasing extreme parabolic pump tops (> 180% h1 or dumping > -6% on m5)
                        if price_change_h1 <= 2.0 or price_change_h1 > 180.0 or price_change_m5 < -6.0:
                            skipped_trend += 1
                            continue
                        # Require net buy order-flow pressure
                        if (buys_h1 + sells_h1) >= 10 and buy_sell_ratio < 1.12:
                            skipped_orderflow += 1
                            continue
                        # Liquidity-to-FDV sanity check (prevent spoofed FDV honeypots)
                        if fdv > 0 and (liquidity / fdv) < 0.03:
                            skipped_fdv += 1
                            continue

                    pair_address = p.get("pairAddress")

                    # Composite Alpha Score: balances volume turnover, buy/sell order flow, and healthy momentum
                    turnover_score = min(volume_h1 / max(liquidity, 1.0), 4.0)
                    orderflow_score = min(buy_sell_ratio, 3.0)
                    alpha_score = turnover_score * orderflow_score * (1.0 + max(0.0, min(price_change_h1, 60.0)) / 100.0)

                    candidates.append({
                        "symbol": symbol,
                        "address": address,
                        "pair_address": pair_address,
                        "liquidity": liquidity,
                        "volume_h1": volume_h1,
                        "volume": volume_24h,
                        "fdv": fdv,
                        "price_change_h1": price_change_h1,
                        "price_change_m5": price_change_m5,
                        "buy_sell_ratio": round(buy_sell_ratio, 2),
                        "alpha_score": round(alpha_score, 3),
                        "age_minutes": age_minutes,
                        "age_hours": age_hours,
                        "url": p.get("url")
                    })

                # =====================================================
                # STEP 5 — SORT BY ALPHA SCORE & PICK BEST
                # =====================================================
                if candidates:
                    if mode == "SNIPER":
                        candidates.sort(key=lambda x: (x["alpha_score"], x["liquidity"]), reverse=True)
                    else:
                        candidates.sort(key=lambda x: (x["alpha_score"], x["volume_h1"]), reverse=True)

                    for best in candidates[:5]:
                        if not best.get("pair_address"):
                            best["pair_address"] = await resolve_best_pool(best["address"], session=session)

                        if best.get("pair_address"):
                            age_label = f"{best['age_minutes']:.1f}m" if best["age_minutes"] < 60 else f"{best['age_hours']:.2f}h"
                            trend = best.get("price_change_h1", 0)

                            sys_logger.add_log(
                                f"💎 GEM ({mode}) {best['symbol']} | Age {age_label} | "
                                f"Liq ${best['liquidity']:,.0f} | Vol1h ${best['volume_h1']:,.0f} | "
                                f"B/S {best['buy_sell_ratio']}x | Trend {trend}% | Score {best['alpha_score']}"
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
