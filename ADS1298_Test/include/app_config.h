/* src/app_config.h
 *
 * Common includes + global config for ESxxxx TX (IMU + EMG).
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
#define EN_EMG 1
#define EN_UWB 0 // Temporarily disable UWB will be added later on next HW revision

/* ========================= RT settings ========================= */
#define BLE_PRIO   3 //BLE prio higher than IMU and EMG to ensure data flow
#define IMU_PRIO   5 
#define EMG_PRIO   4 // EMG prio higher than IMU to reduce latency
#define UWB_PRIO   4 // dont care for now



#define BLE_STACK_SIZE  1280
#define IMU_STACK_SIZE  1536
#define UWB_STACK_SIZE  1536
#define EMG_STACK_SIZE  2048
#define BLE_BUF_SIZE    256

/* EMG: 每个 BLE 帧里包含多少个时间点的 8 路样本
 * 可选：1 / 2 / 3 / 4
 */
#define EMG_SAMPLES_PER_FRAME   2 //compressed to 2 samples per frame for better throughput
#define EMG_SAMPLE_RATE_SPS     500

#if (EMG_SAMPLE_RATE_SPS != 250)  && \
    (EMG_SAMPLE_RATE_SPS != 500)  && \
    (EMG_SAMPLE_RATE_SPS != 1000) && \
    (EMG_SAMPLE_RATE_SPS != 2000) && \
    (EMG_SAMPLE_RATE_SPS != 4000)
#error "EMG_SAMPLE_RATE_SPS must be one of: 250/500/1000/2000/4000"
#endif

#if (EMG_SAMPLES_PER_FRAME < 1) || (EMG_SAMPLES_PER_FRAME > 4)
#error "EMG_SAMPLES_PER_FRAME must be 1..4"
#endif

static inline uint32_t ts_ms(void)
{
    return (uint32_t)k_uptime_get_32();
}
