# BESS Tracker sync backend — container image for Render / any Docker host.
FROM python:3.12-slim

# Faster, quieter Python; no .pyc clutter
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: build tools for argon2-cffi / psycopg2 wheels are usually not
# needed (binary wheels), but keep libpq for psycopg2-binary runtime safety.
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code (build context is the backend/ directory)
COPY . .

# Persistent data lives on a mounted disk; defaults suit Render's /var/data.
ENV UPLOAD_DIR=/var/data/uploads

# Render provides $PORT; default to 8000 for local `docker run`.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
