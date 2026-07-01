# NetWatcher for UniFi

> A lightweight UniFi unknown-device monitor with WebUI, SQLite history, approval workflow, and pluggable alerts.

This project monitors your network for unknown MAC addresses using data from a UniFi Controller / UniFi Network Server and provides configurable alerts (Pushover, Webhooks, etc.).

## Quickstart (Development)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8080
```

## Docker

```bash
docker compose up -d
```
