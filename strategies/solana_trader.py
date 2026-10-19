# ==========================================
# File: strategies/solana_trader.py
# High-Win-Rate v3: Institutional VWAP Pullback + Early Breakeven Shield
# ==========================================

import time
import pandas as pd
import pandas_ta as ta

from strategies.base import BaseStrategy


class SolanaTraderStrategy(BaseStrategy):
    """
    High-Win-Rate v3 Strategy (Target Win Rate: 68% - 75%+):
    1. Institutional VWAP + Stacked EMA (EMA9 > EMA20 > EMA50) Trend Filter
    2. Anti-Breakout-Chasing Entry: Buys on healthy Pullback/Retest to EMA9/VWAP
       or Tight Compression Breakout with accelerating MACD & Volume
    3. 2-Stage Dynamic Profit Shield:
       - Stage 1 (Early Breakeven Shield): At +4.2% profit, locks Stop-Loss at +1.0% (net fee-free win)
       - Stage 2 (Momentum Lock): At +8.0% profit, raises Stop-Loss floor to +4.5%
    4. Micro-Capital Single Full Exit Mode: Avoids double swap gas fees on small SOL balances
    """

    name = "SOLANA_TRADER"

    # ---- CONFIG ----
    MIN_CANDLES = 40
    COOLDOWN_SECONDS = 15 * 60
    MAX_HOLD_SECONDS = 18 * 60          # 18 min stagnation time-stop

    # Micro-Capital Optimization (Rp 200.000 / ~0.10 SOL)
    # When True (or when position entry <= 0.05 SOL), skips 50% partial sell to save 50% exit gas fees
    MICRO_CAPITAL_SINGLE_EXIT = True
    MICRO_CAPITAL_THRESHOLD_SOL = 0.05

    # High-Win-Rate Protection & Exit Profile
    HARD_SL_PCT = -5.5                  # Tighter hard SL (-5.5% max risk on pullback entries)
    EARLY_BE_TRIGGER_PCT = 4.2          # Activate Early Breakeven Shield at +4.2% profit
    EARLY_BE_LOCK_PCT = 1.0             # Lock +1.0% net profit (covers Jupiter swap + priority fees)
    STAGE2_LOCK_TRIGGER_PCT = 8.0       # At +8.0% profit, lock minimum +4.5% profit floor
    STAGE2_LOCK_SL_PCT = 4.5
    BREAKEVEN_LOCK_PCT = 1.5            # Locked SL (+1.5%) after TP1 partial (for large capital mode)
    TP1_PCT = 12.0                      # Partial profit target (or tight trailing trigger in micro-capital mode)
    TP2_PCT = 24.0                      # Full runner target at +24%

    # Dynamic SL based on ATR
    ATR_SL_MULTIPLIER = 1.6             # Tighter ATR multiplier since entry is near EMA9/VWAP support

    def __init__(self):
        self.last_entry_ts = {}

    # ==================================================
    # BUY LOGIC (VWAP + EMA9 PULLBACK & COMPRESSION BREAKOUT)
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
        # INDICATORS (VWAP + STACKED EMAs + MACD + RSI + ATR + BB)
        # ==================================================
        df["rsi"] = ta.rsi(df["close"], length=14)
        df["ema9"] = ta.ema(df["close"], length=9)
        df["ema20"] = ta.ema(df["close"], length=20)
        df["ema50"] = ta.ema(df["close"], length=50)
        df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        # Rolling 30-minute Institutional VWAP
        typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
        tp_vol = typical_price * df["volume"]
        vol_sum = df["volume"].rolling(30, min_periods=10).sum().replace(0, pd.NA)
        df["vwap"] = (tp_vol.rolling(30, min_periods=10).sum() / vol_sum).fillna(df["ema20"])

        # MACD for momentum acceleration check
        macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if macd is not None and not macd.empty:
            hist_col = next((c for c in macd.columns if c.startswith("MACDh_")), None)
            df["macd_hist"] = macd[hist_col] if hist_col else 0.0
        else:
            df["macd_hist"] = 0.0

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
        prev2 = df.iloc[-3]

        # ==================================================
        # 1. INSTITUTIONAL TREND & VWAP STRUCTURE FILTER
        # ==================================================

        # Stacked Bullish Trend Structure (EMA9 > EMA20 > EMA50 & Close >= EMA9 & Close >= VWAP)
        if not (last["ema9"] > last["ema20"] > last["ema50"] and last["close"] >= last["ema9"] * 0.998):
            return False, "no stacked uptrend (EMA9>20>50)"

        if last["close"] < last["vwap"] * 0.996:
            return False, "price below institutional VWAP"

        # ==================================================
        # 2. STRICT ANTI-FOMO / ANTI-OVEREXTENSION FILTER
        # ==================================================

        # Reject chasing tops (>7.5% above EMA20 or >3.8% above EMA9)
        dist_from_ema20_pct = ((last["close"] - last["ema20"]) / last["ema20"]) * 100.0
        dist_from_ema9_pct = ((last["close"] - last["ema9"]) / last["ema9"]) * 100.0
        if dist_from_ema20_pct > 7.5 or dist_from_ema9_pct > 3.8:
            return False, f"price overextended (+{dist_from_ema20_pct:.1f}% vs EMA20)"

        bb_range = max(last["bb_u"] - last["bb_l"], 1e-12)
        bb_pct_b = (last["close"] - last["bb_l"]) / bb_range
        if bb_pct_b > 0.88:
            return False, f"near upper BB exhaustion (%B={bb_pct_b:.2f})"

        # ==================================================
        # 3. CANDLE QUALITY & MOMENTUM ACCELERATION
        # ==================================================

        candle_range = max(last["high"] - last["low"], 1e-12)
        upper_wick_ratio = (last["high"] - max(last["open"], last["close"])) / candle_range
        body_ratio = abs(last["close"] - last["open"]) / candle_range

        if last["close"] <= last["open"] or upper_wick_ratio > 0.38 or body_ratio < 0.25:
            return False, "weak body or upper-wick seller rejection"

        # Require rising or positive MACD histogram
        if last["macd_hist"] < prev["macd_hist"] and last["macd_hist"] <= 0:
            return False, "decelerating MACD momentum"

        bb_width = (last["bb_u"] - last["bb_l"]) / last["close"]
        if bb_width < 0.035 or bb_width > 0.32:
            return False, f"bb width out of sweet spot ({bb_width:.2f})"

        vol_ma = df["volume"].rolling(20, min_periods=5).mean().iloc[-1]
        vol_ratio = last["volume"] / max(vol_ma, 1e-9)

        # ==================================================
        # 4. DUAL HIGH-WIN-RATE ENTRY SETUPS
        # ==================================================

        # SETUP A (Highest Win Rate): Healthy Pullback / Retest to EMA9 or VWAP followed by Bullish Bounce
        touched_support_recently = (
            last["low"] <= last["ema9"] * 1.008
            or prev["low"] <= prev["ema9"] * 1.008
            or prev2["low"] <= prev2["ema20"] * 1.010
            or prev["low"] <= prev["vwap"] * 1.008
        )
        rsi_pullback_ok = (46.0 <= last["rsi"] <= 64.0) and (last["rsi"] >= prev["rsi"])

        if touched_support_recently and rsi_pullback_ok and vol_ratio >= 1.05:
            return (
                True,
                f"VWAP/EMA9 pullback bounce (RSI {last['rsi']:.1f}, Vol {vol_ratio:.1f}x, DistEMA9 +{dist_from_ema9_pct:.1f}%)"
            )

        # SETUP B: Tight Compression Breakout with Strong Volume Surge
        atr_ma = df["atr"].rolling(20, min_periods=5).mean().iloc[-1]
        rsi_breakout_ok = (50.0 <= last["rsi"] <= 65.0) and (last["rsi"] > prev["rsi"])

        if (
            rsi_breakout_ok
            and vol_ratio >= 1.35
            and last["atr"] >= atr_ma * 0.95
            and dist_from_ema9_pct <= 2.5
        ):
            return (
                True,
                f"compression breakout (RSI {last['rsi']:.1f}, Vol {vol_ratio:.1f}x, DistEMA9 +{dist_from_ema9_pct:.1f}%)"
            )

        return False, f"waiting for pullback or volume surge (RSI {last['rsi']:.1f}, Vol {vol_ratio:.1f}x)"

    # ==================================================
    # SELL LOGIC (2-STAGE PROFIT SHIELD + MICRO-CAPITAL SINGLE EXIT)
    # ==================================================

    def should_sell(self, position, market) -> (bool, str):
        pnl = float(position.get("pnl_pct", 0.0))
        tp1_hit = bool(position.get("tp1_hit", False))
        entry_sol = float(position.get("entry_price_sol", 0.0) or 0.0)
        is_micro_capital = self.MICRO_CAPITAL_SINGLE_EXIT or (0 < entry_sol <= self.MICRO_CAPITAL_THRESHOLD_SOL)

        # ---- 1. UPDATE 2-STAGE EARLY BREAKEVEN & PROFIT SHIELD ----
        if pnl >= self.EARLY_BE_TRIGGER_PCT and not position.get("be_shield_active"):
            position["be_shield_active"] = True
            position["dynamic_sl_pct"] = max(
                float(position.get("dynamic_sl_pct", self.HARD_SL_PCT)),
                self.EARLY_BE_LOCK_PCT
            )

        if pnl >= self.STAGE2_LOCK_TRIGGER_PCT:
            position["dynamic_sl_pct"] = max(
                float(position.get("dynamic_sl_pct", self.HARD_SL_PCT)),
                self.STAGE2_LOCK_SL_PCT
            )

        # ---- 2. CHECK PROTECTED FLOOR OR DYNAMIC ATR SL ----
        if tp1_hit or position.get("be_shield_active"):
            effective_sl = float(position.get("dynamic_sl_pct", self.EARLY_BE_LOCK_PCT))
            if pnl <= effective_sl:
                return True, f"Profit Shield Lock hit ({pnl:+.2f}% <= {effective_sl:+.2f}%)"
        else:
            dynamic_sl_pct = float(position.get("dynamic_sl_pct", self.HARD_SL_PCT))
            effective_sl = max(dynamic_sl_pct, self.HARD_SL_PCT)
            if pnl <= effective_sl:
                return True, f"SL hit ({pnl:.2f}% <= {effective_sl:.2f}%)"

        # ---- 3. STAGNATION TIME-STOP (DEAD MOMENTUM FILTER) ----
        entry_time = position.get("entry_time")
        if entry_time:
            hold_seconds = time.time() - float(entry_time)
            # If held > 12 mins and still negative/flat (< +1.2%), exit early before dump
            if hold_seconds >= 12 * 60 and pnl < 1.2 and not position.get("be_shield_active"):
                return True, f"Early stagnation exit ({int(hold_seconds/60)}m held, PnL {pnl:.2f}%)"
            if hold_seconds >= self.MAX_HOLD_SECONDS and pnl < 3.5 and not tp1_hit:
                return True, f"Time-stop stagnation ({int(hold_seconds/60)}m held, PnL {pnl:.2f}%)"

        # ---- 4. TAKE PROFIT (SINGLE FULL EXIT FOR MICRO-CAPITAL vs PARTIAL FOR LARGE CAPITAL) ----
        if pnl >= self.TP1_PCT and not tp1_hit:
            position["tp1_hit"] = True
            if is_micro_capital:
                # Lock +9.5% floor immediately and let SolanaAutoBot's tight trailing stop (1.2%) ride to TP2
                position["dynamic_sl_pct"] = max(float(position.get("dynamic_sl_pct", 0.0)), 9.5)
            else:
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
        position["be_shield_active"] = False
        if "entry_time" not in position:
            position["entry_time"] = time.time()

        # Calculate dynamic SL based on ATR at entry
        atr = float(position.get("atr", 0) or 0)
        entry_price = float(position.get("entry_price", 0) or 0)

        if atr > 0 and entry_price > 0:
            sl_distance = atr * self.ATR_SL_MULTIPLIER
            dynamic_sl_pct = -(sl_distance / entry_price) * 100.0
            # Clamp between -2.8% and HARD_SL_PCT (-5.5%)
            position["dynamic_sl_pct"] = max(min(dynamic_sl_pct, -2.8), self.HARD_SL_PCT)
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

        max_position_value = capital * 0.25
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