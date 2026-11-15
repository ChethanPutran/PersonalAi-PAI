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

# camera2ndk — NDK Camera2 API (ACameraManager etc.)
# mediandk  — AMediaCodec for future video recording
# android   — AImageReader, ANativeWindow
# log       — __android_log_print
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

# ---------------------------------------------------------------------------
# Symbol export — MUST be set, or dlsym("pai_plugin_module_create") returns
# null after dlopen succeeds.
#
#   CXX_VISIBILITY_PRESET=hidden (below) makes -fvisibility=hidden the
#   default. That's correct — only the two ABI entry points should be
#   exported. They mark themselves `__attribute__((visibility("default")))`
#   in source.
#
#   --export-dynamic  — second safety net against --gc-sections (default
#   on Android Release). The entry points have no internal caller, only
#   dlsym() at runtime, so the linker considers them dead code. The
#   `used` attribute in source covers most cases; this flag guarantees it
#   regardless of linker version or future flag changes.
#
#   --no-undefined — fails the build if any external symbol is missing.
#   This is what would have caught the missing libc++_shared dependency
#   at build time instead of as a runtime dlopen failure.
#
target_link_options(pai_camera PRIVATE
    "-Wl,--export-dynamic"
    "-Wl,--no-undefined"
)

set_target_properties(pai_camera PROPERTIES
    # Entry points override this in source. Everything else is internal
    # and stays hidden from the host's dlsym.
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON

    # Output: libpai_camera.so  (PREFIX + OUTPUT_NAME)
    PREFIX "libpai_"
    OUTPUT_NAME "camera"
)
