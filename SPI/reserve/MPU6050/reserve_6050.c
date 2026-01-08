/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * MPU6050 中断触发 + 200 Hz 输出示例
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/sys/printk.h>
#include <zephyr/devicetree.h>

/* DeviceTree 中第 0 个 invensense,mpu6050 */
#define MPU6050_NODE DT_INST(0, invensense_mpu6050)
static const struct device *mpu = DEVICE_DT_GET(MPU6050_NODE);

/* 触发器对象 */
static struct sensor_trigger mpu_trig;

/* 数据就绪回调 */
static void mpu_data_ready(const struct device *dev,
                           const struct sensor_trigger *trig)
{
    struct sensor_value accel[3], gyro[3], temp;

    /* 拉取并打印三路数据 */
    sensor_sample_fetch_chan(dev, SENSOR_CHAN_ACCEL_XYZ);
    sensor_channel_get(dev, SENSOR_CHAN_ACCEL_XYZ, accel);

    sensor_sample_fetch_chan(dev, SENSOR_CHAN_GYRO_XYZ);
    sensor_channel_get(dev, SENSOR_CHAN_GYRO_XYZ, gyro);

    sensor_sample_fetch_chan(dev, SENSOR_CHAN_DIE_TEMP);
    sensor_channel_get(dev, SENSOR_CHAN_DIE_TEMP, &temp);

    printk("ACC: %d.%06d %d.%06d %d.%06d | ",
           accel[0].val1, accel[0].val2,
           accel[1].val1, accel[1].val2,
           accel[2].val1, accel[2].val2);
    printk("GYRO:%d.%06d %d.%06d %d.%06d | ",
           gyro[0].val1, gyro[0].val2,
           gyro[1].val1, gyro[1].val2,
           gyro[2].val1, gyro[2].val2);
    printk("TEMP:%d.%06d C\n",
           temp.val1, temp.val2);
}

void main(void)
{
    int rc;

    if (!device_is_ready(mpu)) {
        printk("MPU6050 device not ready\n");
        return;
    }

    /* 1) 动态设置 200 Hz 输出 */
    struct sensor_value freq = { .val1 = 200, .val2 = 0 };
    rc = sensor_attr_set(mpu, SENSOR_CHAN_ACCEL_XYZ,
                         SENSOR_ATTR_SAMPLING_FREQUENCY, &freq);
    if (rc) {
        printk("Failed to set accel freq: %d\n", rc);
    }
    rc = sensor_attr_set(mpu, SENSOR_CHAN_GYRO_XYZ,
                         SENSOR_ATTR_SAMPLING_FREQUENCY, &freq);
    if (rc) {
        printk("Failed to set gyro freq: %d\n", rc);
    }
    printk("Sampling frequency set to %d Hz\n", freq.val1);

    /* 2) 配置中断触发 */
    mpu_trig.type = SENSOR_TRIG_DATA_READY;
    mpu_trig.chan = SENSOR_CHAN_ALL;
    rc = sensor_trigger_set(mpu, &mpu_trig, mpu_data_ready);
    if (rc) {
        printk("Cannot set trigger: %d\n", rc);
        return;
    }



    /* 3) 主线程空转，所有采样在中断回调里完成 */
    while (1) {
        k_sleep(K_SECONDS(1));
    }
}
