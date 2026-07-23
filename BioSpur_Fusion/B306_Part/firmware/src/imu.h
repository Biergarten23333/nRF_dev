#ifndef BIOSPUR_IMU_H
#define BIOSPUR_IMU_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "biospur_fusion_ble.h"

typedef int (*bsf_imu_publish_fn)(const void *record, size_t len);

struct bsf_imu_stats {
	uint32_t pulls;
	uint32_t duplicate_samples;
	uint32_t i2c_errors;
	uint32_t records;
	uint16_t rate_hz;
	uint8_t batch_size;
	uint8_t active;
	uint8_t verify_pass;
	uint16_t gyrocalithr;
	uint16_t gyrocalitime;
	uint16_t rrate;
	uint16_t bandwidth;
};

int bsf_imu_init(bsf_imu_publish_fn publish);
int bsf_imu_start(void);
int bsf_imu_stop(void);
int bsf_imu_set_rate(uint16_t rate_hz);
int bsf_imu_set_batch(uint8_t batch_size);
int bsf_imu_provision(char *reply, size_t reply_size);
int bsf_imu_cal_acc(char *reply, size_t reply_size);
int bsf_imu_selftest(char *reply, size_t reply_size);
void bsf_imu_format_status(char *reply, size_t reply_size);
void bsf_imu_get_stats(struct bsf_imu_stats *stats);
void bsf_imu_clear_counters(void);

#endif /* BIOSPUR_IMU_H */
