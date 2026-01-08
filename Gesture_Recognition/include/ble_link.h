/* src/ble_link.h */

#pragma once
#include <stdint.h>
#include <stdbool.h>

int   ble_link_init(void);
bool  ble_link_send(const uint8_t *buf, uint16_t len);
uint32_t ble_link_get_drop_count(void);

/* 时间同步：TX 端保存 host_time - local_time 的 offset */
void     ble_link_set_time_offset(int64_t offset_ms);
int64_t  ble_link_get_time_offset(void);

/* 返回“全局时间”（电脑时间系下的毫秒），= ts_ms() + offset */
uint32_t ble_link_now_host_ms(void);

/* 速率 / 时隙控制：给 IMU 下发 target_fps / phase_ms */
void imu_set_rate_phase(uint16_t target_fps, uint16_t phase_ms);
