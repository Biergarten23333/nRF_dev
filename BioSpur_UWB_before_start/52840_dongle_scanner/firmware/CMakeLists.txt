cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(biospur_ble_scanner)

zephyr_compile_definitions(NRF52840_XXAA)
target_compile_definitions(app PRIVATE
  NRFX_CLOCK_ENABLED=1
  NRFX_POWER_ENABLED=1
  NRFX_PPI_ENABLED=1
  NRFX_GPIOTE_ENABLED=1
  NRFX_GPIOTE0_ENABLED=1
)
target_sources(app PRIVATE src/main.c)
target_sources(app PRIVATE
  $ENV{ZEPHYR_BASE}/../modules/hal/nordic/nrfx/mdk/system_nrf52.c
  $ENV{ZEPHYR_BASE}/modules/hal_nordic/nrfx/nrfx_glue.c
  $ENV{ZEPHYR_BASE}/../modules/hal/nordic/nrfx/drivers/src/nrfx_clock.c
  $ENV{ZEPHYR_BASE}/../modules/hal/nordic/nrfx/drivers/src/nrfx_power.c
  $ENV{ZEPHYR_BASE}/../modules/hal/nordic/nrfx/drivers/src/nrfx_gpiote.c
  $ENV{ZEPHYR_BASE}/../modules/hal/nordic/nrfx/drivers/src/nrfx_ppi.c
  $ENV{ZEPHYR_BASE}/../modules/hal/nordic/nrfx/helpers/nrfx_gppi_ppi.c
  $ENV{ZEPHYR_BASE}/../modules/hal/nordic/nrfx/helpers/nrfx_flag32_allocator.c
)
zephyr_include_directories(
  $ENV{ZEPHYR_BASE}/modules/cmsis
  $ENV{ZEPHYR_BASE}/../modules/hal/cmsis/CMSIS/Core/Include
  $ENV{ZEPHYR_BASE}/modules/hal_nordic/nrfx
  $ENV{ZEPHYR_BASE}/../modules/hal/nordic/nrfx
  $ENV{ZEPHYR_BASE}/../modules/hal/nordic/nrfx/mdk
  $ENV{ZEPHYR_BASE}/../modules/hal/nordic/nrfx/drivers/include
)
