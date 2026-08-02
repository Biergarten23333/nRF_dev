#ifndef BIOSPUR_IMU_H
#define BIOSPUR_IMU_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "biospur_fusion_ble.h"

typedef int (*bsf_imu_publish_fn)(const void *record, size_t len);

struct bsf_imu_stats {
	uint32_t pulls;
	/* I2C polls completed before the next B306-clock sample deadline. */
	uint32_t repeated_chip_polls;
	uint32_t fresh_frames;
	uint32_t equal_motion_frames;
	uint32_t incoherent_reads;
	uint32_t missed_chip_frames;
	uint32_t i2c_errors;
	uint32_t records;
	uint16_t rate_hz;
	uint16_t last_chip_ms;
	uint8_t batch_size;
	uint8_t active;
	uint8_t have_chip_ms;
	uint8_t verify_pass;
	uint16_t gyrocalithr;
	uint16_t gyrocalitime;
	uint16_t rrate;
	uint16_t bandwidth;
	uint8_t health_class;
	uint8_t health_active;
	uint8_t health_latched;
	uint8_t extended_burst;
	uint32_t health_reset;
	uint32_t health_frozen;
	uint32_t health_rate;
	uint32_t health_canary;
	uint32_t health_plausibility;
	uint32_t health_dead;
	uint32_t health_identical;
	uint32_t health_i2c_escalation;
	uint32_t health_recover_ok;
	uint32_t health_recover_fail;
	uint32_t legacy_pull_mean_us;
	uint32_t extended_pull_mean_us;
	uint64_t last_good_ts_us;
	uint64_t fault_ts_us;
	uint64_t recovered_ts_us;
	uint32_t pull_lateness_max_us;
	uint32_t pull_duration_max_us;
};

int bsf_imu_init(bsf_imu_publish_fn publish);
int bsf_imu_start(char *reply, size_t reply_size);
int bsf_imu_stop(void);
int bsf_imu_set_rate(uint16_t rate_hz);
int bsf_imu_set_batch(uint8_t batch_size);
int bsf_imu_set_rrate_runtime(uint16_t rrate, char *reply,
			      size_t reply_size);
int bsf_imu_set_bandwidth_runtime(uint16_t bandwidth, char *reply,
				  size_t reply_size);
int bsf_imu_reg_read(uint8_t reg, char *reply, size_t reply_size);
int bsf_imu_reg_write(uint8_t reg, uint16_t value, char *reply,
		      size_t reply_size);
int bsf_imu_provision(char *reply, size_t reply_size);
int bsf_imu_cal_acc(char *reply, size_t reply_size);
int bsf_imu_selftest(char *reply, size_t reply_size);
void bsf_imu_format_status(char *reply, size_t reply_size);
void bsf_imu_format_latency(char *reply, size_t reply_size);
int bsf_imu_format_delta_page(uint8_t page, char *reply, size_t reply_size);
void bsf_imu_format_pull_summary(char *reply, size_t reply_size);
int bsf_imu_format_pull_hist_page(bool duration, uint8_t page, char *reply,
				  size_t reply_size);
void bsf_imu_format_stop(char *reply, size_t reply_size, int stop_result);
void bsf_imu_get_stats(struct bsf_imu_stats *stats);
void bsf_imu_clear_counters(void);

#endif /* BIOSPUR_IMU_H */
