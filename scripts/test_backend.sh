# Health check
curl http://localhost:8000/

# Create session
curl -X POST http://localhost:8000/api/v1/session/create

# Test WebSocket (using wscat)
npm install -g wscat
wscat -c ws://localhost:8000/ws/your-session-id


# Test Voice Activity Detection
# Send test audio (requires audio file)
curl -X POST http://localhost:8000/api/v1/test/vad \
  -F "audio=@test.wav" \
  -F "session_id=test123"


# Test File Upload
curl -X POST http://localhost:8000/api/v1/upload \
  -F "session_id=test123" \
  -F "file=@document.pdf"


# Test Push Notification
curl -X POST http://localhost:8000/api/v1/notifications/test \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test123",
    "title": "Test",
    "body": "Hello from backend"
  }'