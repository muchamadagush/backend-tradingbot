# File: backend/sys_logger.py
from datetime import datetime

# Alias for compatibility with sys_logger.log(...)
def log(message):
    add_log(message)

# Buffer untuk menyimpan 50 log terakhir
log_buffer = []

def add_log(message):
    """Menambahkan pesan ke buffer dan print ke terminal"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted_msg = f"[{timestamp}] {message}"
    
    # 1. Tetap print ke terminal asli (biar debug mudah)
    print(formatted_msg)
    
    # 2. Simpan ke memory untuk Frontend
    log_buffer.append(formatted_msg)
    
    # Jaga agar memory tidak penuh (cuma simpan 50 baris terakhir)
    if len(log_buffer) > 50:
        log_buffer.pop(0)

def get_logs():
    """Ambil semua log (urutan terbaru di bawah)"""
    return log_buffer