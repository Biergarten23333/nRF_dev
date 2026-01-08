#ifndef APP_CMD_H
#define APP_CMD_H

#include <stdint.h>
#include <stdbool.h>

/* 启动 CDC 命令解析线程（scan / conn / reboot / TSYNC / RSLOT ...） */
int app_cmd_init(void);

/* 来自各个 TX 节点的 NUS 数据（已经按 dev_id 区分）。*/
void app_cmd_on_nus_rx(uint16_t dev_id, const uint8_t *data, uint16_t len);

/* 扫描时发现/更新设备，用于在 USB CDC 上打印扫描列表。*/
void app_cmd_scan_update(uint16_t dev_id,
                         uint8_t filterindex,
                         const char *name,
                         int8_t rssi,
                         bool is_new);

#endif /* APP_CMD_H */
