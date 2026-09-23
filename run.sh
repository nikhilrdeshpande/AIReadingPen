#!/bin/sh
# Start the AI Reading Pen app. Usage: ./run.sh   (open http://localhost:8000)
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
exec .venv/bin/python -m app.server
