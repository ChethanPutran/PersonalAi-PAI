cmake_minimum_required(VERSION 3.18)
project(pai_desktop CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(SHARED_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../shared")

add_library(pai_desktop SHARED
    ${SHARED_DIR}/pai_system_helpers.cpp
    src/desktop_plugin.cpp
)

target_include_directories(pai_desktop PRIVATE ${SHARED_DIR})

set_target_properties(pai_desktop PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    PREFIX "libpai_"
    OUTPUT_NAME "desktop"
)

target_link_options(pai_desktop PRIVATE
    "-Wl,--export-dynamic"
    "-Wl,--no-undefined"
)