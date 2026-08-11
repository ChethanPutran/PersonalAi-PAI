#!/bin/bash

# Start the Personal AI System

set -e

echo "Starting Personal AI System..."

# Check if Docker is available
if command -v docker &> /dev/null && command -v docker-compose &> /dev/null; then
    echo "Starting with Docker Compose..."
    docker-compose -f docker/docker-compose.yml up --build
else
    echo "Docker not found. Starting locally..."
    
    # Install dependencies
    pip install -r requirements.txt
    
    # Run the API server
    python -m pai.main
fi