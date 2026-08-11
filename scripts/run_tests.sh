#!/bin/bash
set -e

# Activate virtual environment if any
# source venv/bin/activate

echo "Running unit tests with pytest..."
pytest tests/ -v --tb=short --asyncio-mode=auto

echo "Running with coverage..."
pytest tests/ --cov=src/pai --cov-report=term --cov-report=html

echo "Tests completed. Coverage report in htmlcov/"