set -a
source .env
set +a
PYTHONPATH=src uvicorn pai.main:app --reload --host 0.0.0.0 --port 8000