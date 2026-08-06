#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "../../../include/biospur_fusion_ble.h"
#include "../../include/host_binary_protocol.h"

static void check_batch(uint8_t count)
{
	uint8_t ble[BSF_IMU_RECORD_LEN(BSF_IMU_BATCH_MAX)] = { 0 };
	uint8_t host[sizeof(bsf_host_imu_prefix_t) +
		     BSF_IMU_BATCH_MAX * sizeof(bsf_ble_imu_sample_t)] = { 0 };
	bsf_ble_imu_prefix_t prefix = {
		.version = BSF_BLE_PROTOCOL_VERSION,
		.kind = BSF_BLE_KIND_IMU,
		.len = (uint16_t)BSF_IMU_RECORD_LEN(count),
		.seq = 0xfff8u,
		.base_timer2_ts_us = 0xfffffff0u,
	};
	bsf_host_imu_prefix_t host_prefix = {
		.ble_version = prefix.version,
		.sample_count = count,
		.sequence = prefix.seq,
		.base_timer2_ts_us = 0x12345678fffffff0ULL,
		.temperature_raw = 321,
	};
	size_t offset = sizeof(prefix);

	memcpy(ble, &prefix, sizeof(prefix));
	for (uint8_t i = 0u; i < count; ++i) {
		bsf_ble_imu_sample_t sample = {
			.delta_us = (uint16_t)(i * 5000u),
			.acc = { i, (int16_t)-i, (int16_t)(100 + i) },
			.gyro = { (int16_t)(200 + i), (int16_t)-200, i },
		};

		memcpy(&ble[offset], &sample, sizeof(sample));
		offset += sizeof(sample);
	}
	memcpy(&ble[offset], &host_prefix.temperature_raw,
	       sizeof(host_prefix.temperature_raw));
	offset += sizeof(host_prefix.temperature_raw);
	assert(offset == BSF_IMU_RECORD_LEN(count));
	assert(prefix.len == offset);

	memcpy(host, &host_prefix, sizeof(host_prefix));
	memcpy(&host[sizeof(host_prefix)], &ble[sizeof(prefix)],
	       (size_t)count * sizeof(bsf_ble_imu_sample_t));
	assert(sizeof(host_prefix) +
	       (size_t)count * sizeof(bsf_ble_imu_sample_t) == 14u + 14u * count);
	for (uint8_t i = 0u; i < count; ++i) {
		bsf_ble_imu_sample_t sample;

		memcpy(&sample,
		       &host[sizeof(host_prefix) +
			     (size_t)i * sizeof(sample)],
		       sizeof(sample));
		assert(sample.delta_us == (uint16_t)(i * 5000u));
		assert(sample.acc[0] == i);
		assert(sample.gyro[0] == 200 + i);
	}
}

int main(void)
{
	check_batch(5u);
	check_batch(8u);
	check_batch(10u);
	check_batch(16u);
	assert(BSF_IMU_RECORD_LEN(5u) == 82u);
	assert(BSF_IMU_RECORD_LEN(8u) == 124u);
	assert(BSF_IMU_RECORD_LEN(10u) == 152u);
	assert(BSF_IMU_RECORD_LEN(16u) == 236u);
	assert(BSF_IMU_BATCH_MAX == 16u);
	assert(sizeof(bsf_host_imu_prefix_t) +
	       5u * sizeof(bsf_ble_imu_sample_t) == 84u);
	assert(sizeof(bsf_host_imu_prefix_t) +
	       8u * sizeof(bsf_ble_imu_sample_t) == 126u);
	assert(sizeof(bsf_host_imu_prefix_t) +
	       10u * sizeof(bsf_ble_imu_sample_t) == 154u);
	puts("IMU batch 5/8/10/16 round-trip tests passed");
	return 0;
}
