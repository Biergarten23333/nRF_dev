/* src/ads1298.h */

#pragma once

#include <stdint.h>

/* 初始化 ADS1298 + SPI + GPIO + USB CDC + DRDY 监视线程
 *
 * 返回 0 表示成功，负值表示错误。
 */
int ads1298_init(void);

/* 启动 ADS1298 采样线程：
 *  - 内部会进入 while(1) 循环：
 *      * 等 DRDY
 *      * SPI 读出 8 路 24bit 数据
 *      * 组装 0xAA 帧（31B）
 *      * 通过 USB CDC 发出去
 *      * RTT 每 N 帧打印一次调试信息
 */
void ads1298_start(void);

/* 可选：注册一个“每帧 EMG 数据”的回调。
 *
 * 这个回调在采样线程里被调用：
 *   callback(ch_code[8], status[3]);
 *
 * 你以后要加：
 *   - BLE NUS 发送 EMG
 *   - 或者本地做特征提取
 * 就可以在外面实现一个函数，然后通过这里注册进去。
 */
typedef void (*ads1298_frame_cb_t)(const int32_t ch_code[8],
                                   const uint8_t status[3]);

void ads1298_set_frame_callback(ads1298_frame_cb_t cb);
