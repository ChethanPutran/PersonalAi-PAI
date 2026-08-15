#!/usr/bin/env python3
import socket
import base64
import hashlib
import os
import struct
import json
import time

HOST = '127.0.0.1'
PORT = 8000
PATH = '/ws'

def create_handshake_key():
    key = os.urandom(16)
    return base64.b64encode(key).decode('ascii')

def build_handshake(key):
    return (
        f"GET {PATH} HTTP/1.1\r\n"
        f"Host: {HOST}:{PORT}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )

# minimal frame builder for client (masked)
def build_text_frame(message: bytes):
    # FIN + text
    b1 = 0x81
    length = len(message)
    mask_bit = 0x80
    if length <= 125:
        b2 = mask_bit | length
        header = struct.pack('!BB', b1, b2)
    elif length < 65536:
        b2 = mask_bit | 126
        header = struct.pack('!BBH', b1, b2, length)
    else:
        b2 = mask_bit | 127
        header = struct.pack('!BBQ', b1, b2, length)
    mask = os.urandom(4)
    masked = bytearray(message)
    for i in range(len(masked)):
        masked[i] ^= mask[i % 4]
    return header + mask + masked

# minimal frame parser for server frames (unmasked)
def recv_frame(sock):
    # read 2 bytes
    hdr = sock.recv(2)
    if not hdr or len(hdr) < 2:
        return None
    b1, b2 = hdr[0], hdr[1]
    fin = (b1 >> 7) & 1
    opcode = b1 & 0x0f
    masked = (b2 >> 7) & 1
    payload_len = b2 & 0x7f
    if payload_len == 126:
        ext = sock.recv(2)
        payload_len = struct.unpack('!H', ext)[0]
    elif payload_len == 127:
        ext = sock.recv(8)
        payload_len = struct.unpack('!Q', ext)[0]
    if masked:
        mask = sock.recv(4)
    data = b''
    remaining = payload_len
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        data += chunk
        remaining -= len(chunk)
    if masked:
        # unmask
        unmasked = bytearray(data)
        for i in range(len(unmasked)):
            unmasked[i] ^= mask[i % 4]
        data = bytes(unmasked)
    return opcode, data


def main():
    key = create_handshake_key()
    hs = build_handshake(key)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect((HOST, PORT))
    s.send(hs.encode('utf8'))
    # read HTTP response headers
    resp = b''
    while True:
        chunk = s.recv(1024)
        if not chunk:
            break
        resp += chunk
        if b"\r\n\r\n" in resp:
            break
    print('Handshake response:')
    print(resp.decode(errors='ignore'))

    # send register_device payload
    payload = {
        'type': 'register_device',
        'payload': {
            'user_id': 'me',
            'device_id': 'raw-cli-1',
            'device_name': 'Raw CLI Device',
            'device_type': 'desktop',
            'platform': 'raw-python',
            'capabilities': {'input': 'keyboard', 'output': 'screen'},
            'config': {}
        }
    }
    msg = json.dumps(payload).encode('utf8')
    frame = build_text_frame(msg)
    s.send(frame)
    print('Sent register_device; waiting for response...')
    # read a few frames
    s.settimeout(2)
    try:
        for _ in range(3):
            res = recv_frame(s)
            if res is None:
                break
            opcode, data = res
            if opcode == 1:
                print('Received text frame:', data.decode('utf8', errors='ignore'))
            else:
                print('Received opcode', opcode)
    except Exception as e:
        print('Receive error:', e)
    s.close()

if __name__ == '__main__':
    main()
