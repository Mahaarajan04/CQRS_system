#!/bin/bash
# Start all CQRS services locally (no Docker needed)

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/venv/bin/activate"

source "$VENV"

echo "[START] Write API  → http://localhost:8001"
cd "$ROOT/write-api" && uvicorn main:app --port 8001 --log-level info > /tmp/write-api.log 2>&1 &
WRITE_PID=$!

echo "[START] Read API   → http://localhost:8002"
cd "$ROOT/read-api"  && uvicorn main:app --port 8002 --log-level info > /tmp/read-api.log 2>&1 &
READ_PID=$!

echo "[START] Consumer worker"
cd "$ROOT/consumer"  && python worker.py > /tmp/consumer.log 2>&1 &
CONSUMER_PID=$!

echo "[START] Saga orchestrator"
cd "$ROOT/saga"      && python saga_worker.py > /tmp/saga.log 2>&1 &
SAGA_PID=$!

echo ""
echo "PIDs: write=$WRITE_PID  read=$READ_PID  consumer=$CONSUMER_PID  saga=$SAGA_PID"
echo "Logs: /tmp/write-api.log  /tmp/read-api.log  /tmp/consumer.log  /tmp/saga.log"
echo ""
echo "Press Ctrl+C to stop all services"

trap "kill $WRITE_PID $READ_PID $CONSUMER_PID $SAGA_PID 2>/dev/null; echo 'Stopped.'" INT TERM
wait
