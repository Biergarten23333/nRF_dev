#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/sys/printk.h>
#include <zephyr/usb/usb_device.h>
#include <zephyr/drivers/uart.h>

static struct bt_conn *default_conn;
static const struct bt_data ad_scan[] = {
    BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_NO_BREDR),
    BT_DATA_BYTES(BT_DATA_UUID16_ALL, BT_UUID_16_ENCODE(BT_UUID_HRS_VAL)),
};

const struct device *uart_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));

// 扫描回调
static void scan_cb(const bt_addr_le_t *addr, int8_t rssi, uint8_t adv_type,
                    struct net_buf_simple *buf) {
    char addr_str[BT_ADDR_LE_STR_LEN];
    bt_addr_le_to_str(addr, addr_str, sizeof(addr_str));
    printk("发现设备: %s\n", addr_str);

    // 自动连接第一个发现的设备
    if (!default_conn) {
        bt_conn_le_create(addr, BT_CONN_LE_CREATE_CONN, BT_LE_CONN_PARAM_DEFAULT, &default_conn);
    }
}

// 连接成功回调
static void connected(struct bt_conn *conn, uint8_t err) {
    if (err) {
        printk("连接失败 (错误码 %d)\n", err);
        return;
    }
    printk("已连接\n");
    default_conn = conn;
}

// 断开连接回调
static void disconnected(struct bt_conn *conn, uint8_t reason) {
    printk("断开连接 (原因: 0x%02x)\n", reason);
    default_conn = NULL;
}

// 订阅通知回调
static uint8_t notify_cb(struct bt_conn *conn, struct bt_gatt_subscribe_params *params,
                         const void *data, uint16_t length) {
    if (!data) return BT_GATT_ITER_STOP;
    printk("收到数据: %s\n", (const char *)data);
    uart_fifo_fill(uart_dev, data, length);
    return BT_GATT_ITER_CONTINUE;
}

int main(void) {
    // 初始化 USB 串口
    if (usb_enable(NULL)) return -1;
    while (!device_is_ready(uart_dev)) { k_sleep(K_MSEC(100)); }

    // 初始化 BLE
    bt_enable(NULL);
    struct bt_conn_cb conn_callbacks = {
        .connected = connected,
        .disconnected = disconnected,
    };
    bt_conn_cb_register(&conn_callbacks);

    // 启动扫描
    struct bt_le_scan_param scan_params = {
        .type = BT_LE_SCAN_TYPE_PASSIVE,
        .options = BT_LE_SCAN_OPT_NONE,
        .interval = BT_GAP_SCAN_FAST_INTERVAL,
        .window = BT_GAP_SCAN_FAST_WINDOW,
    };
    int err = bt_le_scan_start(&scan_params, scan_cb);
    if (err) printk("扫描失败 (错误码 %d)\n", err);

    // 等待连接并订阅
    while (1) {
        if (default_conn) {
            struct bt_gatt_subscribe_params subscribe_params = {
                .notify = notify_cb,
                .value = BT_UUID_HRS_MEASUREMENT, // 替换为你的特征 UUID
                .ccc_handle = 0x0000,             // 替换为 CCC 句柄
            };
            bt_gatt_subscribe(default_conn, &subscribe_params);
            break;
        }
        k_sleep(K_MSEC(1000));
    }

    return 0;
}