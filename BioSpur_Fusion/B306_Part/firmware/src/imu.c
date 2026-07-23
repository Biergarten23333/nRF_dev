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
#include <zephyr/sys/util.h>

#include "strobe_capture.h"

LOG_MODULE_DECLARE(biospur_fusion);

#define IMU_I2C_NODE DT_ALIAS(imu_i2c)
#define JY61P_I2C_ADDRESS 0x50u
#define JY61P_BURST_REGISTER 0x34u
#define JY61P_BURST_LENGTH 26u

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
#define JY61P_GYROCALITHR_VALUE 0x0000u
#define JY61P_GYROCALTIME_VALUE 0xffffu
#define JY61P_RESTART_VALUE     0x00ffu

#define IMU_THREAD_STACK_SIZE 2048
#define IMU_THREAD_PRIORITY 4

BUILD_ASSERT(DT_NODE_HAS_STATUS(IMU_I2C_NODE, okay),
	     "B306 IMU I2C must be enabled by the application overlay");
BUILD_ASSERT(DT_PROP(IMU_I2C_NODE, clock_frequency) == I2C_BITRATE_FAST,
	     "JY61P bus must run at 400 kHz");

static const struct device *const imu_i2c = DEVICE_DT_GET(IMU_I2C_NODE);
static bsf_imu_publish_fn publish_record;

static K_MUTEX_DEFINE(i2c_lock);
static K_SEM_DEFINE(start_sem, 0, 1);
static K_SEM_DEFINE(stopped_sem, 0, 1);

static atomic_t imu_active;
static atomic_t imu_rate_hz = ATOMIC_INIT(200);
static atomic_t imu_batch_size = ATOMIC_INIT(BSF_IMU_BATCH_DEFAULT);
static atomic_t imu_pulls;
static atomic_t imu_duplicate_samples;
static atomic_t imu_i2c_errors;
static atomic_t imu_records;

static uint16_t verified_gyrocalithr;
static uint16_t verified_gyrocalitime;
static uint16_t verified_rrate;
static uint16_t verified_bandwidth;
static bool verify_pass;

static uint8_t last_motion_sample[12];
static bool have_last_motion_sample;
static uint16_t next_sample_sequence;

static bsf_ble_imu_sample_t batch_samples[BSF_IMU_BATCH_MAX];
static uint8_t batch_count;
static uint16_t batch_first_sequence;
static uint32_t batch_base_timestamp;
static int16_t batch_temperature;

static uint16_t get_le16(const uint8_t *bytes)
{
	return sys_get_le16(bytes);
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
		verified_gyrocalithr == JY61P_GYROCALITHR_VALUE &&
		verified_gyrocalitime == JY61P_GYROCALTIME_VALUE &&
		verified_rrate == JY61P_RRATE_200HZ &&
		verified_bandwidth == JY61P_BANDWIDTH_98HZ;
	return first_error;
}

static void flush_batch(void)
{
	uint8_t record[BSF_IMU_RECORD_LEN(BSF_IMU_BATCH_MAX)];
	bsf_ble_imu_prefix_t prefix = {
		.version = BSF_BLE_PROTOCOL_VERSION,
		.kind = BSF_BLE_KIND_IMU,
		.len = (uint16_t)BSF_IMU_RECORD_LEN(batch_count),
		.seq = batch_first_sequence,
		.base_timer2_ts_us = batch_base_timestamp,
	};
	size_t offset = 0u;
	int ret;

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
		ret = publish_record(record, offset);
		ARG_UNUSED(ret);
	}
	batch_count = 0u;
}

static void accept_sample(const uint8_t raw[JY61P_BURST_LENGTH],
			  uint32_t timestamp_us)
{
	uint16_t sequence;
	uint32_t delta;
	uint8_t target_batch = (uint8_t)atomic_get(&imu_batch_size);
	bsf_ble_imu_sample_t *sample;

	if (have_last_motion_sample &&
	    memcmp(last_motion_sample, raw, sizeof(last_motion_sample)) == 0) {
		atomic_inc(&imu_duplicate_samples);
		return;
	}
	memcpy(last_motion_sample, raw, sizeof(last_motion_sample));
	have_last_motion_sample = true;

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
		sample->acc[i] = (int16_t)get_le16(&raw[i * 2u]);
		sample->gyro[i] = (int16_t)get_le16(&raw[6u + i * 2u]);
	}
	batch_temperature = (int16_t)get_le16(&raw[24]);

	if (batch_count >= target_batch) {
		flush_batch();
	}
}

static void pull_once(uint32_t timestamp_us)
{
	uint8_t raw[JY61P_BURST_LENGTH];
	int ret;

	atomic_inc(&imu_pulls);
	k_mutex_lock(&i2c_lock, K_FOREVER);
	ret = jy61p_read(JY61P_BURST_REGISTER, raw, sizeof(raw));
	k_mutex_unlock(&i2c_lock);
	if (ret != 0) {
		atomic_inc(&imu_i2c_errors);
		return;
	}
	accept_sample(raw, timestamp_us);
}

static void imu_thread(void *unused1, void *unused2, void *unused3)
{
	ARG_UNUSED(unused1);
	ARG_UNUSED(unused2);
	ARG_UNUSED(unused3);

	while (true) {
		uint64_t next_deadline;

		k_sem_take(&start_sem, K_FOREVER);
		next_deadline = bsf_time_now_us();

		while (atomic_get(&imu_active) != 0) {
			uint64_t now = bsf_time_now_us();
			uint32_t period_us =
				1000000u / (uint32_t)atomic_get(&imu_rate_hz);

			if (now < next_deadline) {
				uint64_t remaining = next_deadline - now;

				k_usleep((int32_t)MIN(remaining, (uint64_t)INT32_MAX));
				continue;
			}

			pull_once((uint32_t)bsf_time_now_us());
			next_deadline += period_us;
			now = bsf_time_now_us();
			if (now > next_deadline &&
			    now - next_deadline > period_us) {
				next_deadline = now + period_us;
			}
		}

		flush_batch();
		k_sem_give(&stopped_sem);
	}
}

K_THREAD_DEFINE(imu_thread_id, IMU_THREAD_STACK_SIZE,
		imu_thread, NULL, NULL, NULL,
		IMU_THREAD_PRIORITY, 0, 0);

int bsf_imu_init(bsf_imu_publish_fn publish)
{
	int ret;

	if (!device_is_ready(imu_i2c)) {
		return -ENODEV;
	}
	publish_record = publish;

	/* JY61P startup time is at least one second. Boot performs only this
	 * four-register verification; sampling remains silent until IMU START. */
	k_sleep(K_SECONDS(1));
	k_mutex_lock(&i2c_lock, K_FOREVER);
	ret = verify_registers();
	k_mutex_unlock(&i2c_lock);
	if (ret != 0) {
		LOG_WRN("JY61P boot verify unavailable: %d", ret);
		return 0;
	}
	if (!verify_pass) {
		LOG_WRN("JY61P boot verify mismatch: 61=%04x 63=%04x 03=%04x 1f=%04x",
			verified_gyrocalithr, verified_gyrocalitime,
			verified_rrate, verified_bandwidth);
	} else {
		LOG_INF("JY61P boot verify PASS: 61=%04x 63=%04x 03=%04x 1f=%04x",
			verified_gyrocalithr, verified_gyrocalitime,
			verified_rrate, verified_bandwidth);
	}
	return 0;
}

int bsf_imu_start(void)
{
	if (atomic_cas(&imu_active, 0, 1) == false) {
		return -EALREADY;
	}
	have_last_motion_sample = false;
	batch_count = 0u;
	k_sem_reset(&stopped_sem);
	k_sem_give(&start_sem);
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
		snprintf(reply, reply_size, "IMU PROVISION FAIL step=%s err=%d",
			 steps[failed_step].name, ret);
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
	snprintf(reply, reply_size,
		 "IMU active=%u rate=%u batch=%u verify=%s 61=%04X 63=%04X 03=%04X 1F=%04X pulls=%u dup=%u i2c_err=%u records=%u",
		 atomic_get(&imu_active) != 0,
		 (uint16_t)atomic_get(&imu_rate_hz),
		 (uint8_t)atomic_get(&imu_batch_size),
		 verify_pass ? "PASS" : "WARN",
		 verified_gyrocalithr, verified_gyrocalitime,
		 verified_rrate, verified_bandwidth,
		 (uint32_t)atomic_get(&imu_pulls),
		 (uint32_t)atomic_get(&imu_duplicate_samples),
		 (uint32_t)atomic_get(&imu_i2c_errors),
		 (uint32_t)atomic_get(&imu_records));
}

void bsf_imu_get_stats(struct bsf_imu_stats *stats)
{
	*stats = (struct bsf_imu_stats) {
		.pulls = (uint32_t)atomic_get(&imu_pulls),
		.duplicate_samples =
			(uint32_t)atomic_get(&imu_duplicate_samples),
		.i2c_errors = (uint32_t)atomic_get(&imu_i2c_errors),
		.records = (uint32_t)atomic_get(&imu_records),
		.rate_hz = (uint16_t)atomic_get(&imu_rate_hz),
		.batch_size = (uint8_t)atomic_get(&imu_batch_size),
		.active = (uint8_t)(atomic_get(&imu_active) != 0),
		.verify_pass = (uint8_t)verify_pass,
		.gyrocalithr = verified_gyrocalithr,
		.gyrocalitime = verified_gyrocalitime,
		.rrate = verified_rrate,
		.bandwidth = verified_bandwidth,
	};
}

void bsf_imu_clear_counters(void)
{
	atomic_set(&imu_pulls, 0);
	atomic_set(&imu_duplicate_samples, 0);
	atomic_set(&imu_i2c_errors, 0);
	atomic_set(&imu_records, 0);
}
