#ifndef APP_CONFIG_H
#define APP_CONFIG_H

#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/atomic.h>

/* ========================= Global toggles ========================= */
#define DEBUG_LOG_ENABLE   0  /* 0=silent, 1=printk */
#if DEBUG_LOG_ENABLE
  #define LOGF(...)  printk(__VA_ARGS__)
#else
  #define LOGF(...)  do {} while (0)
#endif

#define EN_BLE             1
#define EN_IMU             1
#define EN_UWB             1
#define EN_ECG_RAW         1
#define EN_ECG_BPM         1
#define EN_ECG_SIGQ        1
#define BMD_OPEN_RAW_ON_BOOT 1
#define FORCE_Q_TEST   0

/* ========================= RT settings ========================= */
#define BLE_PRIO   3
#define IMU_PRIO   4
#define UWB_PRIO   3
#define ECG_PRIO   2

#define BLE_STACK_SIZE  1280
#define IMU_STACK_SIZE  1536
#define UWB_STACK_SIZE  1536
#define ECG_STACK_SIZE  2304

/* BLE send buffer & FIFO depth (tune to RAM budget) */
#define BLE_BUF_SIZE     128
#ifndef BLE_FIFO_MAX
#define BLE_FIFO_MAX     160
#endif

/* Helpers */
static inline uint32_t ts_ms(void)
{
    return (uint32_t)k_uptime_get_32();
}

#endif /* APP_CONFIG_H */
