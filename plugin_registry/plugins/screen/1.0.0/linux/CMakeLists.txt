cmake_minimum_required(VERSION 3.18)
project(pai_screen CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(SHARED_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../shared")

add_library(pai_screen SHARED
    ${SHARED_DIR}/pai_system_helpers.cpp
    src/screen_plugin.cpp
)

target_include_directories(pai_screen PRIVATE ${SHARED_DIR})

set_target_properties(pai_screen PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    PREFIX "libpai_"
    OUTPUT_NAME "screen"
)

target_link_options(pai_screen PRIVATE
    "-Wl,--export-dynamic"
    "-Wl,--no-undefined"
)