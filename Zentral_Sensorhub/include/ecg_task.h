#ifndef ECG_TASK_H
#define ECG_TASK_H

#include <zephyr/sys/atomic.h>
#include "app_config.h"

/* BMD101 复位脚控制 */
void bmd101_hw_reset(void);

/* 统计（main 中 DEBUG 输出可用） */
extern atomic_t ecg_rx_overflows;
extern atomic_t ecg_rx_isr_cnt;

#endif /* ECG_TASK_H */
