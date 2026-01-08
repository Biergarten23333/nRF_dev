/*
 * Sensor-Hub main.c  —— “等 Central 打开通知再发” + IMU / UWB / ECG 全功能版
 * nRF Connect SDK v2.8 / Zephyr 3.7.99
 *
 * ── 主要变动（仅蓝牙部分）───────────────────────────────────────────
 * · 取消对 NUS TX-CCC 的手动回调挂接，改为检测 bt_nus_send() 返回码
 * · 删除 can_send 变量，发送线程只检查 current_conn
 * · 发送线程遇到 -ENOTCONN / -EAGAIN / -EPIPE 都会把数据重新排队
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/util.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/uuid.h>
#include <bluetooth/services/nus.h>
#include <dk_buttons_and_leds.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys_clock.h>      /* ← for k_ticks_to_us_floor64() */
#include <math.h>
#include <stdio.h>
#include <string.h>

/* ───────────── BLE 参数 ───────────── */
#define DEVICE_NAME        "SENSOR-HUB"
#define DEVICE_NAME_LEN    (sizeof(DEVICE_NAME) - 1)

#define LED_RUN            DK_LED1
#define LED_CONN           DK_LED2

#define BLE_STACK_SIZE     1024
#define BLE_PRIORITY       7

#define BLE_BUF_SIZE       256
#define BLE_FIFO_MAX       32

static struct bt_conn *current_conn;

/* ─────────── NUS 回调 ─────────── */
static void nus_sent_cb(struct bt_conn *conn) { ARG_UNUSED(conn); }

static struct bt_nus_cb nus_cb = {
	.received = NULL,
	.sent     = nus_sent_cb,
};

/* ─── 广播 / 扫描应答数组放文件作用域 ─── */
static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA(BT_DATA_NAME_COMPLETE, DEVICE_NAME, DEVICE_NAME_LEN),
};

static const struct bt_data sd[] = {
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_NUS_VAL),
};

/* 信号量：BLE init 完成后释放 */
static K_SEM_DEFINE(ble_ready, 0, 1);

/* FIFO 节点 */
struct ble_item_t {
	void *fifo_reserved;
	char  data[BLE_BUF_SIZE];
};
static K_FIFO_DEFINE(fifo_ble);
static atomic_t fifo_cnt = ATOMIC_INIT(0);

/* ──────────── 时间戳 ──────────── */
static void timestamp_now(char *buf, size_t len)
{
	uint64_t us = k_ticks_to_us_floor64(k_uptime_ticks());   /* boot-time μs */
	uint32_t us_part = us % 1000000ULL;
	uint32_t sec_tot = (uint32_t)(us / 1000000ULL);

	uint32_t h  = (sec_tot / 3600U) % 24U;
	uint32_t m  = (sec_tot / 60U)   % 60U;
	uint32_t s  =  sec_tot          % 60U;
	uint32_t ms = us_part / 1000U;
	uint32_t us_rem = us_part % 1000U;

	snprintk(buf, len, "%02u:%02u:%02u.%03u%03u", h, m, s, ms, us_rem);
}

/* ────── SEND 宏：把字符串丢进 FIFO ────── */
#define SEND(tag, fmt, ...)                                                   \
	do {                                                                      \
		while (atomic_get(&fifo_cnt) >= BLE_FIFO_MAX) {                       \
			k_sleep(K_MSEC(5));                                              \
		}                                                                     \
		struct ble_item_t *it = k_malloc(sizeof(*it));                        \
		if (!it) break;                                                       \
		char _tsbuf[20];                                                      \
		timestamp_now(_tsbuf, sizeof _tsbuf);                                 \
		snprintk(it->data, BLE_BUF_SIZE, "[%s][%s] " fmt "\r\n",              \
		         _tsbuf, tag, ##__VA_ARGS__);                                 \
		k_fifo_put(&fifo_ble, it);                                            \
		atomic_inc(&fifo_cnt);                                                \
	} while (0)

/* ────────── 连接回调 ────────── */
static void connected(struct bt_conn *conn, uint8_t err)
{
	char addr[BT_ADDR_LE_STR_LEN];

	if (err) {
		printk("Conn failed (0x%02x)\n", err);
		return;
	}

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Connected: %s\n", addr);

	current_conn = bt_conn_ref(conn);
	dk_set_led_on(LED_CONN);
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	char addr[BT_ADDR_LE_STR_LEN];

	bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	printk("Disconnected: %s (0x%02x)\n", addr, reason);

	if (current_conn) {
		bt_conn_unref(current_conn);
		current_conn = NULL;
	}
	dk_set_led_off(LED_CONN);
}

BT_CONN_CB_DEFINE(conn_cb) = {
	.connected    = connected,
	.disconnected = disconnected,
};

/* ────────── BLE 初始化 ────────── */
static int ble_init(void)
{
	int err = bt_enable(NULL);
	if (err) { printk("BLE init fail %d\n", err); return err; }

	printk("Bluetooth ready\n");

	if (bt_nus_init(&nus_cb)) {
		printk("NUS init fail\n"); return -1;
	}

	err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad),
	                      sd, ARRAY_SIZE(sd));
	if (err) { printk("Adv start fail %d\n", err); return err; }

	printk("Advertising \"%s\"\n", DEVICE_NAME);

	dk_leds_init();
	dk_set_led_on(LED_RUN);

	k_sem_give(&ble_ready);
	return 0;
}

/* ──────────── 发送线程 ──────────── */
static void ble_tx_thread(void)
{
	k_sem_take(&ble_ready, K_FOREVER);
	printk("BLE-TX thread up\n");

	while (1) {
		struct ble_item_t *it = k_fifo_get(&fifo_ble, K_FOREVER);
		atomic_dec(&fifo_cnt);

		if (!current_conn) {
			/* 尚未连接：暂存，稍后再发 */
			k_fifo_put(&fifo_ble, it);
			atomic_inc(&fifo_cnt);
			k_sleep(K_MSEC(30));
			continue;
		}

		size_t len = strlen(it->data);
		int ret = bt_nus_send(current_conn, (uint8_t *)it->data, len);

		if (ret == -ENOMEM || ret == -EAGAIN ||
		    ret == -EINVAL  || ret == -ENOTCONN ||
		    ret == -EPIPE) {
			/* 通知尚未开启或缓冲区满——稍后重试 */
			k_fifo_put(&fifo_ble, it);
			atomic_inc(&fifo_cnt);
			k_sleep(K_MSEC(20));
			continue;
		}

		/* 发送成功 / 其他致命错误：释放内存 */
		k_free(it);
	}
}
K_THREAD_DEFINE(ble_tid, BLE_STACK_SIZE, ble_tx_thread,
		NULL, NULL, NULL, BLE_PRIORITY, 0, 0);

/* =================================================
 *                 IMU (MPU6050 200 Hz)
 * =================================================*/
#define MPU6050_NODE DT_INST(0, invensense_mpu6050)
static const struct device *mpu = DEVICE_DT_GET(MPU6050_NODE);
static struct sensor_trigger mpu_trig;

static void fmt_val(double v, char *out, size_t len)
{
	int32_t t = (int32_t)lround(v * 100);
	snprintk(out, len, "%d.%02d", t / 100, abs(t % 100));
}

static void mpu_ready(const struct device *dev,
                      const struct sensor_trigger *trig)
{
	struct sensor_value acc[3], gyr[3];
	sensor_sample_fetch_chan(dev, SENSOR_CHAN_ACCEL_XYZ);
	sensor_channel_get(dev, SENSOR_CHAN_ACCEL_XYZ, acc);
	sensor_sample_fetch_chan(dev, SENSOR_CHAN_GYRO_XYZ);
	sensor_channel_get(dev, SENSOR_CHAN_GYRO_XYZ,  gyr);

	static int cnt;
	if (++cnt >= 5) {       /* 200 Hz / 20 = 10 Hz 发一包 */
		cnt = 0;
		char ax[12], ay[12], az[12], gx[12], gy[12], gz[12];
		fmt_val(sensor_value_to_double(&acc[0]), ax, sizeof ax);
		fmt_val(sensor_value_to_double(&acc[1]), ay, sizeof ay);
		fmt_val(sensor_value_to_double(&acc[2]), az, sizeof az);
		fmt_val(sensor_value_to_double(&gyr[0]), gx, sizeof gx);
		fmt_val(sensor_value_to_double(&gyr[1]), gy, sizeof gy);
		fmt_val(sensor_value_to_double(&gyr[2]), gz, sizeof gz);
		SEND("IMU", "[%s,%s,%s,%s,%s,%s]",
		     ax, ay, az, gx, gy, gz);
	}
}

static void imu_init(void)
{
	if (!device_is_ready(mpu)) { printk("MPU6050 not found\n"); return; }

	struct sensor_value f = { .val1 = 200 };
	sensor_attr_set(mpu, SENSOR_CHAN_ACCEL_XYZ,
	                SENSOR_ATTR_SAMPLING_FREQUENCY, &f);
	sensor_attr_set(mpu, SENSOR_CHAN_GYRO_XYZ,
	                SENSOR_ATTR_SAMPLING_FREQUENCY, &f);

	mpu_trig.type = SENSOR_TRIG_DATA_READY;
	mpu_trig.chan = SENSOR_CHAN_ALL;
	sensor_trigger_set(mpu, &mpu_trig, mpu_ready);

	printk("MPU6050 OK\n");
}

/* =================================================
 *                   UWB 线程
 * =================================================*/
#define WAKE_BYTE 0x00
static const uint8_t CMD_LOC_GET[2] = {0x0C, 0x00};
static const uint8_t TLV_INT_EN[4]  = {0x34, 0x02, 0x01, 0x00};
#define UBUF_MAX 128

static const struct device *uart1 = DEVICE_DT_GET(DT_NODELABEL(uart1));
static const struct device *gpio1 = DEVICE_DT_GET(DT_NODELABEL(gpio1));
static struct gpio_callback uwb_cb;
static struct k_sem sem_uwb;

static void gpio_int(const struct device *d,
                     struct gpio_callback *cb, uint32_t pins)
{
	ARG_UNUSED(d); ARG_UNUSED(cb); ARG_UNUSED(pins);
	k_sem_give(&sem_uwb);
}

static inline void uart_write(const uint8_t *buf, size_t len)
{
	for (size_t i = 0; i < len; i++) uart_poll_out(uart1, buf[i]);
}

static void uwb_thread(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	if (!device_is_ready(uart1) || !device_is_ready(gpio1)) {
		printk("UWB uart/gpio not ready\n"); return;
	}

	gpio_pin_configure(gpio1, 3, GPIO_INPUT | GPIO_PULL_UP);
	gpio_pin_interrupt_configure(gpio1, 3, GPIO_INT_EDGE_RISING);
	gpio_init_callback(&uwb_cb, gpio_int, BIT(3));
	gpio_add_callback(gpio1, &uwb_cb);
	k_sem_init(&sem_uwb, 0, 1);

	/* wake + enable irq */
	for (int i = 0; i < 3; ++i) uart_poll_out(uart1, WAKE_BYTE);
	uart_write(TLV_INT_EN, sizeof TLV_INT_EN);
	k_sleep(K_MSEC(10));
	printk("UWB ready\n");

	uint8_t buf[UBUF_MAX];

	while (1) {
		k_sem_take(&sem_uwb, K_FOREVER);
		uart_write(CMD_LOC_GET, sizeof CMD_LOC_GET);

		size_t n = 0; uint8_t ch;
		int64_t dl = k_uptime_get() + 60;               /* 60 ms timeout */
		while (k_uptime_get() < dl && n < UBUF_MAX) {
			if (!uart_poll_in(uart1, &ch)) {
				buf[n++] = ch;
			}
		}

		/* 简单解析 TLV 0x41 位置数据 */
		for (size_t i = 0; i + 14 <= n; i++) {
			if (buf[i] == 0x41 && buf[i + 1] == 0x0D) { /* 长度 13 */
				const uint8_t *p = buf + i + 2;
				int32_t x = sys_get_le32(p);
				int32_t y = sys_get_le32(p + 4);
				int32_t z = sys_get_le32(p + 8);
				uint8_t  q = p[12];
				SEND("UWB", "[%d,%d,%d,%u]", x, y, z, q);
				goto done;
			}
		}
		SEND("UWB", "No POS");
done:
		;
	}
}
K_THREAD_DEFINE(uwb_tid, 1024, uwb_thread,
                NULL, NULL, NULL, 5, 0, 0);

/* =================================================
 *                   ECG 线程
 * =================================================*/
static const struct adc_dt_spec adc = ADC_DT_SPEC_GET(DT_PATH(zephyr_user));
static const struct device *gpio_ecg = DEVICE_DT_GET(DT_NODELABEL(gpio1));

#define ECG_RATE_HZ   200
#define ECG_INT_US    (1000000U / ECG_RATE_HZ)   /* 5 000 µs */
#define ECG_PKT_N     7                          /* 每包 8 个样本 */

static void ecg_thread(void *a, void *b, void *c)
{
    ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

    if (!device_is_ready(gpio_ecg)) { printk("ECG gpio!\n"); return; }
    gpio_pin_configure(gpio_ecg, 10, GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_configure(gpio_ecg, 11, GPIO_INPUT | GPIO_PULL_UP);

    if (!adc_is_ready_dt(&adc)) { printk("ADC not ready\n"); return; }
    adc_channel_setup_dt(&adc);

    int16_t sample;
    struct adc_sequence seq = {
        .buffer = &sample,
        .buffer_size = sizeof sample
    };
    adc_sequence_init_dt(&adc, &seq);

    printk("ECG ready (text, %d samples/line)\n", ECG_PKT_N);

    static int16_t buf[ECG_PKT_N];
    int idx = 0;

    while (1) {
        /* 导联掉线告警（保持文本格式） */
        if (gpio_pin_get(gpio_ecg, 4) && gpio_pin_get(gpio_ecg, 5)) {
            SEND("ECG", "Electrode off");
            idx = 0;                    /* 清空已收集样本 */
            k_sleep(K_MSEC(200));
            continue;
        }

        /* 采 1 点 */
        adc_read(adc.dev, &seq);
        buf[idx++] = sample;

        /* 集满 8 点 → 构造文本并立即发送 */
        if (idx >= ECG_PKT_N) {
            char line[96];              /* 足够容纳 8×“-32768,” + [] */
            int pos = 0;

            pos += snprintk(line + pos, sizeof(line) - pos, "[%d", buf[0]);
            for (int i = 1; i < ECG_PKT_N; i++) {
                pos += snprintk(line + pos, sizeof(line) - pos,
                                 ",%d", buf[i]);
            }
            snprintk(line + pos, sizeof(line) - pos, "]");

            SEND("E", "%s", line);
            idx = 0;
        }

        k_sleep(K_USEC(ECG_INT_US));    /* 5 ms → 200 Hz */
    }
}
K_THREAD_DEFINE(ecg_tid, 1024, ecg_thread,
                NULL, NULL, NULL, 6, 0, 0);


/* ────────── 主函数 ────────── */
int main(void)
{
	printk("\n=== Sensor-Hub boot ===\n");
	if (ble_init()) return 0;

	imu_init();                 /* 其余模块可根据需要启用/禁用 */
	/* uwb_thread / ecg_thread 已自动启动 */

	while (1) {
		dk_set_led(LED_RUN, 1);
		k_sleep(K_MSEC(500));
		dk_set_led(LED_RUN, 0);
		k_sleep(K_MSEC(500));
	}
}
