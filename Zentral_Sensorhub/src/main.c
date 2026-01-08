#include <zephyr/kernel.h>
#include <dk_buttons_and_leds.h>

#include "app_config.h"
#include "device_id.h"
#include "ble_link.h"
#include "imu_task.h"
#include "uwb_task.h"
#include "ecg_task.h"

int main(void)
{
    init_dev_name_and_id();
    LOGF("\n=== %s TX boot ===\n", bt_name);

    /* BMD101 reset -> 打开 RAW / BPM / SigQ 前先硬复位一次 */
    bmd101_hw_reset();

    /* IMU 自检一次 */
    (void)imu_post_once();
    /* 放开 IMU 线程循环 */
    imu_start_loop();

    /* BLE 初始化（NUS + 广播 + DK LED1/2） */
    if (ble_init()) {
        /* BLE 初始化失败就直接停机 */
        return 0;
    }

    while (1) {
        if (EN_BLE) {
            dk_set_led_on(DK_LED1);
            k_sleep(K_MSEC(500));
            dk_set_led_off(DK_LED1);
        }

#if DEBUG_LOG_ENABLE
        /* Periodic stats: lane drops + ECG RX health */
        static uint32_t last_print = 0;
        if (ts_ms() - last_print > 2000) {
            last_print = ts_ms();
            uint32_t drops_norm = (uint32_t)atomic_get(&ble_drops_norm);
            uint32_t drops_hi   = (uint32_t)atomic_get(&ble_drops_hi);
            uint32_t oflow      = (uint32_t)atomic_get(&ecg_rx_overflows);
            uint32_t isrcnt     = (uint32_t)atomic_get(&ecg_rx_isr_cnt);
            LOGF("[STAT] drop_norm=%u drop_hi=%u ecg_of=%u ecg_isr_chunks=%u\n",
                 drops_norm, drops_hi, oflow, isrcnt);
        }
#endif
        k_sleep(K_MSEC(500));
    }
}
