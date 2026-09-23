#!/bin/sh
# Start the AI Reading Pen app and keep it alive. Usage: ./run.sh   (open http://localhost:8000)
# Ctrl-C stops it. If the process ever dies it is restarted after one second.
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
trap 'kill $child 2>/dev/null; exit 0' INT TERM
while true; do
  .venv/bin/python -m app.server &
  child=$!
  wait $child
  echo "app exited, restarting in 1 s (Ctrl-C to stop)"
  sleep 1
done
