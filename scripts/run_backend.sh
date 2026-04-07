# Install dependencies
pip install -r requirements-backend.txt

# Run backend
python run_backend.py --host 0.0.0.0 --port 8000

# Or with Redis (optional)
docker run -d -p 6379:6379 redis
python run_backend.py --redis