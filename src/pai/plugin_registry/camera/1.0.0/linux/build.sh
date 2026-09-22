# Build the Linux version of the plugin:
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
cp build/libpai_camera.so ./libpai_camera.so
