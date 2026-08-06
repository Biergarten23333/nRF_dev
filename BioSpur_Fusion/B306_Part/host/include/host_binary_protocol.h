#ifndef BIOSPUR_HOST_BINARY_PROTOCOL_H_
#define BIOSPUR_HOST_BINARY_PROTOCOL_H_

#include <stddef.h>
#include <stdint.h>

#define BSF_HOST_FRAME_MAGIC 0x5342u
#define BSF_HOST_FRAME_VERSION 1u
#define BSF_HOST_FRAME_MAX_PAYLOAD 512u
#define BSF_HOST_FRAME_MAX_RAW 534u
#define BSF_HOST_FRAME_MAX_ENCODED 538u

enum bsf_host_record_kind {
	BSF_HOST_RECORD_UWB = 1,
	BSF_HOST_RECORD_TELEMETRY = 2,
	BSF_HOST_RECORD_IMU = 3,
	BSF_HOST_RECORD_REPLY = 4,
	BSF_HOST_RECORD_TEXT = 5,
	BSF_HOST_RECORD_QUEUE_COUNTERS = 6,
	BSF_HOST_RECORD_QOS = 7,
	BSF_HOST_RECORD_POOL_USAGE = 8,
};

typedef struct __attribute__((packed)) {
	uint16_t magic;
	uint8_t version;
	uint8_t kind;
	uint16_t node_id;
	uint16_t payload_len;
	uint32_t sequence;
	uint64_t master_arrival_ms;
} bsf_host_frame_header_t;

typedef struct __attribute__((packed)) {
	uint8_t ble_version;
	uint8_t sample_count;
	uint16_t sequence;
	uint64_t base_timer2_ts_us;
	int16_t temperature_raw;
} bsf_host_imu_prefix_t;

typedef struct __attribute__((packed)) {
	uint8_t version;
	uint8_t spacing_mode;
	uint16_t conn_handle;
	uint32_t window_start_ms;
	uint32_t window_duration_ms;
	uint32_t spacing_us;
	uint32_t spacing_generation;
	uint32_t report_count;
	uint32_t event_counter_gap_count;
	uint32_t crc_ok_count;
	uint32_t crc_error_count;
	uint32_t nak_count;
	uint32_t rx_timeout_count;
	uint32_t imu_epoch_defer_drop;
	uint32_t delivered_imu;
	uint32_t delivered_uwb;
	uint32_t delivered_ctl;
	uint16_t first_event_counter;
	uint16_t last_event_counter;
	uint16_t channel_event_count[37];
} bsf_host_qos_t;

static inline uint16_t bsf_host_crc16(const uint8_t *data, size_t length)
{
	uint16_t crc = 0xffffu;

	for (size_t i = 0; i < length; ++i) {
		crc ^= (uint16_t)data[i] << 8;
		for (unsigned int bit = 0; bit < 8u; ++bit) {
			crc = (crc & 0x8000u) != 0u ?
				(uint16_t)((crc << 1) ^ 0x1021u) :
				(uint16_t)(crc << 1);
		}
	}
	return crc;
}

/*
 * COBS plus a zero delimiter gives an unambiguous frame boundary after every
 * record. A corrupt/truncated record cannot consume the following record.
 */
static inline size_t bsf_host_cobs_encode(const uint8_t *input,
					  size_t input_length,
					  uint8_t *output,
					  size_t output_capacity)
{
	size_t read_index = 0u;
	size_t write_index = 1u;
	size_t code_index = 0u;
	uint8_t code = 1u;

	if (output_capacity < 2u) {
		return 0u;
	}
	while (read_index < input_length) {
		if (input[read_index] == 0u) {
			if (code_index >= output_capacity) {
				return 0u;
			}
			output[code_index] = code;
			code = 1u;
			code_index = write_index++;
			if (write_index > output_capacity) {
				return 0u;
			}
			++read_index;
		} else {
			if (write_index >= output_capacity) {
				return 0u;
			}
			output[write_index++] = input[read_index++];
			if (++code == 0xffu) {
				output[code_index] = code;
				code = 1u;
				code_index = write_index++;
				if (write_index > output_capacity) {
					return 0u;
				}
			}
		}
	}
	if (code_index >= output_capacity || write_index >= output_capacity) {
		return 0u;
	}
	output[code_index] = code;
	output[write_index++] = 0u;
	return write_index;
}

static inline size_t bsf_host_cobs_decode(const uint8_t *input,
					  size_t input_length,
					  uint8_t *output,
					  size_t output_capacity)
{
	size_t read_index = 0u;
	size_t write_index = 0u;

	while (read_index < input_length) {
		uint8_t code = input[read_index++];

		if (code == 0u || read_index + (size_t)code - 1u >
					 input_length) {
			return 0u;
		}
		for (uint8_t i = 1u; i < code; ++i) {
			if (write_index >= output_capacity) {
				return 0u;
			}
			output[write_index++] = input[read_index++];
		}
		if (code != 0xffu && read_index < input_length) {
			if (write_index >= output_capacity) {
				return 0u;
			}
			output[write_index++] = 0u;
		}
	}
	return write_index;
}

_Static_assert(sizeof(bsf_host_frame_header_t) == 20u,
	       "host binary header size drifted");
_Static_assert(sizeof(bsf_host_imu_prefix_t) == 14u,
	       "host IMU prefix size drifted");
_Static_assert(sizeof(bsf_host_qos_t) == 138u,
	       "host QoS record size drifted");

#endif /* BIOSPUR_HOST_BINARY_PROTOCOL_H_ */
