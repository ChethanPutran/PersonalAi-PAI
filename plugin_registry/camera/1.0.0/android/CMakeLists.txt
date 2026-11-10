cmake_minimum_required(VERSION 3.18)
project(pai_camera_android CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(pai_camera SHARED
    src/camera_module.cpp
    src/camera_capture.cpp
    src/camera_record.cpp
)

target_include_directories(pai_camera PRIVATE include)

# camera2ndk gives us the NDK Camera2 API (ACameraManager etc.)
# mediandk provides AMediaCodec for future video recording.
# android provides AImageReader, ANativeWindow.
find_library(camera2ndk-lib camera2ndk)
find_library(mediandk-lib  mediandk)
find_library(android-lib   android)
find_library(log-lib       log)

target_link_libraries(pai_camera
    ${camera2ndk-lib}
    ${mediandk-lib}
    ${android-lib}
    ${log-lib}
)

set_target_properties(pai_camera PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    PREFIX "libpai_"
    OUTPUT_NAME "camera"
)