"""
QUICK START SCRIPT
Test semua fitur safety bot dengan cepat
"""

import asyncio
import requests
import time

BASE_URL = "http://localhost:8000"

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

async def test_backtesting():
    """Test backtesting engine"""
    print_section("1. BACKTESTING TEST")
    
    print("Running backtest for BTC/USDT (30 days, 1h timeframe)...")
    
    response = requests.post(f"{BASE_URL}/api/backtest/run", json={
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "days_back": 30,
        "initial_capital": 1000,
        "take_profit_pct": 1.5,
        "stop_loss_pct": 3.0
    })
    
    if response.status_code == 200:
        result = response.json()
        
        if 'error' in result:
            print(f"❌ Error: {result['error']}")
            return False
        
        print(f"✅ Backtest Complete!")
        print(f"   Initial Capital: ${result['initial_capital']:.2f}")
        print(f"   Final Capital: ${result['final_capital']:.2f}")
        print(f"   ROI: {result['roi_pct']:.2f}%")
        print(f"   Total Trades: {result['total_trades']}")
        print(f"   Win Rate: {result['win_rate_pct']:.2f}%")
        print(f"   Max Drawdown: {result['max_drawdown_pct']:.2f}%")
        print(f"   Total Fees: ${result['total_fees_paid']:.2f}")
        
        return result['roi_pct'] > 0
    else:
        print(f"❌ Request failed: {response.status_code}")
        return False

def test_paper_trading():
    """Test paper trading"""
    print_section("2. PAPER TRADING TEST")
    
    # Check account
    print("Checking paper trading account...")
    account = requests.get(f"{BASE_URL}/api/paper/account")
    
    if account.status_code == 200:
        acc_data = account.json()
        print(f"✅ Account Status:")
        print(f"   Balance: ${acc_data['balance']:.2f}")
        print(f"   Total Equity: ${acc_data['total_equity']:.2f}")
        print(f"   Total P&L: ${acc_data['total_pnl']:.2f} ({acc_data['pnl_pct']:.2f}%)")
        print(f"   Open Positions: {acc_data['open_positions']}")
        
        # Get current BTC price
        print("\nGetting BTC/USDT price...")
        price_resp = requests.get(f"{BASE_URL}/api/toko/price/BTC/USDT")
        
        if price_resp.status_code == 200:
            price_data = price_resp.json()
            current_price = price_data['current_price']
            print(f"✅ Current Price: ${current_price:.2f}")
            
            # Place paper buy
            print("\nPlacing paper BUY order (100 USDT)...")
            buy = requests.post(f"{BASE_URL}/api/paper/buy", json={
                "symbol": "BTC/USDT",
                "amount_usdt": 100
            })
            
            if buy.status_code == 200:
                buy_data = buy.json()
                if buy_data['status'] == 'success':
                    print(f"✅ Buy Success!")
                    print(f"   Asset received: {buy_data['position']['amount']:.8f} BTC")
                    print(f"   Entry price: ${buy_data['order']['execution_price']:.2f}")
                    print(f"   Fee paid: ${buy_data['order']['fee']:.2f}")
                    
                    # Wait a bit
                    print("\n⏳ Waiting 3 seconds...")
                    time.sleep(3)
                    
                    # Sell position
                    print("\nPlacing paper SELL order (100%)...")
                    sell = requests.post(f"{BASE_URL}/api/paper/sell", json={
                        "symbol": "BTC/USDT",
                        "percentage": 100
                    })
                    
                    if sell.status_code == 200:
                        sell_data = sell.json()
                        if sell_data['status'] == 'success':
                            trade = sell_data['trade']
                            print(f"✅ Sell Success!")
                            print(f"   Exit price: ${trade['exit_price']:.2f}")
                            print(f"   Profit: ${trade['profit']:.2f} ({trade['profit_pct']:.2f}%)")
                            return True
                else:
                    print(f"⚠️ Buy rejected: {buy_data.get('message', 'Unknown')}")
    
    return False

def test_fee_calculator():
    """Test fee calculator"""
    print_section("3. FEE CALCULATOR TEST")
    
    # Test buy cost
    print("Calculating buy cost for 100 USDT at $50,000...")
    buy_fee = requests.post(f"{BASE_URL}/api/fee/calculate-buy", json={
        "amount_usdt": 100,
        "price": 50000,
        "order_type": "market"
    })
    
    if buy_fee.status_code == 200:
        data = buy_fee.json()
        print(f"✅ Buy Cost Analysis:")
        print(f"   Total Cost: ${data['total_cost_usdt']:.2f}")
        print(f"   Asset Amount: {data['asset_amount']:.8f} BTC")
        print(f"   Fee: ${data['fee_usdt']:.2f} ({data['fee_pct']:.2f}%)")
        print(f"   Slippage Cost: ${data['slippage_cost']:.2f}")
        print(f"   Total Overhead: ${data['total_overhead']:.2f}")
        
        # Test round-trip
        print("\nCalculating round-trip cost...")
        rt = requests.post(f"{BASE_URL}/api/fee/round-trip", json={
            "amount_usdt": 100,
            "price": 50000
        })
        
        if rt.status_code == 200:
            rt_data = rt.json()
            print(f"✅ Round-Trip Analysis:")
            print(f"   Initial: ${rt_data['initial_capital']:.2f}")
            print(f"   Final: ${rt_data['final_capital']:.2f}")
            print(f"   Loss: ${rt_data['total_loss']:.2f} ({rt_data['loss_pct']:.2f}%)")
            print(f"   {rt_data['message']}")
            return True
    
    return False

def test_risk_management():
    """Test risk management"""
    print_section("4. RISK MANAGEMENT TEST")
    
    # Test position sizing
    print("Calculating optimal position size...")
    print("Entry: $50,000 | Stop-Loss: $48,500 (3% risk)")
    
    pos = requests.post(f"{BASE_URL}/api/risk/position-size", json={
        "capital": 1000,
        "entry_price": 50000,
        "stop_loss_price": 48500
    })
    
    if pos.status_code == 200:
        data = pos.json()
        if data['status'] == 'success':
            print(f"✅ Position Sizing:")
            print(f"   {data['message']}")
            print(f"   Position Size: {data['position_size']:.8f} BTC")
            print(f"   Position Value: ${data['position_value_usdt']:.2f}")
            print(f"   Risk Amount: ${data['risk_amount']:.2f} ({data['risk_pct']:.2f}%)")
            
            # Test Kelly Criterion
            print("\nCalculating Kelly Criterion...")
            kelly = requests.post(f"{BASE_URL}/api/risk/kelly", json={
                "win_rate": 55,
                "avg_win": 50,
                "avg_loss": 30
            })
            
            if kelly.status_code == 200:
                kelly_data = kelly.json()
                if kelly_data['status'] == 'success':
                    print(f"✅ Kelly Criterion:")
                    print(f"   {kelly_data['message']}")
                    print(f"   Recommendation: {kelly_data['recommendation']}")
                    
                    # Get risk summary
                    print("\nGetting risk summary...")
                    summary = requests.get(f"{BASE_URL}/api/risk/summary")
                    
                    if summary.status_code == 200:
                        sum_data = summary.json()
                        print(f"✅ Risk Summary:")
                        print(f"   Risk Level: {sum_data['risk_level']}")
                        print(f"   Current Drawdown: {sum_data['current_drawdown_pct']:.2f}%")
                        print(f"   Daily P&L: ${sum_data['daily_pnl']:.2f}")
                        print(f"   Open Positions: {sum_data['open_positions']}/{sum_data['limits']['max_positions']}")
                        return True
    
    return False

def main():
    """Run all tests"""
    print(f"\n{'*'*60}")
    print("  🤖 TRADING BOT - SAFETY FEATURES TEST")
    print(f"{'*'*60}")
    print("\nMake sure bot is running: uvicorn main:app --reload")
    print("\nPress Enter to start tests...")
    input()
    
    results = {}
    
    # Run tests
    try:
        results['fee_calculator'] = test_fee_calculator()
        results['risk_management'] = test_risk_management()
        results['paper_trading'] = test_paper_trading()
        
        # Backtest takes longer, ask user
        print("\n⏳ Backtesting will take 30-60 seconds (downloading data).")
        run_backtest = input("Run backtest? (y/n): ").lower() == 'y'
        
        if run_backtest:
            results['backtesting'] = asyncio.run(test_backtesting())
        else:
            results['backtesting'] = None
            print("⏭️  Skipped backtest")
        
    except Exception as e:
        print(f"\n❌ Error during tests: {e}")
        print("\nMake sure the bot is running:")
        print("  cd backend")
        print("  uvicorn main:app --reload")
        return
    
    # Summary
    print_section("TEST SUMMARY")
    
    for test_name, result in results.items():
        if result is None:
            status = "⏭️  SKIPPED"
        elif result:
            status = "✅ PASSED"
        else:
            status = "❌ FAILED"
        
        print(f"{status} - {test_name.replace('_', ' ').title()}")
    
    passed = sum([1 for r in results.values() if r == True])
    total = sum([1 for r in results.values() if r is not None])
    
    print(f"\n📊 Result: {passed}/{total} tests passed")
    
    if passed == total and total > 0:
        print("\n🎉 All tests passed! Bot is ready for paper trading.")
        print("\n⚠️  REMEMBER:")
        print("   1. Test with paper trading for 1-2 weeks")
        print("   2. Only use LIVE mode if consistently profitable")
        print("   3. Never risk money you can't afford to lose")
    else:
        print("\n⚠️  Some tests failed. Check the errors above.")

if __name__ == "__main__":
    main()
