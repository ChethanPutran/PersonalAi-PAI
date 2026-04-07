#!/usr/bin/env python3
"""
Start the OpenClaw backend server for mobile app
Usage: python run_backend.py --host 0.0.0.0 --port 8000
"""

import argparse
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

from backend.server import start_server

def main():
    parser = argparse.ArgumentParser(description="OpenClaw Mobile Backend")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--redis", action="store_true", help="Use Redis for session storage")
    
    args = parser.parse_args()
    
    # Set Redis flag if needed
    if args.redis:
        from backend.session_manager import session_manager
        session_manager.use_redis = True
    
    print(f"🚀 Starting OpenClaw Backend Server")
    print(f"📍 Host: {args.host}:{args.port}")
    print(f"📡 WebSocket URL: ws://{args.host}:{args.port}/ws/{{session_id}}")
    print(f"📚 API Docs: http://{args.host}:{args.port}/docs")
    print()
    
    start_server(host=args.host, port=args.port)

if __name__ == "__main__":
    main()