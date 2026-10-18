# File: backend/test_analytics.py
"""
Simple test untuk analytics module
Bisa dijalankan dengan: python test_analytics.py
"""

import json
import os
from datetime import datetime, timedelta
import sys

# Ensure UTF-8 output encoding on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Ensure we're running from backend directory for relative paths
if not os.path.exists("solana_trades.json") and os.path.exists("backend"):
    os.chdir("backend")
    print("📂 Changed to backend directory")

# Test imports
try:
    import analytics
    print("✅ Analytics module imported successfully")
except ImportError as e:
    print(f"❌ Failed to import analytics: {e}")
    sys.exit(1)

try:
    import pandas as pd
    print("✅ Pandas imported successfully")
except ImportError as e:
    print(f"❌ Failed to import pandas: {e}")
    print("   Run: pip install pandas")
    sys.exit(1)


def test_solana_analytics_empty():
    """Test Solana analytics dengan data kosong"""
    print("\n" + "="*60)
    print("TEST 1: Solana Analytics - Empty Data")
    print("="*60)
    
    result = analytics.calculate_solana_analytics()
    
    assert isinstance(result, dict), "Result harus dict"
    assert "data" in result, "Result harus punya 'data' key"
    assert "summary" in result, "Result harus punya 'summary' key"
    assert isinstance(result["data"], list), "Data harus list"
    
    summary = result["summary"]
    assert summary["total_trades"] == 0, "Harus 0 trades"
    assert summary["win_rate"] == 0.0, "Win rate harus 0"
    
    print("✅ Empty data test passed")


def test_solana_analytics_with_mock_data():
    """Test Solana analytics dengan mock data dan Realized PnL (FIFO)"""
    print("\n" + "="*60)
    print("TEST 2: Solana Analytics - Mock Data (FIFO Realized PnL)")
    print("="*60)
    
    # Create mock solana_trades.json: BUY yesterday, SELL today
    mock_trades = [
        {
            "timestamp": (datetime.now() - timedelta(days=1)).isoformat(),
            "symbol": "USDC/SOL",
            "side": "BUY",
            "amount_in": 1.0,  # Spent 1.0 SOL to buy 100 USDC (0.01 SOL/USDC)
            "amount_out": 100.0,  # 100 USDC
            "status": "Success"
        },
        {
            "timestamp": datetime.now().isoformat(),
            "symbol": "USDC/SOL",
            "side": "SELL",
            "amount_in": 50.0,  # Sold 50 USDC (cost basis = 50 * 0.01 = 0.5 SOL)
            "amount_out": 0.6,  # Received 0.6 SOL -> Realized PnL = +0.1 SOL
            "status": "Success"
        }
    ]
    
    # Temporarily save mock data
    original_path = "solana_trades.json"
    backup_path = "solana_trades.json.bak"
    
    if os.path.exists(original_path):
        os.rename(original_path, backup_path)
    
    try:
        with open(original_path, "w") as f:
            json.dump(mock_trades, f)
        
        result = analytics.calculate_solana_analytics(sol_to_usd=200.0)
        
        assert len(result["data"]) > 0, "Harus ada data"
        assert result["summary"]["total_trades"] == 1, "Harus 1 trade (SELL exit)"
        
        # Realized PnL:
        # BUY: 100 USDC for 1.0 SOL -> 0.01 SOL/USDC
        # SELL: 50 USDC for 0.6 SOL -> Cost basis = 0.5 SOL -> Realized = +0.1 SOL
        net_profit = result["summary"]["net_profit_sol"]
        print(f"   Net Realized Profit (SOL): {net_profit}")
        print(f"   Net Realized Profit (USD): ${result['summary']['net_profit_usd']:.2f}")
        print(f"   Win Rate: {result['summary']['win_rate']:.1f}%")
        print(f"   Total Trades (SELL exit): {result['summary']['total_trades']}")
        
        assert round(net_profit, 4) == 0.1, f"Expected net profit 0.1 SOL, got {net_profit}"
        assert result["summary"]["win_rate"] == 100.0, f"Expected 100% win rate, got {result['summary']['win_rate']}"
        assert result["summary"]["winning_trades"] == 1, "Expected 1 winning trade"
        assert result["summary"]["losing_trades"] == 0, "Expected 0 losing trades"
        
        print("✅ Mock data test passed")
        
    finally:
        # Restore original file
        if os.path.exists(original_path):
            os.remove(original_path)
        if os.path.exists(backup_path):
            os.rename(backup_path, original_path)


def test_tokocrypto_analytics_empty():
    """Test Tokocrypto analytics dengan database kosong"""
    print("\n" + "="*60)
    print("TEST 3: Tokocrypto Analytics - Empty Database")
    print("="*60)
    
    result = analytics.calculate_tokocrypto_analytics()
    
    assert isinstance(result, dict), "Result harus dict"
    assert "data" in result, "Result harus punya 'data' key"
    assert "summary" in result, "Result harus punya 'summary' key"
    
    summary = result["summary"]
    assert summary["total_trades"] == 0, "Harus 0 trades"
    assert summary["win_rate"] == 0.0, "Win rate harus 0"
    
    print("✅ Empty database test passed")


def test_timeframe_helper():
    """Test _get_pandas_freq helper"""
    print("\n" + "="*60)
    print("TEST 4: Timeframe Helper")
    print("="*60)
    
    assert analytics._get_pandas_freq("daily") == "D"
    assert analytics._get_pandas_freq("weekly") == "W"
    assert analytics._get_pandas_freq("monthly") == "M"
    assert analytics._get_pandas_freq("yearly") == "Y"
    
    print("✅ Timeframe helper test passed")


def run_all_tests():
    """Run semua test"""
    print("\n" + "="*70)
    print("🧪 RUNNING ANALYTICS MODULE TESTS")
    print("="*70)
    
    try:
        test_solana_analytics_empty()
        test_solana_analytics_with_mock_data()
        test_tokocrypto_analytics_empty()
        test_timeframe_helper()
        
        print("\n" + "="*70)
        print("✅ ALL TESTS PASSED!")
        print("="*70)
        print("\nModul analytics siap digunakan.")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
