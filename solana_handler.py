# File: backend/solana_handler.py
import os
from solders.keypair import Keypair # type: ignore
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey # type: ignore
from dotenv import load_dotenv

load_dotenv()

# Load Konfigurasi
RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY")

class SolanaManager:
    def __init__(self):
        try:
            # 1. Inisialisasi Keypair (Dompet) dari Private Key
            if PRIVATE_KEY:
                # Private key biasanya format Base58 string
                self.payer = Keypair.from_base58_string(PRIVATE_KEY)
                self.public_key = self.payer.pubkey()
                print(f"✅ Solana Wallet Loaded: {self.public_key}")
            else:
                self.payer = None
                print("⚠️ Solana Private Key belum disetting di .env")
        except Exception as e:
            print(f"❌ Error Load Wallet: {e}")
            self.payer = None

    async def get_balance(self):
        """Cek Saldo SOL di Wallet"""
        if not self.payer: return 0.0

        async with AsyncClient(RPC_URL) as client:
            try:
                # Request Balance ke Blockchain
                response = await client.get_balance(self.public_key)
                
                # Konversi dari Lamport ke SOL (1 SOL = 1 Miliar Lamport)
                balance_lamports = response.value
                balance_sol = balance_lamports / 1_000_000_000
                return balance_sol
            except Exception as e:
                print(f"⚠️ Gagal cek saldo Solana: {e}")
                return 0.0

# Buat satu instance agar bisa dipanggil di main.py
solana_bot = SolanaManager()