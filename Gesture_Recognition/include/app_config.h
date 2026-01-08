/* src/app_config.h
 *
 * Common includes + global config for eFxx TX (IMU + UWB).
 */

#pragma once

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/util.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/sys_io.h>
#include <zephyr/settings/settings.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <bluetooth/services/nus.h>
#include <dk_buttons_and_leds.h>
#include <math.h>

/* ========================= Global toggles ========================= */
#define DEBUG_LOG_ENABLE   1
#if DEBUG_LOG_ENABLE
  #define LOGF(...)  printk(__VA_ARGS__)
#else
  #define LOGF(...)  do {} while (0)
#endif

#define EN_BLE 1
#define EN_IMU 1
#define EN_UWB 1

/* ========================= RT settings ========================= */
#define BLE_PRIO   3
#define UWB_PRIO   4 //UWB is more prio than the IMU task
#define IMU_PRIO   5


#define BLE_STACK_SIZE  1280
#define IMU_STACK_SIZE  1536
#define UWB_STACK_SIZE  1536

#define BLE_BUF_SIZE    128

static inline uint32_t ts_ms(void)
{
    return (uint32_t)k_uptime_get_32();
}
