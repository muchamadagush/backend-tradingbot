# File: backend/Dockerfile
# Optimized for 2GB RAM VPS & Podman (excludes unused matplotlib/numba/backtrader bloat)

FROM docker.io/library/python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install minimal system dependencies required for C-extensions (coincurve/psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Use lean requirements.txt instead of bloated requirements_docker.txt (saves ~450MB disk & ~150MB RAM)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Auto-initialize PostgreSQL tables (init_pg_db_cli.py) before launching FastAPI Uvicorn server
CMD ["sh", "-c", "python init_pg_db_cli.py && uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1"]
