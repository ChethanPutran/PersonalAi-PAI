cmake_minimum_required(VERSION 3.18)
project(pai_agent_host)

add_library(pai_agent_host SHARED
    native_loader.cpp
)

find_library(log-lib log)
target_link_libraries(pai_agent_host
    ${log-lib}
    dl
)