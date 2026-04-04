#ifndef BSGR_TX_IMU_UART_DRIVER_H_
#define BSGR_TX_IMU_UART_DRIVER_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "bsgr_protocol.h"

struct bsgr_imu_sample {
	void *fifo_reserved;
	uint32_t host_capture_ticks;
	uint16_t parser_flags;
	uint16_t raw_len;
	uint8_t raw[BSGR_IMU_MAX_RAW_FRAME_LEN];
};

int imu_uart_driver_init(void);
int imu_uart_driver_start(void);
void imu_uart_driver_stop(void);
bool imu_uart_driver_is_bound(void);
void imu_uart_driver_process(void);
int imu_uart_driver_ingest_bytes(const uint8_t *data, size_t len, uint32_t capture_ticks);
struct bsgr_imu_sample *imu_uart_driver_pop_sample(void);

#endif /* BSGR_TX_IMU_UART_DRIVER_H_ */
