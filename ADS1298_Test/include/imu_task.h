/* src/imu_task.h */

#pragma once

int  imu_init(void);   /* 做一次 POST 等 */
void imu_start(void);  /* 让线程开始跑采样 */
void imu_set_rate_phase(uint16_t target_fps, uint16_t phase_ms);
