cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(central)

target_sources(app PRIVATE
	src/main.c
	src/app_ble.c
	src/cdc_async.c
	src/tsync_master.c
	src/wdt_monitor.c
)

target_include_directories(app PRIVATE
	${CMAKE_CURRENT_SOURCE_DIR}/include
	${CMAKE_CURRENT_SOURCE_DIR}/../shared/include
)
