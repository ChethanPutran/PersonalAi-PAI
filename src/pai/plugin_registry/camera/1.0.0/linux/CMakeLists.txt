cmake_minimum_required(VERSION 3.18)
project(pai_camera_linux CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(pai_camera SHARED
    src/camera_module.cpp
    src/camera_capture.cpp
    src/camera_record.cpp
)

target_include_directories(pai_camera PRIVATE include)

# Hidden visibility — only pai_plugin_module_create/destroy are exported.
set_target_properties(pai_camera PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    PREFIX "libpai_"
    OUTPUT_NAME "camera"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
)

# V4L2 uses <linux/videodev2.h> from libc headers; no extra lib needed.