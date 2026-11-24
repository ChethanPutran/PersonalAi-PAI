cmake_minimum_required(VERSION 3.18)
project(pai_input CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(SHARED_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../../shared")

find_package(PkgConfig REQUIRED)
pkg_check_modules(GIO REQUIRED gio-2.0)      # GDBus: talks to xdg-desktop-portal

add_library(pai_input SHARED
    ${SHARED_DIR}/pai_system_helpers.cpp
    ${SHARED_DIR}/pai_portal.cpp
    src/input_plugin.cpp
)

target_include_directories(pai_input PRIVATE ${SHARED_DIR} ${GIO_INCLUDE_DIRS})
target_link_libraries(pai_input PRIVATE ${GIO_LIBRARIES})

set_target_properties(pai_input PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    PREFIX "libpai_"
    OUTPUT_NAME "input"
)

target_link_options(pai_input PRIVATE
    "-Wl,--export-dynamic"
    "-Wl,--no-undefined"
)
