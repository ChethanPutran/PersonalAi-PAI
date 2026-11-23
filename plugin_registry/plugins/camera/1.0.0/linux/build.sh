# Build the Linux version of the plugin:
cd src/pai/plugin_registry/camera/1.0.0/linux/
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
cp build/libpai_camera.so ./libpai_camera.so
