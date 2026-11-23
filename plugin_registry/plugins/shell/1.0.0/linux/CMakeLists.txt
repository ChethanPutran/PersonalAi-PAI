cmake_minimum_required(VERSION 3.18)
project(pai_shell CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(SHARED_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../shared")

add_library(pai_shell SHARED
    ${SHARED_DIR}/pai_system_helpers.cpp
    src/shell_plugin.cpp
)

target_include_directories(pai_shell PRIVATE ${SHARED_DIR})

set_target_properties(pai_shell PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    PREFIX "libpai_"
    OUTPUT_NAME "shell"
)

target_link_options(pai_shell PRIVATE
    "-Wl,--export-dynamic"
    "-Wl,--no-undefined"
)