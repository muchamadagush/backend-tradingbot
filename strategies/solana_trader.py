# ==========================================
# File: strategies/solana_trader.py
# ==========================================

import time
import pandas as pd
import pandas_ta as ta

from strategies.base import BaseStrategy


class SolanaTraderStrategy(BaseStrategy):
    """
    Momentum + Volume + Structure Breakout Strategy v2
    Designed for Solana DEX tokens with anti-exhaustion & time-decay protection.
    """

    name = "SOLANA_TRADER"

    # ---- CONFIG ----
    MIN_CANDLES = 40
    COOLDOWN_SECONDS = 15 * 60
    MAX_HOLD_SECONDS = 20 * 60          # 20 min stagnation time-stop

    # Asymmetric Risk-Reward Profile (Positive Expectancy after DEX fees & priority fees)
    HARD_SL_PCT = -6.5                  # Tighter hard SL (-6.5% vs old -8.0%)
    BREAKEVEN_LOCK_PCT = 1.5            # Locked SL (+1.5%) after TP1 partial is hit
    TP1_PCT = 12.0                      # Take 50% partial profit at +12% to de-risk quickly
    TP2_PCT = 28.0                      # Full runner target at +28% (let trailing stop ride)

    # Dynamic SL based on ATR
    ATR_SL_MULTIPLIER = 1.8             # SL = entry - (ATR * 1.8)

    def __init__(self):
        self.last_entry_ts = {}

    # ==================================================
    # BUY LOGIC (WITH ANTI-EXHAUSTION & VOLUME ACCELERATION)
    # ==================================================

    def should_buy(self, market) -> (bool, str):
        df: pd.DataFrame = market.get("df")
        token = market.get("token", "BACKTEST")

        if df is None or len(df) < self.MIN_CANDLES:
            return False, "not enough candles"

        # ---- COOLDOWN (LIVE ONLY) ----
        last_entry = self.last_entry_ts.get(token)
        if last_entry and time.time() - last_entry < self.COOLDOWN_SECONDS:
            return False, "cooldown active"

        df = df.copy()
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # ==================================================
        # INDICATORS
        # ==================================================
        df["rsi"] = ta.rsi(df["close"], length=14)
        df["ema9"] = ta.ema(df["close"], length=9)
        df["ema20"] = ta.ema(df["close"], length=20)
        df["ema50"] = ta.ema(df["close"], length=50)
        df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        bb = ta.bbands(df["close"], length=20, std=2)
        if bb is None or bb.empty:
            return False, "bbands calculation failed"
        bb_l = next(c for c in bb.columns if c.startswith("BBL"))
        bb_u = next(c for c in bb.columns if c.startswith("BBU"))

        df["bb_l"] = bb[bb_l]
        df["bb_u"] = bb[bb_u]

        df.dropna(inplace=True)
        if len(df) < 5:
            return False, "indicator warmup"

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # ==================================================
        # MARKET REGIME & STRUCTURE FILTER
        # ==================================================

        # 1. Stacked Bullish Trend Structure (EMA9 > EMA20 > EMA50 & Close > EMA9)
        if not (last["ema9"] > last["ema20"] > last["ema50"] and last["close"] >= last["ema9"]):
            return False, "no stacked uptrend (EMA9>20>50)"

        # 2. Anti-Overextension Filter (Jangan beli jika harga sudah >10% di atas EMA20 = rawan dump)
        dist_from_ema20_pct = ((last["close"] - last["ema20"]) / last["ema20"]) * 100.0
        if dist_from_ema20_pct > 10.0:
            return False, f"price overextended (+{dist_from_ema20_pct:.1f}% above EMA20)"

        # 3. Candle Quality Filter (Tolak candle dengan ekor atas panjang / distribusi)
        candle_range = max(last["high"] - last["low"], 1e-12)
        upper_wick_ratio = (last["high"] - max(last["open"], last["close"])) / candle_range
        if last["close"] < last["open"] or upper_wick_ratio > 0.45:
            return False, "bearish or upper-wick rejection candle"

        # 4. Volatility & Bollinger Band Health
        atr_ma = df["atr"].rolling(20, min_periods=5).mean().iloc[-1]
        if last["atr"] <= atr_ma * 0.95:
            return False, "low volatility expansion"

        bb_width = (last["bb_u"] - last["bb_l"]) / last["close"]
        if bb_width < 0.04 or bb_width > 0.35:
            return False, f"bb width out of sweet spot ({bb_width:.2f})"

        # ==================================================
        # ENTRY SIGNAL (MOMENTUM + VOLUME SURGE)
        # ==================================================

        # RSI rising in healthy momentum zone (48 - 68)
        if not (48.0 <= last["rsi"] <= 68.0 and last["rsi"] >= prev["rsi"] - 1.0):
            return False, f"rsi not in momentum zone ({last['rsi']:.1f})"

        vol_ma = df["volume"].rolling(20, min_periods=5).mean().iloc[-1]
        if last["volume"] < vol_ma * 1.30:
            return False, "insufficient volume surge (<1.3x MA)"

        return True, f"momentum breakout confirmed (RSI {last['rsi']:.1f}, Vol {last['volume']/max(vol_ma,1e-9):.1f}x)"

    # ==================================================
    # SELL LOGIC (DYNAMIC SL + BREAKEVEN + TIME STOP)
    # ==================================================

    def should_sell(self, position, market) -> (bool, str):
        pnl = float(position.get("pnl_pct", 0.0))
        tp1_hit = bool(position.get("tp1_hit", False))

        # ---- 1. BREAKEVEN LOCK AFTER TP1 PARTIAL ----
        if tp1_hit:
            effective_sl = max(float(position.get("dynamic_sl_pct", self.HARD_SL_PCT)), self.BREAKEVEN_LOCK_PCT)
            if pnl <= effective_sl:
                return True, f"Breakeven Lock hit after TP1 ({pnl:.2f}% <= {effective_sl:.2f}%)"
        else:
            # ---- 2. DYNAMIC ATR-BASED SL ----
            dynamic_sl_pct = float(position.get("dynamic_sl_pct", self.HARD_SL_PCT))
            effective_sl = max(dynamic_sl_pct, self.HARD_SL_PCT)
            if pnl <= effective_sl:
                return True, f"SL hit ({pnl:.2f}% <= {effective_sl:.2f}%)"

        # ---- 3. STAGNATION TIME-STOP (DEAD MOMENTUM FILTER) ----
        entry_time = position.get("entry_time")
        if entry_time:
            hold_seconds = time.time() - float(entry_time)
            if hold_seconds >= self.MAX_HOLD_SECONDS and pnl < 3.0 and not tp1_hit:
                return True, f"Time-stop stagnation ({int(hold_seconds/60)}m held, PnL {pnl:.2f}%)"

        # ---- 4. PARTIAL & FULL TAKE PROFIT ----
        if pnl >= self.TP1_PCT and not tp1_hit:
            position["tp1_hit"] = True
            position["dynamic_sl_pct"] = self.BREAKEVEN_LOCK_PCT
            return True, "tp1 partial"

        if pnl >= self.TP2_PCT:
            return True, "tp2 full exit"

        return False, ""

    # ==================================================
    # STATE
    # ==================================================

    def on_buy(self, position):
        token = position.get("token", "BACKTEST")
        self.last_entry_ts[token] = time.time()
        position["tp1_hit"] = False
        if "entry_time" not in position:
            position["entry_time"] = time.time()

        # Calculate dynamic SL based on ATR at entry
        atr = float(position.get("atr", 0) or 0)
        entry_price = float(position.get("entry_price", 0) or 0)

        if atr > 0 and entry_price > 0:
            sl_distance = atr * self.ATR_SL_MULTIPLIER
            dynamic_sl_pct = -(sl_distance / entry_price) * 100.0
            # Clamp between -3.0% and HARD_SL_PCT (-6.5%)
            position["dynamic_sl_pct"] = max(min(dynamic_sl_pct, -3.0), self.HARD_SL_PCT)
        else:
            position["dynamic_sl_pct"] = self.HARD_SL_PCT

    def on_sell(self, position):
        pass

    # ==================================================
    # RISK-ADJUSTED POSITION SIZING
    # ==================================================

    def calculate_position_size(
        self,
        capital: float,
        entry_price: float,
        atr: float,
        risk_pct: float = 1.5
    ) -> dict:
        if atr <= 0 or entry_price <= 0 or capital <= 0:
            return {"error": "Invalid inputs", "position_size": 0}

        risk_amount = capital * (risk_pct / 100.0)
        sl_distance = atr * self.ATR_SL_MULTIPLIER
        sl_price = entry_price - sl_distance

        position_size = risk_amount / sl_distance
        position_value = position_size * entry_price

        max_position_value = capital * 0.15
        if position_value > max_position_value:
            position_size = max_position_value / entry_price
            position_value = max_position_value

        return {
            "position_size": position_size,
            "position_value_usd": position_value,
            "sl_price": sl_price,
            "sl_pct": -(sl_distance / entry_price) * 100.0,
            "risk_amount": risk_amount,
            "risk_pct": risk_pct,
        }
