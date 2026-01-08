/* src/main.c */

#include "app_config.h"
#include "device_id.h"
#include "ble_link.h"
#include "imu_task.h"
#include "uwb_task.h"

int main(void)
{
    device_id_init();
    LOGF("\n=== %s TX boot (IMU+UWB only, NO SLEEP) ===\n",
         device_bt_name_get());

    /* IMU：做一次 POST，然后启动采样线程 */
    (void)imu_init();      /* 原来的 imu_post_once() */
    imu_start();           /* 相当于 k_sem_give(&imu_start_sem) */

    /* BLE link */
    if (ble_link_init()) {
        /* BLE 起不来就直接退出 main（保持跟你原来逻辑一致） */
        return 0;
    }

    /* UWB：现在真正干活的是 uwb_task.c 里的 uwb_thread（K_THREAD_DEFINE）；
     * 这里的 uwb_init_and_start 只是一个占位接口，方便以后扩展。
     * 目前调用它不会再定义任何栈 / 线程对象，所以不会产生重复定义。
     */
    (void)uwb_init_and_start();

    while (1) {
#if DEBUG_LOG_ENABLE
        static uint32_t last_print = 0;
        if (ts_ms() - last_print > 2000U) {
            last_print = ts_ms();
            uint32_t drops_norm = ble_link_get_drop_count();
            LOGF("[STAT] drop_norm=%u\n", drops_norm);
        }
#endif
        dk_set_led_on(DK_LED1);
        k_sleep(K_MSEC(200));
        dk_set_led_off(DK_LED1);
        k_sleep(K_MSEC(800));
    }
}
//---//