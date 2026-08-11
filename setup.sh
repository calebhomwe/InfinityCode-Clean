#!/bin/bash
set -e
echo "=== Infinity Code Setup ==="

python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

npm install

mkdir -p backend/skills backend/references backend/outputs backend/worktrees tmp

[ -f .env ] || echo "# OPENROUTER_API_KEY=sk-or-..." > .env

echo "=== Done. ==="
echo "1. Edit .env with your real OPENROUTER_API_KEY"
echo "2. Backend:  source venv/bin/activate && cd backend && uvicorn main:app --reload --port 8000"
echo "3. Frontend: npm run dev"
echo "4. Open http://localhost:1420"
