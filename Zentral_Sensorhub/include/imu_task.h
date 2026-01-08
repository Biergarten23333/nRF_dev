#ifndef IMU_TASK_H
#define IMU_TASK_H

#include <zephyr/kernel.h>
#include "app_config.h"

/* 上电后做一次 POST（可选） */
int imu_post_once(void);

/* main 中调用，释放内部的 imu_start_sem，开始 IMU 循环 */
void imu_start_loop(void);

#endif /* IMU_TASK_H */
