fuser -k 8000/tcp 2>/dev/null; 
sleep 1; 
set -a; 
source .env; 
set +a; 
PYTHONPATH=src 
uv run pai
