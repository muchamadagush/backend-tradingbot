# TradingBot Backend Engine

Algorithmic trading engine supporting **Tokocrypto/Binance Spot Trading (CEX)** via CCXT and **Solana (DEX)** on-chain trading via Jupiter Aggregator API, powered by FastAPI & PostgreSQL.

---

## 🛠️ Tech Stack

- **Framework**: FastAPI (Python 3.10+)
- **Database**: PostgreSQL 15+ (SQLAlchemy ORM + atomic ledger)
- **CEX Engine**: CCXT (Async)
- **DEX Engine**: Jupiter Swap API, Solana-py / Solders, Dexscreener & GeckoTerminal APIs
- **Safety**: RugCheck API, Portfolio Drawdown Kill Switch, Dynamic ATR SL/TP
- **Auth**: API Key + JWT Bearer Tokens

---

## 🚀 Getting Started

### 1. Database Setup
```bash
docker-compose up -d postgres
```

### 2. Environment Configuration
Copy `.env.example` to `.env` and fill in the necessary API keys and credentials:
```bash
cp .env.example .env
```

### 3. Virtual Environment & Dependencies
```bash
python -m venv venv

# Windows (PowerShell):
.\venv\Scripts\Activate.ps1

# Linux / macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### 4. Initialize Database & Seed User
```bash
python init_pg_db_cli.py
python seed_user.py
```

### 5. Run Server
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
Swagger API docs available at: `http://localhost:8000/docs`

---

## 🛡️ Financial Guardrails
- Maximum slippage default on DEX trades is strictly 100 bps (1.0%).
- Real-time confirmation of transaction signatures before updating trade ledger state.
- Automated kill switch for daily drawdown and portfolio circuit breakers.
