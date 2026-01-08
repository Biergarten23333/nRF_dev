/* src/main.c */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>

/* 在编译期从 devicetree 拿到 gpio1 设备指针 */
static const struct device *gpio1_dev =
    DEVICE_DT_GET(DT_NODELABEL(gpio1));

/* 从 devicetree 拿到我们只用了第 0 路 io-channel 的 ADC 配置 */
static const struct adc_dt_spec adc = ADC_DT_SPEC_GET(DT_PATH(zephyr_user));

#define SAMPLING_RATE_HZ     250
#define SAMPLING_INTERVAL_US (1000000U / SAMPLING_RATE_HZ)

int main(void)
{
    int err;
    int16_t sample;

    /* 1) 检查 GPIO1 设备 */
    if (!device_is_ready(gpio1_dev)) {
        printk("ERROR: gpio1 not ready\n");
        return 0;
    }
    /* 配置 P1.04 和 P1.05 为 输入 + 上拉 */
    gpio_pin_configure(gpio1_dev, 4, GPIO_INPUT | GPIO_PULL_UP);
    gpio_pin_configure(gpio1_dev, 5, GPIO_INPUT | GPIO_PULL_UP);

    /* 2) 检查 ADC 设备并 setup */
    if (!adc_is_ready_dt(&adc)) {
        printk("ERROR: ADC %s not ready\n", adc.dev->name);
        return 0;
    }
    err = adc_channel_setup_dt(&adc);
    if (err < 0) {
        printk("ERROR: ADC setup failed (%d)\n", err);
        return 0;
    }

    /* 3) 准备好异步单样本读取 */
    struct adc_sequence_options options = {
        .interval_us     = SAMPLING_INTERVAL_US,
        .extra_samplings = 0,  /* 每次只读 1 个样本 */
    };
    struct adc_sequence sequence = {
        .options       = &options,
        .buffer        = &sample,
        .buffer_size   = sizeof(sample),
    };
    adc_sequence_init_dt(&adc, &sequence);

    /* 4) 用 k_poll_signal 等待异步完成 */
    static struct k_poll_signal signal;
    k_poll_signal_init(&signal);
    struct k_poll_event event = K_POLL_EVENT_INITIALIZER(
        K_POLL_TYPE_SIGNAL, K_POLL_MODE_NOTIFY_ONLY, &signal);

    while (1) {
        /* 4a) 读极化脚，若都为高就判定“极化脱落” */
        bool pole_off = gpio_pin_get(gpio1_dev, 4)
                      && gpio_pin_get(gpio1_dev, 5);
        if (pole_off) {
            printk("ECG POLE OFF\n");
            k_busy_wait(SAMPLING_INTERVAL_US);
            continue;
        }

        /* 4b) 正常通道：触发一次异步 ADC 采样 */
        err = adc_read_async(adc.dev, &sequence, &signal);
        if (err < 0) {
            printk("ERROR: ADC async start failed (%d)\n", err);
            return 0;
        }
        k_poll(&event, 1, K_FOREVER);

        /* 4c) 打印 ECG 原始值 */
        printk(">ECG:%d\n", sample);

        /* 4d) 清除 signal，进入下一轮 */
        {
            unsigned int signaled;
            int result;
            k_poll_signal_check(&signal, &signaled, &result);
        }
    }

    return 0;
}
