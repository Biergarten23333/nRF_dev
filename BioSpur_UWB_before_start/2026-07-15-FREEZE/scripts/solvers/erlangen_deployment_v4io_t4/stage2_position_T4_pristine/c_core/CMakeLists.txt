cmake_minimum_required(VERSION 3.16)
project(biospur_tagpos_c_core C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

add_library(biospur_tagpos SHARED
    src/tagpos_solver.c
)

target_include_directories(biospur_tagpos
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(biospur_tagpos PUBLIC m)

