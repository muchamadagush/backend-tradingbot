# File: backend/solana_tracker.py
"""
Solana trade tracker - now using PostgreSQL for persistence.
Maintains backward compatibility with existing API.
"""
import time
from datetime import datetime

# Import from PostgreSQL layer
from database_pg import save_solana_trade, get_solana_trades, get_all_solana_trades


def load_history(limit: int = 50):
    """Load trade history from PostgreSQL"""
    return get_solana_trades(limit=limit)


def load_all_history():
    """Load all trade history for analytics"""
    return get_all_solana_trades()


def save_trade(txid, input_token, output_token, input_amount, output_estimate, status="Success", token_address=None):
    """Menyimpan transaksi baru dengan deteksi BUY/SELL yang akurat"""
    
    # --- LOGIKA DETEKSI SIDE (BUY/SELL) ---
    # Jika input adalah SOL/WSOL, berarti kita membeli token lain (BUY)
    # Jika input adalah token lain, berarti kita menjual ke SOL (SELL)
    is_buying = input_token in ["SOL", "WSOL"]
    side = "BUY" if is_buying else "SELL"
    
    # --- FORMAT SYMBOL KONSISTEN: SELALU TOKEN/SOL ---
    # Jika BUY: input=SOL, output=TOKEN → symbol = TOKEN/SOL
    # Jika SELL: input=TOKEN, output=SOL → symbol = TOKEN/SOL
    if is_buying:
        token_symbol = output_token  # Kita beli token ini
        symbol = f"{token_symbol}/SOL"
    else:
        token_symbol = input_token  # Kita jual token ini
        symbol = f"{token_symbol}/SOL"
    
    # --- LOG CONSOLE YANG JELAS ---
    print(f"\n{'='*60}")
    print(f"📝 Recording Transaction: {side}")
    print(f"   Symbol: {symbol}")
    print(f"   In:  {input_amount:.8f} {input_token}")
    print(f"   Out: {output_estimate:.8f} {output_token}")
    print(f"   Status: {status}")
    print(f"   TxID: {txid}")
    print(f"{'='*60}\n")
    
    # Generate unique trade ID
    trade_id = f"SOL-{int(time.time())}"
    timestamp = datetime.now()
    
    # Save to PostgreSQL
    trade = save_solana_trade(
        trade_id=trade_id,
        timestamp=timestamp,
        symbol=symbol,
        side=side,
        amount_in=input_amount,
        amount_out=output_estimate,
        token_address=token_address,
        txid=txid,
        status=status,
        source="Jupiter",
        price=0  # Calculated later if needed
    )
    
    if trade:
        return trade
    
    # Fallback: return dict if DB save failed
    return {
        "id": trade_id,
        "timestamp": timestamp.isoformat(),
        "symbol": symbol,
        "side": side,
        "price": 0,
        "amount_in": input_amount,
        "amount_out": output_estimate,
        "token_address": token_address,
        "txid": txid,
        "status": status,
        "source": "Jupiter"
    }