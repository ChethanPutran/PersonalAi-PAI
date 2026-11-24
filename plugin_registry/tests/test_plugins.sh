SHELL=plugins/shell/1.0.0/linux/artifact/libpai_shell.so
FILES=plugins/files/1.0.0/linux/artifact/libpai_files.so
SCREEN=plugins/screen/1.0.0/linux/artifact/libpai_screen.so
DESKTOP=plugins/desktop/1.0.0/linux/artifact/libpai_desktop.so
CAMERA=plugins/camera/1.0.0/linux/artifact/libpai_camera.so


# Setup: create test files inside an allowed root
mkdir -p /tmp/pai
head -c 500 /dev/urandom > /tmp/pai/bintest.bin        # binary with NULs
printf 'hello\n'      > /tmp/pai/small.txt             # 6-byte text


# 1. Shell — whitelist rejection delivers full map
python3 tools/pai_test.py "$SHELL" --capability shell.exec \
  --params '{"command":"rm -rf /"}'

# 2. Shell — default cwd should be HomeDir
python3 tools/pai_test.py "$SHELL" --capability shell.exec \
  --params '{"command":"pwd"}'

# 3. Files — write into non-existent root (tests PathInRoots Fix 5)
python3 tools/pai_test.py "$FILES" --capability files.write \
  --params '{"path":"/tmp/pai/smoketest/a.txt","content":"hello"}'

# 4. Files — binary detection (this is what you wanted to test)
python3 tools/pai_test.py "$FILES" --capability files.read \
  --params '{"path":"/tmp/pai/bintest.bin"}'
# Expected: err="binary_file", result has hint about binary=true

# 5. Files — binary with base64 opt-in
python3 tools/pai_test.py "$FILES" --capability files.read \
  --params '{"path":"/tmp/pai/bintest.bin","binary":"true"}'
# Expected: rc=0, result {ok:true, encoding:"base64", content:<non-empty>}

# 6. Files — max_bytes clamp (negative value)
python3 tools/pai_test.py "$FILES" --capability files.read \
  --params '{"path":"/tmp/pai/small.txt","max_bytes":"-1"}'
# Expected: err="file_too_large" — because max_bytes=-1 clamps to 1,
#           6 > 1, so rejected. Fix 7 verified.

# 6b. Files — max_bytes clamp sanity (non-negative value passes)
python3 tools/pai_test.py "$FILES" --capability files.read \
  --params '{"path":"/tmp/pai/small.txt","max_bytes":"100"}'
# Expected: rc=0, result {ok:true, content:"hello\n"}

# 7. Screen — outputPath validation (Fix 15)
PAI_SCREEN_CONSENT=1 python3 tools/pai_test.py "$SCREEN" \
  --capability screen.capture \
  --params '{"outputPath":"/etc/cron.d/evil"}'
# → err="outputPath_outside_allowed_roots", result has hint

# 8. Desktop — notifications.read is honest now
python3 tools/pai_test.py "$DESKTOP" --capability notifications.read
# → err="not_implemented", result has {ok:false,hint}

# 9. Camera — consistent error contract (Fix 13)
python3 tools/pai_test.py "$CAMERA" --capability camera.stream
# → err="stream_not_implemented", result has {ok:false,error}

# Now outputPath validation is reachable.
# Expect: err="outputPath_outside_allowed_roots", result has hint
PATH="/tmp/fakebin:$PATH" PAI_SCREEN_CONSENT=1 \
  python3 tools/pai_test.py "$SCREEN" \
  --capability screen.capture \
  --params '{"outputPath":"/etc/cron.d/evil"}'

# And an allowed path should succeed
PATH="/tmp/fakebin:$PATH" PAI_SCREEN_CONSENT=1 \
  python3 tools/pai_test.py "$SCREEN" \
  --capability screen.capture \
  --params '{"outputPath":"/tmp/pai/shot.png"}'
# Expected: rc=0, result {ok:true, path:"/tmp/pai/shot.png", tool:"grim", session:"..."}