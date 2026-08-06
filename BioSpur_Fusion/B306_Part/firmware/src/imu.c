#include "imu.h"

#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/dt-bindings/i2c/i2c.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/time_units.h>
#include <zephyr/sys/util.h>

#include "imu_delta_math.h"
#include "imu_pull_diag_math.h"
#include "strobe_capture.h"

LOG_MODULE_DECLARE(biospur_fusion);

#define IMU_I2C_NODE DT_ALIAS(imu_i2c)
#define JY61P_I2C_ADDRESS 0x50u
#define JY61P_FRAME_REGISTER 0x34u
#define JY61P_FRAME_LENGTH 26u
#define JY61P_EXTENDED_REGISTER 0x30u
#define JY61P_EXTENDED_LENGTH 34u
#define JY61P_EXTENDED_FRAME_OFFSET 8u
#define JY61P_CHIP_TIME_LENGTH 8u
#define JY61P_ACC_OFFSET 0u
#define JY61P_GYRO_OFFSET 6u
#define JY61P_TEMP_OFFSET 24u

#define JY61P_REG_SAVE       0x00u
#define JY61P_REG_CALSW      0x01u
#define JY61P_REG_RRATE      0x03u
#define JY61P_REG_BANDWIDTH  0x1fu
#define JY61P_REG_GYROCALITHR 0x61u
#define JY61P_REG_GYROCALTIME 0x63u
#define JY61P_REG_UNLOCK     0x69u

#define JY61P_UNLOCK_VALUE      0xb588u
#define JY61P_RRATE_200HZ       0x000bu
#define JY61P_BANDWIDTH_98HZ    0x0002u
#define JY61P_RUNTIME_GYROCALITHR 0x0001u
#define JY61P_GYROCALITHR_VALUE 0x0000u
#define JY61P_GYROCALTIME_VALUE 0xffffu
#define JY61P_RESTART_VALUE     0x00ffu

#define IMU_HEALTH_CHECK_US 50000u
#define IMU_HEALTH_FROZEN_LIMIT 4u
#define IMU_HEALTH_RATE_WINDOW_US 2000000u
#define IMU_HEALTH_RATE_TOLERANCE_PERCENT 5u
#define IMU_HEALTH_CANARY_US 30000000u
#define IMU_HEALTH_IDENTICAL_LIMIT 400u
#define IMU_HEALTH_PLAUSIBILITY_LIMIT 400u
#define IMU_HEALTH_I2C_LIMIT 3u
#define IMU_CHIP_HOUR_MS 3600000u
#define IMU_BENCHMARK_SAMPLES 32u
#define IMU_EXTENDED_MAX_DELTA_US 100u
#define IMU_EXTENDED_MAX_DELTA_PERCENT 15u
#define IMU_CR_LENGTH_COUNT 6u
#define IMU_PULL_HIST_BINS_PER_PAGE 7u
#define IMU_PULL_COST_ITERATIONS 4096u

#define IMU_THREAD_STACK_SIZE 2048
#define IMU_THREAD_PRIORITY 4

BUILD_ASSERT(DT_NODE_HAS_STATUS(IMU_I2C_NODE, okay),
	     "B306 IMU I2C must be enabled by the application overlay");
BUILD_ASSERT(DT_PROP(IMU_I2C_NODE, clock_frequency) == I2C_BITRATE_FAST,
	     "JY61P bus must run at 400 kHz");
static const struct device *const imu_i2c = DEVICE_DT_GET(IMU_I2C_NODE);
static bsf_imu_publish_fn publish_record;
static void flush_batch(void);

static K_MUTEX_DEFINE(i2c_lock);
static K_MUTEX_DEFINE(health_lock);
static K_SEM_DEFINE(start_sem, 0, 1);
static K_SEM_DEFINE(stopped_sem, 0, 1);

static atomic_t imu_active;
static atomic_t imu_rate_hz = ATOMIC_INIT(200);
static atomic_t imu_batch_size = ATOMIC_INIT(BSF_IMU_BATCH_DEFAULT);
static atomic_t imu_pulls;
static atomic_t imu_repeated_chip_polls;
static atomic_t imu_fresh_frames;
static atomic_t imu_equal_motion_frames;
static atomic_t imu_incoherent_reads;
static atomic_t imu_missed_chip_frames;
static atomic_t imu_i2c_errors;
static atomic_t imu_records;

static uint16_t verified_gyrocalithr;
static uint16_t verified_gyrocalitime;
static uint16_t verified_rrate;
static uint16_t verified_bandwidth;
static bool verify_pass;

static uint8_t last_motion_sample[12];
static bool have_last_motion_sample;
static uint64_t next_sample_deadline_us;
static bool have_sample_deadline;
static uint16_t next_sample_sequence;
static uint16_t health_identical_run;
static uint16_t health_plausibility_run;
static uint8_t consecutive_i2c_errors;

struct imu_pull_diagnostic {
	uint32_t lateness_hist[BSF_IMU_PULL_HIST_BINS];
	uint32_t duration_hist[BSF_IMU_PULL_HIST_BINS];
	uint64_t lateness_max_ts_us;
	uint64_t duration_max_ts_us;
	uint32_t lateness_max_us;
	uint32_t duration_max_us;
	uint32_t pull_count;
};

static struct imu_pull_diagnostic pull_diagnostic;
static uint32_t pull_diag_cost_cycles;
static uint32_t pull_diag_cost_ns;

static const uint8_t imu_cr_lengths[IMU_CR_LENGTH_COUNT] = {
	2u, 8u, 14u, 20u, 26u, 34u,
};

struct imu_latency_diagnostic {
	uint32_t mean_400_us[IMU_CR_LENGTH_COUNT];
	uint32_t mean_100_us[IMU_CR_LENGTH_COUNT];
	uint32_t production_400_us;
	uint32_t production_100_us;
	uint32_t restored_400_us;
	int configure_400_error;
	int configure_100_error;
	int restore_400_error;
	int transfer_error;
	uint8_t complete;
};

static struct imu_latency_diagnostic latency_diagnostic;

struct jy61p_chip_time {
	uint16_t reg30;
	uint16_t reg31;
	uint16_t reg32;
	uint16_t reg33;
	uint32_t hour_ms;
};

struct imu_health_state {
	uint64_t last_good_ts_us;
	uint64_t fault_ts_us;
	uint64_t recovered_ts_us;
	uint32_t reset_count;
	uint32_t frozen_count;
	uint32_t rate_count;
	uint32_t canary_count;
	uint32_t plausibility_count;
	uint32_t dead_count;
	uint32_t identical_count;
	uint32_t i2c_escalation_count;
	uint32_t recover_ok_count;
	uint32_t recover_fail_count;
	uint32_t legacy_pull_mean_us;
	uint32_t extended_pull_mean_us;
	uint32_t delta_count;
	int32_t delta_min_ms;
	int32_t delta_max_ms;
	uint32_t delta_max_abs_ms;
	uint32_t delta_hist[BSF_IMU_DELTA_HIST_BINS];
	uint8_t fault_class;
	uint8_t fault_active;
	uint8_t fault_latched;
	uint8_t use_extended_burst;
};

static struct imu_health_state health_state;
static struct jy61p_chip_time last_chip_time;
static struct jy61p_chip_time rate_anchor_chip_time;
static uint64_t next_health_check_us;
static uint64_t next_canary_check_us;
static uint64_t rate_anchor_b306_us;
static uint64_t last_chip_b306_us;
static uint8_t frozen_observations;
static bool have_chip_time;
static bool have_rate_anchor;

static bsf_ble_imu_sample_t batch_samples[BSF_IMU_BATCH_MAX];
static uint8_t batch_count;
static uint16_t batch_first_sequence;
static uint64_t batch_base_timestamp;
static int16_t batch_temperature;

static uint16_t get_le16(const uint8_t *bytes)
{
	return sys_get_le16(bytes);
}

static void pull_diag_record(uint64_t start_us, uint32_t lateness_us,
			     uint32_t duration_us)
{
	bool first = pull_diagnostic.pull_count == 0u;

	pull_diagnostic.lateness_hist[
		bsf_imu_pull_hist_bin(lateness_us)]++;
	pull_diagnostic.duration_hist[
		bsf_imu_pull_hist_bin(duration_us)]++;
	if (first || lateness_us > pull_diagnostic.lateness_max_us) {
		pull_diagnostic.lateness_max_us = lateness_us;
		pull_diagnostic.lateness_max_ts_us = start_us;
	}
	if (first || duration_us > pull_diagnostic.duration_max_us) {
		pull_diagnostic.duration_max_us = duration_us;
		pull_diagnostic.duration_max_ts_us = start_us;
	}
	pull_diagnostic.pull_count++;
}

static void pull_diag_measure_cost(void)
{
	uint32_t start_cycles = k_cycle_get_32();

	for (uint32_t i = 0u; i < IMU_PULL_COST_ITERATIONS; ++i) {
		uint64_t start_us = bsf_time_now_us();
		uint64_t end_us = bsf_time_now_us();

		pull_diag_record(start_us, 0u, (uint32_t)(end_us - start_us));
	}
	uint32_t elapsed_cycles = k_cycle_get_32() - start_cycles;

	pull_diag_cost_cycles =
		(elapsed_cycles + IMU_PULL_COST_ITERATIONS / 2u) /
		IMU_PULL_COST_ITERATIONS;
	pull_diag_cost_ns =
		(uint32_t)(k_cyc_to_ns_near64(elapsed_cycles) /
			   IMU_PULL_COST_ITERATIONS);
	memset(&pull_diagnostic, 0, sizeof(pull_diagnostic));
}

static int jy61p_write16(uint8_t reg, uint16_t value)
{
	uint8_t request[] = {
		reg,
		(uint8_t)(value & 0xffu),
		(uint8_t)(value >> 8),
	};

	return i2c_write(imu_i2c, request, sizeof(request), JY61P_I2C_ADDRESS);
}

static int jy61p_read(uint8_t reg, uint8_t *data, size_t len)
{
	return i2c_write_read(imu_i2c, JY61P_I2C_ADDRESS,
			      &reg, sizeof(reg), data, len);
}

static int jy61p_read16(uint8_t reg, uint16_t *value)
{
	uint8_t raw[2];
	int ret = jy61p_read(reg, raw, sizeof(raw));

	if (ret == 0) {
		*value = get_le16(raw);
	}
	return ret;
}

static int decode_chip_time(const uint8_t raw[JY61P_CHIP_TIME_LENGTH],
			    struct jy61p_chip_time *chip)
{
	uint8_t minute;
	uint8_t second;

	chip->reg30 = get_le16(&raw[0]);
	chip->reg31 = get_le16(&raw[2]);
	chip->reg32 = get_le16(&raw[4]);
	chip->reg33 = get_le16(&raw[6]);
	minute = raw[4];
	second = raw[5];
	if (minute >= 60u || second >= 60u || chip->reg33 >= 1000u) {
		return -EBADMSG;
	}
	chip->hour_ms =
		((uint32_t)minute * 60u + (uint32_t)second) * 1000u +
		chip->reg33;
	return 0;
}

static int jy61p_read_chip_time(struct jy61p_chip_time *chip)
{
	uint8_t raw[JY61P_CHIP_TIME_LENGTH];
	int ret = jy61p_read(JY61P_EXTENDED_REGISTER, raw, sizeof(raw));

	return ret == 0 ? decode_chip_time(raw, chip) : ret;
}

static bool i2c_result_requires_recovery(int result)
{
	if (result == 0) {
		consecutive_i2c_errors = 0u;
		return false;
	}

	atomic_inc(&imu_i2c_errors);
	if (++consecutive_i2c_errors < IMU_HEALTH_I2C_LIMIT) {
		return false;
	}
	consecutive_i2c_errors = 0u;
	return true;
}

static void health_increment_class_locked(uint8_t fault_class)
{
	switch (fault_class) {
	case BSF_IMU_HEALTH_BOOT_RESET:
	case BSF_IMU_HEALTH_CHIP_BACKWARD:
		health_state.reset_count++;
		break;
	case BSF_IMU_HEALTH_CHIP_FROZEN:
		health_state.frozen_count++;
		break;
	case BSF_IMU_HEALTH_CHIP_RATE:
		health_state.rate_count++;
		break;
	case BSF_IMU_HEALTH_CANARY:
		health_state.canary_count++;
		break;
	case BSF_IMU_HEALTH_ACC_PLAUSIBILITY:
		health_state.plausibility_count++;
		break;
	case BSF_IMU_HEALTH_DEAD_BLOCK:
		health_state.dead_count++;
		break;
	case BSF_IMU_HEALTH_IDENTICAL_WEDGE:
		health_state.identical_count++;
		break;
	case BSF_IMU_HEALTH_I2C_CONSECUTIVE_FAILURES:
		health_state.i2c_escalation_count++;
		break;
	default:
		break;
	}
}

static void health_record_fault(uint8_t fault_class, uint64_t timestamp_us)
{
	k_mutex_lock(&health_lock, K_FOREVER);
	health_increment_class_locked(fault_class);
	if (health_state.fault_active == 0u) {
		health_state.fault_ts_us = timestamp_us;
		health_state.fault_class = fault_class;
		health_state.fault_active = 1u;
	}
	health_state.fault_latched = 1u;
	k_mutex_unlock(&health_lock);
}

static void health_record_good(uint64_t timestamp_us)
{
	k_mutex_lock(&health_lock, K_FOREVER);
	/*
	 * Once a fault is latched, preserve the last pre-fault timestamp as the
	 * left edge of the exclusion window. COUNTERS CLEAR arms a new window.
	 */
	if (health_state.fault_active == 0u &&
	    health_state.fault_latched == 0u) {
		health_state.last_good_ts_us = timestamp_us;
	}
	k_mutex_unlock(&health_lock);
}

static void health_record_recovery(uint64_t timestamp_us, bool recovered)
{
	k_mutex_lock(&health_lock, K_FOREVER);
	if (recovered) {
		health_state.recovered_ts_us = timestamp_us;
		health_state.fault_active = 0u;
		health_state.recover_ok_count++;
	} else {
		health_state.recover_fail_count++;
	}
	k_mutex_unlock(&health_lock);
}

static bool health_fault_is_active(void)
{
	bool active;

	k_mutex_lock(&health_lock, K_FOREVER);
	active = health_state.fault_active != 0u;
	k_mutex_unlock(&health_lock);
	return active;
}

static bool health_uses_extended_burst(void)
{
	bool enabled;

	k_mutex_lock(&health_lock, K_FOREVER);
	enabled = health_state.use_extended_burst != 0u;
	k_mutex_unlock(&health_lock);
	return enabled;
}

static int jy61p_write_verified_locked(uint8_t reg, uint16_t request,
				       uint16_t *readback)
{
	int ret = jy61p_write16(JY61P_REG_UNLOCK, JY61P_UNLOCK_VALUE);

	if (ret == 0) {
		k_msleep(2);
		ret = jy61p_write16(reg, request);
	}
	if (ret == 0) {
		k_msleep(5);
		ret = jy61p_read16(reg, readback);
	}
	if (ret == 0 && *readback != request) {
		ret = -EIO;
	}
	return ret;
}

static int apply_runtime_config_locked(void)
{
	uint16_t readback = 0u;
	int ret;

	ret = jy61p_write_verified_locked(JY61P_REG_GYROCALITHR,
					  JY61P_RUNTIME_GYROCALITHR,
					  &readback);
	if (ret == 0) {
		verified_gyrocalithr = readback;
		ret = jy61p_write_verified_locked(JY61P_REG_RRATE,
						  JY61P_RRATE_200HZ,
						  &readback);
	}
	if (ret == 0) {
		verified_rrate = readback;
		ret = jy61p_write_verified_locked(JY61P_REG_BANDWIDTH,
						  JY61P_BANDWIDTH_98HZ,
						  &readback);
	}
	if (ret == 0) {
		verified_bandwidth = readback;
	}
	verify_pass = ret == 0 &&
		verified_gyrocalithr == JY61P_RUNTIME_GYROCALITHR &&
		verified_rrate == JY61P_RRATE_200HZ &&
		verified_bandwidth == JY61P_BANDWIDTH_98HZ;
	return ret;
}

static int read_runtime_canaries_locked(uint16_t *gyrocalithr,
					uint16_t *rrate,
					uint16_t *bandwidth,
					uint16_t *version)
{
	int ret = jy61p_read16(JY61P_REG_GYROCALITHR, gyrocalithr);

	if (ret == 0) {
		ret = jy61p_read16(JY61P_REG_RRATE, rrate);
	}
	if (ret == 0) {
		ret = jy61p_read16(JY61P_REG_BANDWIDTH, bandwidth);
	}
	if (ret == 0) {
		ret = jy61p_read16(0x2eu, version);
	}
	return ret;
}

static int benchmark_exact_read(uint8_t reg, size_t length,
				uint32_t *mean_us)
{
	uint8_t raw[JY61P_EXTENDED_LENGTH];
	uint64_t total = 0u;

	for (uint32_t sample = 0u; sample < IMU_BENCHMARK_SAMPLES; ++sample) {
		uint64_t started = bsf_time_now_us();
		int ret = jy61p_read(reg, raw, length);
		uint64_t finished = bsf_time_now_us();

		if (ret != 0) {
			return ret;
		}
		total += finished - started;
	}
	*mean_us = (uint32_t)(total / IMU_BENCHMARK_SAMPLES);
	return 0;
}

static int benchmark_length_set(uint32_t means_us[IMU_CR_LENGTH_COUNT],
				uint32_t *production_mean_us)
{
	uint8_t raw[JY61P_EXTENDED_LENGTH];
	uint64_t totals[IMU_CR_LENGTH_COUNT] = { 0 };

	for (uint32_t sample = 0u; sample < IMU_BENCHMARK_SAMPLES; ++sample) {
		for (uint32_t position = 0u;
		     position < IMU_CR_LENGTH_COUNT; ++position) {
			uint32_t index = (sample & 1u) == 0u ?
				position : IMU_CR_LENGTH_COUNT - 1u - position;
			uint64_t started = bsf_time_now_us();
			int ret = jy61p_read(JY61P_EXTENDED_REGISTER, raw,
					     imu_cr_lengths[index]);
			uint64_t finished = bsf_time_now_us();

			if (ret != 0) {
				return ret;
			}
			totals[index] += finished - started;
		}
	}
	for (uint32_t index = 0u; index < IMU_CR_LENGTH_COUNT; ++index) {
		means_us[index] =
			(uint32_t)(totals[index] / IMU_BENCHMARK_SAMPLES);
	}
	return benchmark_exact_read(JY61P_FRAME_REGISTER, JY61P_FRAME_LENGTH,
				    production_mean_us);
}

static void benchmark_latency_rebaseline(void)
{
	int ret = 0;

	memset(&latency_diagnostic, 0, sizeof(latency_diagnostic));
	latency_diagnostic.configure_400_error =
		i2c_configure(imu_i2c, I2C_MODE_CONTROLLER |
			      I2C_SPEED_SET(I2C_SPEED_FAST));
	ret = latency_diagnostic.configure_400_error;
	if (ret == 0) {
		ret = benchmark_length_set(latency_diagnostic.mean_400_us,
					   &latency_diagnostic.production_400_us);
	}
	if (ret == 0) {
		latency_diagnostic.configure_100_error =
			i2c_configure(imu_i2c, I2C_MODE_CONTROLLER |
				      I2C_SPEED_SET(I2C_SPEED_STANDARD));
		ret = latency_diagnostic.configure_100_error;
	}
	if (ret == 0) {
		ret = benchmark_length_set(latency_diagnostic.mean_100_us,
					   &latency_diagnostic.production_100_us);
	}

	/*
	 * This restore is deliberately unconditional. A diagnostic failure must
	 * never strand the production bus at 100 kHz.
	 */
	latency_diagnostic.restore_400_error =
		i2c_configure(imu_i2c, I2C_MODE_CONTROLLER |
			      I2C_SPEED_SET(I2C_SPEED_FAST));
	if (ret == 0) {
		ret = latency_diagnostic.restore_400_error;
	}
	if (ret == 0) {
		ret = benchmark_exact_read(JY61P_FRAME_REGISTER,
					   JY61P_FRAME_LENGTH,
					   &latency_diagnostic.restored_400_us);
	}
	latency_diagnostic.transfer_error = ret;
	latency_diagnostic.complete =
		latency_diagnostic.configure_400_error == 0 &&
		latency_diagnostic.configure_100_error == 0 &&
		latency_diagnostic.restore_400_error == 0 && ret == 0;
	LOG_INF("JY61P C-R latency complete=%u cfg=%d/%d/%d transfer=%d "
		"400=%u,%u,%u,%u,%u,%u 100=%u,%u,%u,%u,%u,%u "
		"production=%u/%u restored=%u",
		latency_diagnostic.complete,
		latency_diagnostic.configure_400_error,
		latency_diagnostic.configure_100_error,
		latency_diagnostic.restore_400_error,
		latency_diagnostic.transfer_error,
		latency_diagnostic.mean_400_us[0],
		latency_diagnostic.mean_400_us[1],
		latency_diagnostic.mean_400_us[2],
		latency_diagnostic.mean_400_us[3],
		latency_diagnostic.mean_400_us[4],
		latency_diagnostic.mean_400_us[5],
		latency_diagnostic.mean_100_us[0],
		latency_diagnostic.mean_100_us[1],
		latency_diagnostic.mean_100_us[2],
		latency_diagnostic.mean_100_us[3],
		latency_diagnostic.mean_100_us[4],
		latency_diagnostic.mean_100_us[5],
		latency_diagnostic.production_400_us,
		latency_diagnostic.production_100_us,
		latency_diagnostic.restored_400_us);
}

static void benchmark_transaction_shapes(void)
{
	uint8_t legacy[JY61P_FRAME_LENGTH];
	uint8_t extended[JY61P_EXTENDED_LENGTH];
	uint64_t legacy_total = 0u;
	uint64_t extended_total = 0u;
	uint64_t started;
	uint64_t finished;
	uint32_t legacy_mean = UINT32_MAX;
	uint32_t extended_mean = UINT32_MAX;
	uint32_t delta;
	uint32_t relative_percent;
	uint32_t legacy_ok = 0u;
	uint32_t extended_ok = 0u;

	for (uint32_t i = 0u; i < IMU_BENCHMARK_SAMPLES; ++i) {
		started = bsf_time_now_us();
		if (jy61p_read(JY61P_FRAME_REGISTER, legacy, sizeof(legacy)) == 0) {
			finished = bsf_time_now_us();
			legacy_total += finished - started;
			legacy_ok++;
		}
		started = bsf_time_now_us();
		if (jy61p_read(JY61P_EXTENDED_REGISTER,
			       extended, sizeof(extended)) == 0) {
			finished = bsf_time_now_us();
			extended_total += finished - started;
			extended_ok++;
		}
	}
	if (legacy_ok != 0u) {
		legacy_mean = (uint32_t)(legacy_total / legacy_ok);
	}
	if (extended_ok != 0u) {
		extended_mean = (uint32_t)(extended_total / extended_ok);
	}
	delta = extended_mean >= legacy_mean ?
		extended_mean - legacy_mean : legacy_mean - extended_mean;
	relative_percent = legacy_mean != 0u && legacy_mean != UINT32_MAX ?
		(uint32_t)(((uint64_t)delta * 100u) / legacy_mean) :
		UINT32_MAX;

	k_mutex_lock(&health_lock, K_FOREVER);
	health_state.legacy_pull_mean_us = legacy_mean;
	health_state.extended_pull_mean_us = extended_mean;
	health_state.use_extended_burst =
		legacy_ok == IMU_BENCHMARK_SAMPLES &&
		extended_ok == IMU_BENCHMARK_SAMPLES &&
		delta <= IMU_EXTENDED_MAX_DELTA_US &&
		relative_percent <= IMU_EXTENDED_MAX_DELTA_PERCENT;
	k_mutex_unlock(&health_lock);
	LOG_INF("JY61P pull benchmark legacy=%u us extended=%u us delta=%u us relative=%u%% use_extended=%u",
		legacy_mean, extended_mean, delta, relative_percent,
		health_state.use_extended_burst);
}

static int verify_registers(void)
{
	int first_error = 0;
	int ret;

	ret = jy61p_read16(JY61P_REG_GYROCALITHR, &verified_gyrocalithr);
	if (ret != 0 && first_error == 0) {
		first_error = ret;
	}
	ret = jy61p_read16(JY61P_REG_GYROCALTIME, &verified_gyrocalitime);
	if (ret != 0 && first_error == 0) {
		first_error = ret;
	}
	ret = jy61p_read16(JY61P_REG_RRATE, &verified_rrate);
	if (ret != 0 && first_error == 0) {
		first_error = ret;
	}
	ret = jy61p_read16(JY61P_REG_BANDWIDTH, &verified_bandwidth);
	if (ret != 0 && first_error == 0) {
		first_error = ret;
	}

	verify_pass = first_error == 0 &&
		verified_gyrocalithr == JY61P_RUNTIME_GYROCALITHR &&
		verified_rrate == JY61P_RRATE_200HZ &&
		verified_bandwidth == JY61P_BANDWIDTH_98HZ;
	return first_error;
}

static uint32_t chip_time_delta_ms(uint32_t newer, uint32_t older)
{
	return (newer + IMU_CHIP_HOUR_MS - older) % IMU_CHIP_HOUR_MS;
}

static void health_record_delta(int32_t residual_ms)
{
	uint32_t absolute_ms = residual_ms < 0 ?
		(uint32_t)(-(int64_t)residual_ms) : (uint32_t)residual_ms;
	uint8_t bin = bsf_imu_delta_hist_bin(residual_ms);

	k_mutex_lock(&health_lock, K_FOREVER);
	if (health_state.delta_count == 0u) {
		health_state.delta_min_ms = residual_ms;
		health_state.delta_max_ms = residual_ms;
	} else {
		if (residual_ms < health_state.delta_min_ms) {
			health_state.delta_min_ms = residual_ms;
		}
		if (residual_ms > health_state.delta_max_ms) {
			health_state.delta_max_ms = residual_ms;
		}
	}
	if (absolute_ms > health_state.delta_max_abs_ms) {
		health_state.delta_max_abs_ms = absolute_ms;
	}
	health_state.delta_count++;
	health_state.delta_hist[bin]++;
	k_mutex_unlock(&health_lock);
}

static void reset_health_runtime(uint64_t now_us)
{
	have_chip_time = false;
	have_rate_anchor = false;
	frozen_observations = 0u;
	consecutive_i2c_errors = 0u;
	health_identical_run = 0u;
	health_plausibility_run = 0u;
	next_health_check_us = now_us + IMU_HEALTH_CHECK_US;
	next_canary_check_us = now_us + IMU_HEALTH_CANARY_US;
}

static bool motion_block_dead(const uint8_t raw[JY61P_FRAME_LENGTH])
{
	bool all_zero = true;
	bool all_ff = true;

	for (size_t i = 0u; i < 12u; ++i) {
		all_zero &= raw[i] == 0u;
		all_ff &= raw[i] == 0xffu;
	}
	return all_zero || all_ff;
}

static bool runtime_config_matches(uint16_t gyrocalithr, uint16_t rrate,
				   uint16_t bandwidth, uint16_t version)
{
	return gyrocalithr == JY61P_RUNTIME_GYROCALITHR &&
		rrate == JY61P_RRATE_200HZ &&
		bandwidth == JY61P_BANDWIDTH_98HZ &&
		version == 0x469bu;
}

static bool health_attempt_recovery(uint8_t fault_class, uint64_t fault_ts_us)
{
	struct jy61p_chip_time first = { 0 };
	struct jy61p_chip_time second = { 0 };
	uint8_t raw[JY61P_FRAME_LENGTH];
	uint32_t chip_delta = 0u;
	int ret;

	health_record_fault(fault_class, fault_ts_us);
	/*
	 * Preserve already accepted samples. Dropping a partial batch here
	 * consumed sequence numbers without any queue-drop or producer-abort
	 * evidence, making a host-visible gap impossible to balance.
	 */
	flush_batch();
	have_sample_deadline = false;
	have_last_motion_sample = false;

	k_mutex_lock(&i2c_lock, K_FOREVER);
	if (fault_class == BSF_IMU_HEALTH_I2C_CONSECUTIVE_FAILURES) {
		(void)i2c_recover_bus(imu_i2c);
	}
	ret = apply_runtime_config_locked();
	if (ret == 0) {
		ret = jy61p_read_chip_time(&first);
	}
	if (ret == 0) {
		k_msleep(10);
		ret = jy61p_read_chip_time(&second);
	}
	if (ret == 0) {
		chip_delta = chip_time_delta_ms(second.hour_ms, first.hour_ms);
		if (chip_delta == 0u || chip_delta > IMU_CHIP_HOUR_MS / 2u) {
			ret = -EIO;
		}
	}
	if (ret == 0) {
		ret = jy61p_read(JY61P_FRAME_REGISTER, raw, sizeof(raw));
	}
	if (ret == 0 && motion_block_dead(raw)) {
		ret = -ENODATA;
	}
	k_mutex_unlock(&i2c_lock);

	health_record_recovery(bsf_time_now_us(), ret == 0);
	if (ret == 0) {
		uint64_t recovered_us = bsf_time_now_us();

		reset_health_runtime(recovered_us);
		last_chip_time = second;
		last_chip_b306_us = recovered_us;
		have_chip_time = true;
		rate_anchor_chip_time = second;
		rate_anchor_b306_us = recovered_us;
		have_rate_anchor = true;
	}
	LOG_WRN("JY61P health fault class=%u recovery=%s err=%d chip_delta_ms=%u",
		fault_class, ret == 0 ? "PASS" : "FAIL", ret, chip_delta);
	return ret == 0;
}

static uint8_t evaluate_chip_time(const struct jy61p_chip_time *chip,
				  uint64_t now_us)
{
	uint32_t chip_delta;
	uint32_t b306_delta_ms;
	uint32_t sensor_delta_ms;
	uint32_t difference_ms;

	if (!have_chip_time) {
		last_chip_time = *chip;
		last_chip_b306_us = now_us;
		have_chip_time = true;
		rate_anchor_chip_time = *chip;
		rate_anchor_b306_us = now_us;
		have_rate_anchor = true;
		return BSF_IMU_HEALTH_NONE;
	}

	health_record_delta(bsf_imu_delta_residual_ms(
		chip->hour_ms, last_chip_time.hour_ms,
		now_us - last_chip_b306_us));
	chip_delta = chip_time_delta_ms(chip->hour_ms, last_chip_time.hour_ms);
	last_chip_b306_us = now_us;
	if (chip_delta > IMU_CHIP_HOUR_MS / 2u) {
		return BSF_IMU_HEALTH_CHIP_BACKWARD;
	}
	if (chip_delta == 0u) {
		if (++frozen_observations >= IMU_HEALTH_FROZEN_LIMIT) {
			return BSF_IMU_HEALTH_CHIP_FROZEN;
		}
	} else {
		frozen_observations = 0u;
	}
	last_chip_time = *chip;

	if (!have_rate_anchor) {
		rate_anchor_chip_time = *chip;
		rate_anchor_b306_us = now_us;
		have_rate_anchor = true;
		return BSF_IMU_HEALTH_NONE;
	}
	if (now_us - rate_anchor_b306_us < IMU_HEALTH_RATE_WINDOW_US) {
		return BSF_IMU_HEALTH_NONE;
	}
	b306_delta_ms = (uint32_t)((now_us - rate_anchor_b306_us) / 1000u);
	sensor_delta_ms = chip_time_delta_ms(
		chip->hour_ms, rate_anchor_chip_time.hour_ms);
	if (sensor_delta_ms > IMU_CHIP_HOUR_MS / 2u) {
		return BSF_IMU_HEALTH_CHIP_BACKWARD;
	}
	difference_ms = sensor_delta_ms >= b306_delta_ms ?
		sensor_delta_ms - b306_delta_ms :
		b306_delta_ms - sensor_delta_ms;
	rate_anchor_chip_time = *chip;
	rate_anchor_b306_us = now_us;
	if ((uint64_t)difference_ms * 100u >
	    (uint64_t)b306_delta_ms * IMU_HEALTH_RATE_TOLERANCE_PERCENT) {
		return BSF_IMU_HEALTH_CHIP_RATE;
	}
	return BSF_IMU_HEALTH_NONE;
}

static uint8_t check_runtime_canaries(void)
{
	uint16_t gyrocalithr = 0u;
	uint16_t rrate = 0u;
	uint16_t bandwidth = 0u;
	uint16_t version = 0u;
	int ret;

	do {
		k_mutex_lock(&i2c_lock, K_FOREVER);
		ret = read_runtime_canaries_locked(&gyrocalithr, &rrate,
						   &bandwidth, &version);
		k_mutex_unlock(&i2c_lock);
		if (ret == 0) {
			(void)i2c_result_requires_recovery(0);
			break;
		}
		if (i2c_result_requires_recovery(ret)) {
			return BSF_IMU_HEALTH_I2C_CONSECUTIVE_FAILURES;
		}
	} while (true);
	if (!runtime_config_matches(gyrocalithr, rrate, bandwidth, version)) {
		return BSF_IMU_HEALTH_CANARY;
	}
	return BSF_IMU_HEALTH_NONE;
}

static bool run_periodic_health_checks(uint64_t now_us,
				       const struct jy61p_chip_time *burst_chip)
{
	struct jy61p_chip_time chip = { 0 };
	uint8_t fault_class = BSF_IMU_HEALTH_NONE;
	int ret = 0;

	if (now_us < next_health_check_us) {
		return false;
	}
	next_health_check_us = now_us + IMU_HEALTH_CHECK_US;
	if (burst_chip != NULL) {
		chip = *burst_chip;
	} else {
		do {
			k_mutex_lock(&i2c_lock, K_FOREVER);
			ret = jy61p_read_chip_time(&chip);
			k_mutex_unlock(&i2c_lock);
			if (ret == 0) {
				(void)i2c_result_requires_recovery(0);
				break;
			}
			if (i2c_result_requires_recovery(ret)) {
				fault_class =
					BSF_IMU_HEALTH_I2C_CONSECUTIVE_FAILURES;
				break;
			}
		} while (true);
	}
	if (fault_class == BSF_IMU_HEALTH_NONE) {
		fault_class = evaluate_chip_time(&chip, now_us);
	}
	if (fault_class == BSF_IMU_HEALTH_NONE &&
	    now_us >= next_canary_check_us) {
		next_canary_check_us = now_us + IMU_HEALTH_CANARY_US;
		fault_class = check_runtime_canaries();
	}
	if (fault_class != BSF_IMU_HEALTH_NONE) {
		(void)health_attempt_recovery(fault_class, now_us);
		return true;
	}
	return false;
}

static void flush_batch(void)
{
	uint8_t record[BSF_IMU_RECORD_LEN(BSF_IMU_BATCH_MAX)];
	bsf_ble_imu_prefix_t prefix = {
		.version = BSF_BLE_PROTOCOL_VERSION,
		.kind = BSF_BLE_KIND_IMU,
		.len = (uint16_t)BSF_IMU_RECORD_LEN(batch_count),
		.seq = batch_first_sequence,
		/* The protected kind-3 wire record carries only the low word. */
		.base_timer2_ts_us = (uint32_t)batch_base_timestamp,
	};
	size_t offset = 0u;

	if (batch_count == 0u) {
		return;
	}

	memcpy(&record[offset], &prefix, sizeof(prefix));
	offset += sizeof(prefix);
	memcpy(&record[offset], batch_samples,
	       batch_count * sizeof(batch_samples[0]));
	offset += batch_count * sizeof(batch_samples[0]);
	memcpy(&record[offset], &batch_temperature, sizeof(batch_temperature));
	offset += sizeof(batch_temperature);

	atomic_inc(&imu_records);
	if (publish_record != NULL) {
		(void)publish_record(record, offset);
	}
	batch_count = 0u;
}

static void accept_sample(const uint8_t raw[JY61P_FRAME_LENGTH],
			  uint64_t timestamp_us)
{
	const uint8_t *motion = &raw[JY61P_ACC_OFFSET];
	int32_t ax;
	int32_t ay;
	int32_t az;
	int64_t acc_norm_sq;
	bool equal_motion;
	uint32_t publish_period_us =
		1000000u / (uint32_t)atomic_get(&imu_rate_hz);
	uint16_t sequence;
	uint64_t delta;
	uint8_t target_batch = (uint8_t)atomic_get(&imu_batch_size);
	bsf_ble_imu_sample_t *sample;

	if (!have_sample_deadline) {
		next_sample_deadline_us = timestamp_us;
		have_sample_deadline = true;
	}
	if (timestamp_us < next_sample_deadline_us) {
		atomic_inc(&imu_repeated_chip_polls);
		return;
	}
	if ((timestamp_us - next_sample_deadline_us) >= publish_period_us) {
		uint64_t first_lateness_us =
			timestamp_us - next_sample_deadline_us;
		uint64_t skipped = first_lateness_us / publish_period_us;

		atomic_add(&imu_missed_chip_frames, (atomic_val_t)skipped);
		next_sample_deadline_us += skipped * publish_period_us;
	}
	timestamp_us = next_sample_deadline_us;
	next_sample_deadline_us += publish_period_us;

	if (motion_block_dead(raw)) {
		(void)health_attempt_recovery(BSF_IMU_HEALTH_DEAD_BLOCK,
					     timestamp_us);
		return;
	}
	equal_motion = have_last_motion_sample &&
		memcmp(last_motion_sample, motion, sizeof(last_motion_sample)) == 0;
	if (equal_motion) {
		atomic_inc(&imu_equal_motion_frames);
		if (++health_identical_run > IMU_HEALTH_IDENTICAL_LIMIT) {
			(void)health_attempt_recovery(
				BSF_IMU_HEALTH_IDENTICAL_WEDGE, timestamp_us);
			return;
		}
	} else {
		health_identical_run = 0u;
	}
	memcpy(last_motion_sample, motion, sizeof(last_motion_sample));
	have_last_motion_sample = true;

	ax = (int16_t)get_le16(&raw[JY61P_ACC_OFFSET]);
	ay = (int16_t)get_le16(&raw[JY61P_ACC_OFFSET + 2u]);
	az = (int16_t)get_le16(&raw[JY61P_ACC_OFFSET + 4u]);
	acc_norm_sq =
		(int64_t)ax * ax + (int64_t)ay * ay + (int64_t)az * az;
	if (acc_norm_sq < (int64_t)1024 * 1024 ||
	    acc_norm_sq > (int64_t)3072 * 3072) {
		if (++health_plausibility_run >=
		    IMU_HEALTH_PLAUSIBILITY_LIMIT) {
			(void)health_attempt_recovery(
				BSF_IMU_HEALTH_ACC_PLAUSIBILITY, timestamp_us);
			return;
		}
	} else {
		health_plausibility_run = 0u;
	}
	if (health_fault_is_active()) {
		return;
	}
	atomic_inc(&imu_fresh_frames);

	sequence = next_sample_sequence++;
	if (batch_count != 0u) {
		delta = timestamp_us - batch_base_timestamp;
		if (delta > UINT16_MAX) {
			flush_batch();
		}
	}
	if (batch_count == 0u) {
		batch_first_sequence = sequence;
		batch_base_timestamp = timestamp_us;
		delta = 0u;
	} else {
		delta = timestamp_us - batch_base_timestamp;
	}

	sample = &batch_samples[batch_count++];
	sample->delta_us = (uint16_t)delta;
	for (size_t i = 0; i < 3u; ++i) {
		sample->acc[i] =
			(int16_t)get_le16(&raw[JY61P_ACC_OFFSET + i * 2u]);
		sample->gyro[i] =
			(int16_t)get_le16(&raw[JY61P_GYRO_OFFSET + i * 2u]);
	}
	batch_temperature = (int16_t)get_le16(&raw[JY61P_TEMP_OFFSET]);

	if (batch_count >= target_batch) {
		flush_batch();
	}
	health_record_good(timestamp_us);
}

static void pull_once(void)
{
	uint8_t raw[JY61P_FRAME_LENGTH];
	uint8_t extended[JY61P_EXTENDED_LENGTH];
	struct jy61p_chip_time burst_chip = { 0 };
	const struct jy61p_chip_time *burst_chip_ptr = NULL;
	uint64_t timestamp_full_us;
	uint64_t transaction_end_us;
	uint32_t pull_lateness_us;
	uint32_t pull_duration_us;
	bool use_extended = health_uses_extended_burst();
	int ret;

	atomic_inc(&imu_pulls);
	k_mutex_lock(&i2c_lock, K_FOREVER);
	timestamp_full_us = bsf_time_now_us();
	pull_lateness_us = bsf_imu_pull_lateness_us(
		timestamp_full_us, have_sample_deadline,
		next_sample_deadline_us);
	if (use_extended) {
		ret = jy61p_read(JY61P_EXTENDED_REGISTER,
				 extended, sizeof(extended));
		if (ret == 0) {
			ret = decode_chip_time(extended, &burst_chip);
		}
		if (ret == 0) {
			memcpy(raw, &extended[JY61P_EXTENDED_FRAME_OFFSET],
			       sizeof(raw));
			burst_chip_ptr = &burst_chip;
		}
	} else {
		ret = jy61p_read(JY61P_FRAME_REGISTER, raw, sizeof(raw));
	}
	transaction_end_us = bsf_time_now_us();
	k_mutex_unlock(&i2c_lock);
	pull_duration_us = transaction_end_us >= timestamp_full_us ?
		(uint32_t)MIN(transaction_end_us - timestamp_full_us,
			      (uint64_t)UINT32_MAX) : 0u;
	pull_diag_record(timestamp_full_us, pull_lateness_us,
			 pull_duration_us);
	if (ret != 0) {
		if (i2c_result_requires_recovery(ret)) {
			(void)health_attempt_recovery(
				BSF_IMU_HEALTH_I2C_CONSECUTIVE_FAILURES,
				timestamp_full_us);
		}
		return;
	}
	(void)i2c_result_requires_recovery(0);
	if (run_periodic_health_checks(timestamp_full_us, burst_chip_ptr)) {
		return;
	}
	accept_sample(raw, timestamp_full_us);
}

static void imu_thread(void *unused1, void *unused2, void *unused3)
{
	ARG_UNUSED(unused1);
	ARG_UNUSED(unused2);
	ARG_UNUSED(unused3);

	while (true) {
		k_sem_take(&start_sem, K_FOREVER);

		while (atomic_get(&imu_active) != 0) {
			/*
			 * The sensor has no data-ready pin. Poll continuously so a
			 * 200 Hz producer cannot remain phase-locked just ahead of a
			 * 200 Hz host schedule. Each TWIM transfer blocks this thread;
			 * no artificial delay is inserted between transactions.
			 */
			pull_once();
		}

		flush_batch();
		k_sem_give(&stopped_sem);
	}
}

K_THREAD_DEFINE(imu_thread_id, IMU_THREAD_STACK_SIZE,
		imu_thread, NULL, NULL, NULL,
		IMU_THREAD_PRIORITY, 0, 0);

int bsf_imu_stack_unused(size_t *unused)
{
	return k_thread_stack_space_get(imu_thread_id, unused);
}

int bsf_imu_init(bsf_imu_publish_fn publish)
{
	struct jy61p_chip_time chip = { 0 };
	uint16_t gyrocalithr = 0u;
	uint16_t rrate = 0u;
	uint16_t bandwidth = 0u;
	uint16_t version = 0u;
	uint64_t now_us;
	uint8_t fault_class;
	int ret;

	if (!device_is_ready(imu_i2c)) {
		return -ENODEV;
	}
	publish_record = publish;
	pull_diag_measure_cost();

	/* JY61P startup time is at least one second. */
	k_sleep(K_SECONDS(1));
	k_mutex_lock(&i2c_lock, K_FOREVER);
	benchmark_latency_rebaseline();
	benchmark_transaction_shapes();
	do {
		ret = jy61p_read_chip_time(&chip);
		if (ret == 0) {
			ret = read_runtime_canaries_locked(
				&gyrocalithr, &rrate, &bandwidth, &version);
		}
		if (ret == 0) {
			(void)i2c_result_requires_recovery(0);
			break;
		}
		if (i2c_result_requires_recovery(ret)) {
			break;
		}
	} while (true);
	k_mutex_unlock(&i2c_lock);
	now_us = bsf_time_now_us();
	reset_health_runtime(now_us);
	if (ret == 0) {
		verified_gyrocalithr = gyrocalithr;
		verified_rrate = rrate;
		verified_bandwidth = bandwidth;
		verify_pass = runtime_config_matches(
			gyrocalithr, rrate, bandwidth, version);
		last_chip_time = chip;
		last_chip_b306_us = now_us;
		have_chip_time = true;
		rate_anchor_chip_time = chip;
		rate_anchor_b306_us = now_us;
		have_rate_anchor = true;
	}
	if (ret != 0 || !verify_pass) {
		fault_class =
			ret == 0 && chip.reg30 == 0u && chip.reg31 == 0u &&
			chip.hour_ms < 120000u ?
			BSF_IMU_HEALTH_BOOT_RESET :
			(ret != 0 ?
			 BSF_IMU_HEALTH_I2C_CONSECUTIVE_FAILURES :
			 BSF_IMU_HEALTH_CANARY);
		if (!health_attempt_recovery(fault_class, now_us)) {
			LOG_WRN("JY61P boot health recovery failed: class=%u err=%d 30=%04x 31=%04x 32=%04x 33=%04x 61=%04x 03=%04x 1f=%04x ver=%04x",
				fault_class, ret, chip.reg30, chip.reg31,
				chip.reg32, chip.reg33, gyrocalithr, rrate,
				bandwidth, version);
			return 0;
		}
		LOG_INF("JY61P boot health recovered: class=%u 30=%04x 31=%04x 32=%04x 33=%04x 61=%04x 03=%04x 1f=%04x ver=%04x",
			fault_class, chip.reg30, chip.reg31, chip.reg32,
			chip.reg33, gyrocalithr, rrate, bandwidth, version);
	} else {
		health_record_good(now_us);
		LOG_INF("JY61P boot health PASS: 30=%04x 31=%04x 32=%04x 33=%04x 61=%04x 03=%04x 1f=%04x ver=%04x",
			chip.reg30, chip.reg31, chip.reg32, chip.reg33,
			gyrocalithr, rrate, bandwidth, version);
	}
	return 0;
}

int bsf_imu_start(char *reply, size_t reply_size)
{
	const char *step = "runtime_config";
	int ret;

	if (atomic_get(&imu_active) != 0) {
		ret = -EALREADY;
		snprintf(reply, reply_size,
			 "IMU START FAIL err=%d 61=0000:F 03=0000:F 1F=0000:F volatile=1 saved=0 step=guard reason=active",
			 ret);
		return ret;
	}

	/*
	 * H2 established 0x61=0001. Phase B established that 0x1f=0002 is the
	 * narrowest setting which produces fresh 200 Hz vectors. Re-establish and
	 * verify every volatile canary before sampling; never SAVE or restart.
	 */
	k_mutex_lock(&i2c_lock, K_FOREVER);
	ret = apply_runtime_config_locked();
	k_mutex_unlock(&i2c_lock);
	if (ret != 0) {
		snprintf(reply, reply_size,
			 "IMU START FAIL err=%d 61=%04X:%c 03=%04X:%c 1F=%04X:%c volatile=1 saved=0 step=%s",
			 ret, verified_gyrocalithr,
			 verified_gyrocalithr == JY61P_RUNTIME_GYROCALITHR ?
			 'P' : 'F',
			 verified_rrate,
			 verified_rrate == JY61P_RRATE_200HZ ? 'P' : 'F',
			 verified_bandwidth,
			 verified_bandwidth == JY61P_BANDWIDTH_98HZ ? 'P' : 'F',
			 step);
		return ret;
	}

	if (atomic_cas(&imu_active, 0, 1) == false) {
		ret = -EALREADY;
		snprintf(reply, reply_size,
			 "IMU START FAIL err=%d 61=%04X:P 03=%04X:P 1F=%04X:P volatile=1 saved=0 step=activate reason=active",
			 ret, verified_gyrocalithr, verified_rrate,
			 verified_bandwidth);
		return ret;
	}
	have_last_motion_sample = false;
	have_sample_deadline = false;
	batch_count = 0u;
	reset_health_runtime(bsf_time_now_us());
	k_sem_reset(&stopped_sem);
	k_sem_give(&start_sem);
	snprintf(reply, reply_size,
		 "IMU START OK err=0 61=%04X:P 03=%04X:P 1F=%04X:P volatile=1 saved=0",
		 verified_gyrocalithr, verified_rrate, verified_bandwidth);
	return 0;
}

int bsf_imu_stop(void)
{
	if (atomic_cas(&imu_active, 1, 0) == false) {
		return -EALREADY;
	}
	return k_sem_take(&stopped_sem, K_MSEC(50)) == 0 ?
		0 : -ETIMEDOUT;
}

int bsf_imu_set_rate(uint16_t rate_hz)
{
	if (rate_hz != 200u && rate_hz != 100u && rate_hz != 50u) {
		return -EINVAL;
	}
	atomic_set(&imu_rate_hz, rate_hz);
	return 0;
}

int bsf_imu_set_batch(uint8_t batch_size)
{
	if (batch_size < BSF_IMU_BATCH_MIN ||
	    batch_size > BSF_IMU_BATCH_MAX) {
		return -EINVAL;
	}
	atomic_set(&imu_batch_size, batch_size);
	return 0;
}

int bsf_imu_set_rrate_runtime(uint16_t rrate, char *reply,
			      size_t reply_size)
{
	uint16_t readback = 0u;
	const char *step = "unlock";
	int ret;

	if (rrate < 0x0006u || rrate > JY61P_RRATE_200HZ) {
		snprintf(reply, reply_size,
			 "IMU RRATE FAIL request=%04X volatile=1 saved=0 err=%d reason=range",
			 rrate, -EINVAL);
		return -EINVAL;
	}
	if (atomic_get(&imu_active) != 0) {
		snprintf(reply, reply_size,
			 "IMU RRATE FAIL request=%04X volatile=1 saved=0 err=%d reason=active",
			 rrate, -EBUSY);
		return -EBUSY;
	}

	/*
	 * Match the known runtime path: unlock, allow the sensor to accept the
	 * unlock, write RRATE, allow the new schedule to settle, and do not issue
	 * SAVE or sensor restart. The readback is an added acceptance check.
	 */
	k_mutex_lock(&i2c_lock, K_FOREVER);
	ret = jy61p_write16(JY61P_REG_UNLOCK, JY61P_UNLOCK_VALUE);
	if (ret == 0) {
		k_msleep(2);
		step = "write";
		ret = jy61p_write16(JY61P_REG_RRATE, rrate);
	}
	if (ret == 0) {
		k_msleep(5);
		step = "readback";
		ret = jy61p_read16(JY61P_REG_RRATE, &readback);
	}
	k_mutex_unlock(&i2c_lock);

	if (ret == 0 && readback != rrate) {
		ret = -EIO;
		step = "compare";
	}
	if (ret == 0) {
		verified_rrate = readback;
	}
	snprintf(reply, reply_size,
		 "IMU RRATE %s request=%04X readback=%04X volatile=1 saved=0 step=%s err=%d",
		 ret == 0 ? "OK" : "FAIL", rrate, readback, step, ret);
	return ret;
}

int bsf_imu_set_bandwidth_runtime(uint16_t bandwidth, char *reply,
				  size_t reply_size)
{
	uint16_t readback = 0u;
	const char *step = "unlock";
	int ret;

	if (atomic_get(&imu_active) != 0) {
		snprintf(reply, reply_size,
			 "IMU BW FAIL request=%04X readback=%04X volatile=1 saved=0 step=guard err=%d reason=active",
			 bandwidth, readback, -EBUSY);
		return -EBUSY;
	}

	k_mutex_lock(&i2c_lock, K_FOREVER);
	ret = jy61p_write16(JY61P_REG_UNLOCK, JY61P_UNLOCK_VALUE);
	if (ret == 0) {
		k_msleep(2);
		step = "write";
		ret = jy61p_write16(JY61P_REG_BANDWIDTH, bandwidth);
	}
	if (ret == 0) {
		k_msleep(5);
		step = "readback";
		ret = jy61p_read16(JY61P_REG_BANDWIDTH, &readback);
	}
	k_mutex_unlock(&i2c_lock);

	if (ret == 0 && readback != bandwidth) {
		ret = -EIO;
		step = "compare";
	}
	if (ret == 0) {
		verified_bandwidth = readback;
	}
	snprintf(reply, reply_size,
		 "IMU BW %s request=%04X readback=%04X volatile=1 saved=0 step=%s err=%d",
		 ret == 0 ? "OK" : "FAIL", bandwidth, readback, step, ret);
	return ret;
}

int bsf_imu_reg_read(uint8_t reg, char *reply, size_t reply_size)
{
	uint16_t readback = 0u;
	int ret;

	if (atomic_get(&imu_active) != 0) {
		snprintf(reply, reply_size,
			 "IMU REG FAIL addr=%02X err=%d reason=active",
			 reg, -EBUSY);
		return -EBUSY;
	}

	k_mutex_lock(&i2c_lock, K_FOREVER);
	ret = jy61p_read16(reg, &readback);
	k_mutex_unlock(&i2c_lock);
	snprintf(reply, reply_size,
		 "IMU REG %s addr=%02X readback=%04X err=%d",
		 ret == 0 ? "OK" : "FAIL", reg, readback, ret);
	return ret;
}

int bsf_imu_reg_write(uint8_t reg, uint16_t value, char *reply,
		      size_t reply_size)
{
	uint16_t readback = 0u;
	const char *step = "guard";
	int ret;

	if (atomic_get(&imu_active) != 0) {
		snprintf(reply, reply_size,
			 "IMU REG FAIL addr=%02X request=%04X readback=%04X volatile=1 saved=0 step=guard err=%d reason=active",
			 reg, value, readback, -EBUSY);
		return -EBUSY;
	}
	/*
	 * REG is a volatile investigation tool. Register 0x00 contains SAVE,
	 * factory-reset, and restart operations, so no value at that address is
	 * reachable through this command.
	 */
	if (reg == JY61P_REG_SAVE) {
		snprintf(reply, reply_size,
			 "IMU REG FAIL addr=%02X request=%04X readback=%04X volatile=1 saved=0 step=guard err=%d reason=reg00_forbidden",
			 reg, value, readback, -EPERM);
		return -EPERM;
	}

	k_mutex_lock(&i2c_lock, K_FOREVER);
	step = "unlock";
	ret = jy61p_write16(JY61P_REG_UNLOCK, JY61P_UNLOCK_VALUE);
	if (ret == 0) {
		step = "write";
		ret = jy61p_write16(reg, value);
	}
	if (ret == 0) {
		k_msleep(5);
		step = "readback";
		ret = jy61p_read16(reg, &readback);
	}
	k_mutex_unlock(&i2c_lock);

	snprintf(reply, reply_size,
		 "IMU REG %s addr=%02X request=%04X readback=%04X volatile=1 saved=0 step=%s err=%d",
		 ret == 0 ? "OK" : "FAIL", reg, value, readback, step, ret);
	return ret;
}

int bsf_imu_provision(char *reply, size_t reply_size)
{
	static const struct {
		uint8_t reg;
		uint16_t value;
		const char *name;
	} steps[] = {
		{ JY61P_REG_UNLOCK, JY61P_UNLOCK_VALUE, "unlock" },
		{ JY61P_REG_GYROCALITHR, JY61P_GYROCALITHR_VALUE, "61" },
		{ JY61P_REG_GYROCALTIME, JY61P_GYROCALTIME_VALUE, "63" },
		{ JY61P_REG_RRATE, JY61P_RRATE_200HZ, "03" },
		{ JY61P_REG_BANDWIDTH, JY61P_BANDWIDTH_98HZ, "1f" },
		{ JY61P_REG_SAVE, 0x0000u, "save" },
		{ JY61P_REG_SAVE, JY61P_RESTART_VALUE, "restart" },
	};
	uint16_t immediate_values[4] = { 0 };
	int immediate_results[4] = { -ENODATA, -ENODATA, -ENODATA, -ENODATA };
	int ret = 0;
	size_t failed_step = ARRAY_SIZE(steps);

	if (atomic_get(&imu_active) != 0) {
		snprintf(reply, reply_size,
			 "IMU PROVISION FAIL err=%d reason=active", -EBUSY);
		return -EBUSY;
	}

	k_mutex_lock(&i2c_lock, K_FOREVER);
	for (size_t i = 0; i < ARRAY_SIZE(steps); ++i) {
		ret = jy61p_write16(steps[i].reg, steps[i].value);
		if (ret != 0) {
			failed_step = i;
			break;
		}
			if (i >= 1u && i <= 4u) {
				immediate_results[i - 1u] =
					jy61p_read16(steps[i].reg,
						    &immediate_values[i - 1u]);
				if (immediate_results[i - 1u] != 0) {
					ret = immediate_results[i - 1u];
					failed_step = i;
					break;
				}
				if (immediate_values[i - 1u] != steps[i].value) {
					ret = -EIO;
					failed_step = i;
					break;
				}
			}
	}
	k_mutex_unlock(&i2c_lock);
	if (ret != 0) {
		if (failed_step >= 1u && failed_step <= 4u) {
			snprintf(reply, reply_size,
				 "IMU PROVISION FAIL step=%s request=%04X readback=%04X read_err=%d err=%d",
				 steps[failed_step].name,
				 steps[failed_step].value,
				 immediate_values[failed_step - 1u],
				 immediate_results[failed_step - 1u], ret);
		} else {
			snprintf(reply, reply_size,
				 "IMU PROVISION FAIL step=%s err=%d",
				 steps[failed_step].name, ret);
		}
		return ret;
	}

	k_sleep(K_SECONDS(1));
	k_mutex_lock(&i2c_lock, K_FOREVER);
	ret = verify_registers();
	k_mutex_unlock(&i2c_lock);
	snprintf(reply, reply_size,
		 "IMU PROVISION %s imm=61:%04X:P,63:%04X:P,03:%04X:P,1F:%04X:P final=61:%04X%s,63:%04X%s,03:%04X%s,1F:%04X%s V-A1=REQUIRED",
		 ret == 0 && verify_pass ? "PASS" : "FAIL",
		 immediate_values[0], immediate_values[1],
		 immediate_values[2], immediate_values[3],
		 verified_gyrocalithr,
		 verified_gyrocalithr == JY61P_GYROCALITHR_VALUE ? ":P" : ":F",
		 verified_gyrocalitime,
		 verified_gyrocalitime == JY61P_GYROCALTIME_VALUE ? ":P" : ":F",
		 verified_rrate,
		 verified_rrate == JY61P_RRATE_200HZ ? ":P" : ":F",
		 verified_bandwidth,
		 verified_bandwidth == JY61P_BANDWIDTH_98HZ ? ":P" : ":F");
	return ret == 0 && verify_pass ? 0 : (ret != 0 ? ret : -EIO);
}

int bsf_imu_cal_acc(char *reply, size_t reply_size)
{
	int ret;

	if (atomic_get(&imu_active) != 0) {
		snprintf(reply, reply_size,
			 "IMU CAL_ACC FAIL err=%d reason=active", -EBUSY);
		return -EBUSY;
	}
	k_mutex_lock(&i2c_lock, K_FOREVER);
	ret = jy61p_write16(JY61P_REG_UNLOCK, JY61P_UNLOCK_VALUE);
	if (ret == 0) {
		ret = jy61p_write16(JY61P_REG_CALSW, 0x0001u);
	}
	k_mutex_unlock(&i2c_lock);
	snprintf(reply, reply_size, "IMU CAL_ACC %s err=%d operator-triggered",
		 ret == 0 ? "OK" : "FAIL", ret);
	return ret;
}

int bsf_imu_selftest(char *reply, size_t reply_size)
{
	uint8_t raw[34] = { 0 };
	uint8_t raw_2[34] = { 0 };
	uint8_t raw_3[34] = { 0 };
	bool all_zero = true;
	bool all_ff = true;
	int ret;

	if (atomic_get(&imu_active) != 0) {
		snprintf(reply, reply_size,
			 "IMU SELFTEST FAIL err=%d reason=active", -EBUSY);
		return -EBUSY;
	}
	k_mutex_lock(&i2c_lock, K_FOREVER);
	ret = jy61p_read(0x30u, raw, sizeof(raw));
	if (ret == 0) {
		k_sleep(K_MSEC(2));
		ret = jy61p_read(0x30u, raw_2, sizeof(raw_2));
	}
	if (ret == 0) {
		k_sleep(K_MSEC(4));
		ret = jy61p_read(0x30u, raw_3, sizeof(raw_3));
	}
	k_mutex_unlock(&i2c_lock);
	if (ret == 0) {
		for (size_t i = 0; i < sizeof(raw); ++i) {
			all_zero &= raw[i] == 0u;
			all_ff &= raw[i] == 0xffu;
		}
	}
	snprintf(reply, reply_size,
		 "IMU SELFTEST %s err=%d ax=%d ay=%d az=%d gx=%d gy=%d gz=%d temp=%d chip_ms=%u/%u/%u",
		 ret == 0 && !all_zero && !all_ff ? "PASS" : "FAIL", ret,
		 (int16_t)get_le16(&raw[8]), (int16_t)get_le16(&raw[10]),
		 (int16_t)get_le16(&raw[12]), (int16_t)get_le16(&raw[14]),
		 (int16_t)get_le16(&raw[16]), (int16_t)get_le16(&raw[18]),
		 (int16_t)get_le16(&raw[32]),
		 get_le16(&raw[6]), get_le16(&raw_2[6]),
		 get_le16(&raw_3[6]));
	return ret == 0 && !all_zero && !all_ff ? 0 : (ret != 0 ? ret : -EIO);
}

void bsf_imu_format_status(char *reply, size_t reply_size)
{
	struct bsf_imu_stats stats;

	bsf_imu_get_stats(&stats);
	snprintf(reply, reply_size,
		 "IMU active=%u rate=%u batch=%u verify=%c 61=%04X 03=%04X 1F=%04X p=%u new=%u ie=%u rec=%u h=%u/%u/%u hr=%u/%u lat=%u/%u ext=%u",
		 stats.active, stats.rate_hz, stats.batch_size,
		 stats.verify_pass ? 'P' : 'F',
		 stats.gyrocalithr, stats.rrate, stats.bandwidth,
		 stats.pulls, stats.fresh_frames, stats.i2c_errors,
		 stats.records,
		 stats.health_class, stats.health_active, stats.health_latched,
		 stats.health_recover_ok, stats.health_recover_fail,
		 stats.legacy_pull_mean_us,
			 stats.extended_pull_mean_us, stats.extended_burst);
}

void bsf_imu_format_latency(char *reply, size_t reply_size)
{
	snprintf(reply, reply_size,
		 "IMU LAT %s n=2,8,14,20,26,34 u400=%u,%u,%u,%u,%u,%u u100=%u,%u,%u,%u,%u,%u prod=%u/%u restore=%u cfg=%d/%d/%d xfer=%d",
		 latency_diagnostic.complete ? "PASS" : "FAIL",
		 latency_diagnostic.mean_400_us[0],
		 latency_diagnostic.mean_400_us[1],
		 latency_diagnostic.mean_400_us[2],
		 latency_diagnostic.mean_400_us[3],
		 latency_diagnostic.mean_400_us[4],
		 latency_diagnostic.mean_400_us[5],
		 latency_diagnostic.mean_100_us[0],
		 latency_diagnostic.mean_100_us[1],
		 latency_diagnostic.mean_100_us[2],
		 latency_diagnostic.mean_100_us[3],
		 latency_diagnostic.mean_100_us[4],
		 latency_diagnostic.mean_100_us[5],
		 latency_diagnostic.production_400_us,
		 latency_diagnostic.production_100_us,
		 latency_diagnostic.restored_400_us,
		 latency_diagnostic.configure_400_error,
		 latency_diagnostic.configure_100_error,
		 latency_diagnostic.restore_400_error,
		 latency_diagnostic.transfer_error);
}

int bsf_imu_format_delta_page(uint8_t page, char *reply, size_t reply_size)
{
	uint32_t count;
	int32_t minimum_ms;
	int32_t maximum_ms;
	uint32_t maximum_absolute_ms;
	uint32_t hist[BSF_IMU_DELTA_HIST_BINS];

	k_mutex_lock(&health_lock, K_FOREVER);
	count = health_state.delta_count;
	minimum_ms = health_state.delta_min_ms;
	maximum_ms = health_state.delta_max_ms;
	maximum_absolute_ms = health_state.delta_max_abs_ms;
	memcpy(hist, health_state.delta_hist, sizeof(hist));
	k_mutex_unlock(&health_lock);

	switch (page) {
	case 0u:
		snprintf(reply, reply_size,
			 "IMU DELTA p=0 n=%u min_ms=%d max_ms=%d maxabs_ms=%u h=%u,%u,%u,%u,%u,%u,%u",
			 count, minimum_ms, maximum_ms, maximum_absolute_ms,
			 hist[0], hist[1], hist[2], hist[3], hist[4],
			 hist[5], hist[6]);
		return 0;
	case 1u:
		snprintf(reply, reply_size,
			 "IMU DELTA p=1 h=%u,%u,%u,%u,%u,%u,%u",
			 hist[7], hist[8], hist[9], hist[10], hist[11],
			 hist[12], hist[13]);
		return 0;
	case 2u:
		snprintf(reply, reply_size,
			 "IMU DELTA p=2 h=%u,%u,%u,%u,%u,%u,%u",
			 hist[14], hist[15], hist[16], hist[17], hist[18],
			 hist[19], hist[20]);
		return 0;
	default:
		snprintf(reply, reply_size,
			 "IMU DELTA FAIL err=%d reason=page", -EINVAL);
		return -EINVAL;
	}
}

void bsf_imu_format_pull_summary(char *reply, size_t reply_size)
{
	snprintf(reply, reply_size,
		 "IMU PULL n=%u lm=%u lts=%llu dm=%u dts=%llu ep=0 drop=0 cyc=%u ns=%u",
		 pull_diagnostic.pull_count,
		 pull_diagnostic.lateness_max_us,
		 (unsigned long long)pull_diagnostic.lateness_max_ts_us,
		 pull_diagnostic.duration_max_us,
		 (unsigned long long)pull_diagnostic.duration_max_ts_us,
		 pull_diag_cost_cycles, pull_diag_cost_ns);
}

int bsf_imu_format_pull_hist_page(bool duration, uint8_t page, char *reply,
				  size_t reply_size)
{
	const uint32_t *hist = duration ?
		pull_diagnostic.duration_hist :
		pull_diagnostic.lateness_hist;
	uint32_t first = (uint32_t)page * IMU_PULL_HIST_BINS_PER_PAGE;
	uint32_t count;
	size_t used;

	if (first >= BSF_IMU_PULL_HIST_BINS) {
		snprintf(reply, reply_size,
			 "IMU PULL HIST FAIL err=%d reason=page", -EINVAL);
		return -EINVAL;
	}
	count = MIN(IMU_PULL_HIST_BINS_PER_PAGE,
		    BSF_IMU_PULL_HIST_BINS - first);
	used = (size_t)snprintf(reply, reply_size,
			       "IMU PULL HIST kind=%c p=%u first=%u n=%u h=",
			       duration ? 'D' : 'L', page, first, count);
	for (uint32_t i = 0u; i < count && used < reply_size; ++i) {
		int written = snprintf(&reply[used], reply_size - used,
				       "%s%u", i == 0u ? "" : ",",
				       hist[first + i]);

		if (written < 0 || (size_t)written >= reply_size - used) {
			break;
		}
		used += (size_t)written;
	}
	return 0;
}

void bsf_imu_format_stop(char *reply, size_t reply_size, int stop_result)
{
	struct bsf_imu_stats stats;

	bsf_imu_get_stats(&stats);
	snprintf(reply, reply_size,
		 "IMU STOP %s err=%d h=%u/%u/%u rec=%u/%u win=%016llX/%016llX/%016llX",
		 stop_result == 0 ? "OK" : "FAIL", stop_result,
		 stats.health_class, stats.health_active, stats.health_latched,
		 stats.health_recover_ok, stats.health_recover_fail,
		 (unsigned long long)stats.last_good_ts_us,
		 (unsigned long long)stats.fault_ts_us,
		 (unsigned long long)stats.recovered_ts_us);
}

void bsf_imu_get_stats(struct bsf_imu_stats *stats)
{
	*stats = (struct bsf_imu_stats) {
		.pulls = (uint32_t)atomic_get(&imu_pulls),
		.repeated_chip_polls =
			(uint32_t)atomic_get(&imu_repeated_chip_polls),
		.fresh_frames = (uint32_t)atomic_get(&imu_fresh_frames),
		.equal_motion_frames =
			(uint32_t)atomic_get(&imu_equal_motion_frames),
		.incoherent_reads =
			(uint32_t)atomic_get(&imu_incoherent_reads),
		.missed_chip_frames =
			(uint32_t)atomic_get(&imu_missed_chip_frames),
		.i2c_errors = (uint32_t)atomic_get(&imu_i2c_errors),
		.records = (uint32_t)atomic_get(&imu_records),
		.rate_hz = (uint16_t)atomic_get(&imu_rate_hz),
		.last_chip_ms = 0u,
		.batch_size = (uint8_t)atomic_get(&imu_batch_size),
		.active = (uint8_t)(atomic_get(&imu_active) != 0),
		.have_chip_ms = 0u,
		.verify_pass = (uint8_t)verify_pass,
		.gyrocalithr = verified_gyrocalithr,
		.gyrocalitime = verified_gyrocalitime,
		.rrate = verified_rrate,
		.bandwidth = verified_bandwidth,
		.pull_lateness_max_us = pull_diagnostic.lateness_max_us,
		.pull_duration_max_us = pull_diagnostic.duration_max_us,
	};
	k_mutex_lock(&health_lock, K_FOREVER);
	stats->health_class = health_state.fault_class;
	stats->health_active = health_state.fault_active;
	stats->health_latched = health_state.fault_latched;
	stats->extended_burst = health_state.use_extended_burst;
	stats->health_reset = health_state.reset_count;
	stats->health_frozen = health_state.frozen_count;
	stats->health_rate = health_state.rate_count;
	stats->health_canary = health_state.canary_count;
	stats->health_plausibility = health_state.plausibility_count;
	stats->health_dead = health_state.dead_count;
	stats->health_identical = health_state.identical_count;
	stats->health_i2c_escalation =
		health_state.i2c_escalation_count;
	stats->health_recover_ok = health_state.recover_ok_count;
	stats->health_recover_fail = health_state.recover_fail_count;
	stats->legacy_pull_mean_us = health_state.legacy_pull_mean_us;
	stats->extended_pull_mean_us = health_state.extended_pull_mean_us;
	stats->last_good_ts_us = health_state.last_good_ts_us;
	stats->fault_ts_us = health_state.fault_ts_us;
	stats->recovered_ts_us = health_state.recovered_ts_us;
	k_mutex_unlock(&health_lock);
}

void bsf_imu_clear_counters(void)
{
	atomic_set(&imu_pulls, 0);
	atomic_set(&imu_repeated_chip_polls, 0);
	atomic_set(&imu_fresh_frames, 0);
	atomic_set(&imu_equal_motion_frames, 0);
	atomic_set(&imu_incoherent_reads, 0);
	atomic_set(&imu_missed_chip_frames, 0);
	atomic_set(&imu_i2c_errors, 0);
	atomic_set(&imu_records, 0);
	memset(&pull_diagnostic, 0, sizeof(pull_diagnostic));
	k_mutex_lock(&health_lock, K_FOREVER);
	health_state.reset_count = 0u;
	health_state.frozen_count = 0u;
	health_state.rate_count = 0u;
	health_state.canary_count = 0u;
	health_state.plausibility_count = 0u;
	health_state.dead_count = 0u;
	health_state.identical_count = 0u;
	health_state.i2c_escalation_count = 0u;
	health_state.recover_ok_count = 0u;
	health_state.recover_fail_count = 0u;
	health_state.fault_class = BSF_IMU_HEALTH_NONE;
	health_state.fault_active = 0u;
	health_state.fault_latched = 0u;
	health_state.last_good_ts_us = 0u;
	health_state.fault_ts_us = 0u;
	health_state.recovered_ts_us = 0u;
	health_state.delta_count = 0u;
	health_state.delta_min_ms = 0;
	health_state.delta_max_ms = 0;
	health_state.delta_max_abs_ms = 0u;
	memset(health_state.delta_hist, 0, sizeof(health_state.delta_hist));
	k_mutex_unlock(&health_lock);
}
