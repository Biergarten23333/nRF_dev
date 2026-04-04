#include <errno.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/ring_buffer.h>

#include "imu_uart_driver.h"

LOG_MODULE_REGISTER(tx_imu_uart, LOG_LEVEL_INF);

#define BSGR_IMU_RX_RING_SIZE 256
#define BSGR_IMU_FRAME_BYTES 11
#define BSGR_IMU_SAMPLE_POOL_COUNT 8

RING_BUF_DECLARE(imu_rx_ring, BSGR_IMU_RX_RING_SIZE);
K_FIFO_DEFINE(imu_sample_fifo);
K_HEAP_DEFINE(imu_heap, BSGR_IMU_SAMPLE_POOL_COUNT * sizeof(struct bsgr_imu_sample));

static bool imu_bound;
static bool imu_started;

int imu_uart_driver_init(void)
{
	ring_buf_reset(&imu_rx_ring);
	imu_bound = false;
	imu_started = false;
	LOG_INF("IMU UART driver initialized in conservative parser mode");
	return 0;
}

int imu_uart_driver_start(void)
{
	imu_started = true;
	return imu_bound ? 0 : -ENODEV;
}

void imu_uart_driver_stop(void)
{
	imu_started = false;
}

bool imu_uart_driver_is_bound(void)
{
	return imu_bound;
}

int imu_uart_driver_ingest_bytes(const uint8_t *data, size_t len, uint32_t capture_ticks)
{
	struct bsgr_imu_sample *sample;

	if ((data == NULL) || (len == 0U)) {
		return -EINVAL;
	}

	if (len < BSGR_IMU_FRAME_BYTES) {
		return ring_buf_put(&imu_rx_ring, data, len) == len ? 0 : -ENOSPC;
	}

	sample = k_heap_alloc(&imu_heap, sizeof(*sample), K_NO_WAIT);
	if (sample == NULL) {
		return -ENOMEM;
	}

	memset(sample, 0, sizeof(*sample));
	sample->host_capture_ticks = capture_ticks;
	sample->raw_len = MIN(len, (size_t)BSGR_IMU_MAX_RAW_FRAME_LEN);
	memcpy(sample->raw, data, sample->raw_len);
	sample->parser_flags = BSGR_PARSER_FLAG_STUB_DECODE;

	if ((sample->raw_len >= BSGR_IMU_FRAME_BYTES) && (sample->raw[0] == 0x55U)) {
		sample->parser_flags |= BSGR_PARSER_FLAG_CANDIDATE_FRAME;
	}

	k_fifo_put(&imu_sample_fifo, sample);
	return 0;
}

void imu_uart_driver_process(void)
{
	uint8_t candidate[BSGR_IMU_FRAME_BYTES];
	uint32_t got;

	if (!imu_started) {
		return;
	}

	while (ring_buf_size_get(&imu_rx_ring) >= BSGR_IMU_FRAME_BYTES) {
		got = ring_buf_get(&imu_rx_ring, candidate, sizeof(candidate));
		if (got != sizeof(candidate)) {
			break;
		}

		(void)imu_uart_driver_ingest_bytes(candidate, sizeof(candidate), k_uptime_ticks());
	}
}

struct bsgr_imu_sample *imu_uart_driver_pop_sample(void)
{
	return k_fifo_get(&imu_sample_fifo, K_NO_WAIT);
}
