#!/usr/bin/env python3
import json
import time

try:
    from websocket import create_connection
except Exception as e:
    print('websocket-client not installed:', e)
    raise

WS_URL = 'ws://127.0.0.1:8000/ws'
print('Connecting to', WS_URL)
ws = create_connection(WS_URL, timeout=10)
print('Connected')

payload = {
    'type': 'register_device',
    'payload': {
        'user_id': 'me',
        'device_id': 'cli-test-1',
        'device_name': 'CLI Test Device',
        'device_type': 'desktop',
        'platform': 'python-test',
        'capabilities': {'input': 'keyboard', 'output': 'screen'},
        'config': {}
    }
}
print('Sending:', json.dumps(payload))
ws.send(json.dumps(payload))

# wait for server response(s)
try:
    for _ in range(3):
        resp = ws.recv()
        print('Received:', resp)
        time.sleep(0.2)
except Exception as e:
    print('Receive error or no more messages:', e)

ws.close()
print('Closed')
