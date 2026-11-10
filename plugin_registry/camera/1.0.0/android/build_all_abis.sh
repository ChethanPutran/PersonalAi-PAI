#!/usr/bin/env bash
set -euo pipefail

# Set the environment variables for the Android NDK
NDK="$ANDROID_HOME/ndk/$(ls "$ANDROID_HOME/ndk" | sort -V | tail -n1)"
TOOLCHAIN="$NDK/build/cmake/android.toolchain.cmake"
[ -f "$TOOLCHAIN" ] || { echo "NDK toolchain not found: $TOOLCHAIN"; exit 1; }

echo "Using NDK: $NDK"

for ABI in arm64-v8a armeabi-v7a x86_64; do
  echo "→ $ABI"
  rm -rf "build/$ABI"
  cmake -S . -B "build/$ABI" \
    -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN" \
    -DANDROID_ABI="$ABI" \
    -DANDROID_PLATFORM=android-24 \
    -DANDROID_STL=c++_shared \
    -DCMAKE_BUILD_TYPE=Release
  cmake --build "build/$ABI" -- -j"$(nproc)"
  mkdir -p "$ABI"
  cp "build/$ABI/libpai_camera.so" "$ABI/libpai_camera.so"
done

ls -la */libpai_camera.so