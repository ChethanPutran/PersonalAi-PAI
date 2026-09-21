cmake_minimum_required(VERSION 3.18)
project(pai_camera CXX)

add_library(pai_camera SHARED
    src/camera_module.cpp
)

target_include_directories(pai_camera PRIVATE include)
target_compile_features(pai_camera PRIVATE cxx_std_17)

# Android-specific: link JNI + log so the plugin can use Android APIs.
if (ANDROID)
    find_library(log-lib log)
    find_library(jnigraphics-lib jnigraphics)
    target_link_libraries(pai_camera ${log-lib} ${jnigraphics-lib})
endif()

set_target_properties(pai_camera PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    PREFIX "libpai_"
)