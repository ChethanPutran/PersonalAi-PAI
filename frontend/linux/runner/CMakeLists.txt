cmake_minimum_required(VERSION 3.13)
project(runner LANGUAGES CXX)

add_executable(${BINARY_NAME}
  "main.cc"
  "my_application.cc"
  "agent/bridge/plugin_channel.cc"
  "agent/permissions/permission_manager.cc"
  "agent/plugins/native_module_registry.cc"
  "agent/plugins/plugin_manager.cc"
  "agent/plugins/dynamic_loader.cc"
  "${FLUTTER_MANAGED_DIR}/generated_plugin_registrant.cc"
)

target_include_directories(${BINARY_NAME} PRIVATE
  "${FLUTTER_MANAGED_DIR}/cpp_client_wrapper/include"
)

target_link_libraries(${BINARY_NAME} PRIVATE flutter)
target_link_libraries(${BINARY_NAME} PRIVATE PkgConfig::GTK)
target_link_libraries(${BINARY_NAME} PRIVATE ${CMAKE_DL_LIBS})

apply_standard_settings(${BINARY_NAME})

add_definitions(-DAPPLICATION_ID="${APPLICATION_ID}")

target_include_directories(${BINARY_NAME} PRIVATE "${CMAKE_SOURCE_DIR}")