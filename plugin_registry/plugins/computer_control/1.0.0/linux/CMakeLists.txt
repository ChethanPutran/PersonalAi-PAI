cmake_minimum_required(VERSION 3.18)
project(pai_computer_control CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(SHARED_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../../../shared")

find_package(PkgConfig REQUIRED)
pkg_check_modules(GIO REQUIRED gio-2.0)        # GDBus: the desktop portals, the accessibility bus status
pkg_check_modules(ATSPI REQUIRED atspi-2)      # the accessibility tree
pkg_check_modules(X11 REQUIRED x11 xtst)       # XTEST input, the active window and screen capture on an X11 session
find_package(ZLIB REQUIRED)                    # PNG compression for the X11 screen capture

option(PAI_CC_BUILD_TESTS "also build the unit-test executable" OFF)

# The pure logic: no platform libraries, so the unit tests need nothing but a compiler.
add_library(pai_cc_model STATIC src/cc_model.cpp)
target_include_directories(pai_cc_model PUBLIC src ${SHARED_DIR})
target_compile_options(pai_cc_model PRIVATE -fPIC)

add_library(pai_computer_control SHARED
    ${SHARED_DIR}/pai_system_helpers.cpp
    ${SHARED_DIR}/pai_portal.cpp
    ${SHARED_DIR}/pai_desktop_apps.cpp
    ${SHARED_DIR}/pai_screen_capture.cpp
    src/cc_atspi.cpp
    src/cc_x11.cpp
    src/cc_input.cpp
    src/computer_control_plugin.cpp
)
target_include_directories(pai_computer_control PRIVATE src ${SHARED_DIR}
    ${GIO_INCLUDE_DIRS} ${ATSPI_INCLUDE_DIRS} ${X11_INCLUDE_DIRS})
target_link_libraries(pai_computer_control PRIVATE pai_cc_model
    ${GIO_LIBRARIES} ${ATSPI_LIBRARIES} ${X11_LIBRARIES} ZLIB::ZLIB)
target_compile_options(pai_computer_control PRIVATE -Wall -Wextra)

set_target_properties(pai_computer_control PROPERTIES
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON
    PREFIX "libpai_"
    OUTPUT_NAME "computer_control"
)

target_link_options(pai_computer_control PRIVATE
    "-Wl,--export-dynamic"
    "-Wl,--no-undefined"
)

if(PAI_CC_BUILD_TESTS)
    add_executable(cc_unit_test tests/cc_unit_test.cpp ${SHARED_DIR}/pai_desktop_apps.cpp ${SHARED_DIR}/pai_system_helpers.cpp)
    target_include_directories(cc_unit_test PRIVATE src ${SHARED_DIR})
    target_link_libraries(cc_unit_test PRIVATE pai_cc_model)
endif()
