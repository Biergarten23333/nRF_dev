cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(tx_node)

target_sources(app PRIVATE
	src/main.c
	src/ble_link.c
	src/packet_framer.c
	src/tsync.c
	src/dfu_auth.c
	src/wdt_monitor.c
	src/imu_uart_driver.c
	src/uwb_driver.c
)

target_include_directories(app PRIVATE
	${CMAKE_CURRENT_SOURCE_DIR}/include
	${CMAKE_CURRENT_SOURCE_DIR}/../shared/include
)
