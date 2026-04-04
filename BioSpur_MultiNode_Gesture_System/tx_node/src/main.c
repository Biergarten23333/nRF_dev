#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include "ble_link.h"
#include "dfu_auth.h"
#include "imu_uart_driver.h"
#include "packet_framer.h"
#include "tsync.h"
#include "tx_node.h"
#include "uwb_driver.h"
#include "wdt_monitor.h"

LOG_MODULE_REGISTER(tx_main, LOG_LEVEL_INF);

static struct k_work_delayable tx_service_work;
static uint16_t tx_seq;
static int64_t last_status_ms;

static void tx_submit_boot_status(enum bsgr_status_code code, uint16_t detail, uint32_t value)
{
	struct bsgr_status_payload status = {
		.status_code = code,
		.origin = BSGR_ORIGIN_SYSTEM,
		.detail = detail,
		.value = value,
	};
	struct bsgr_tx_frame frame;

	if (packet_framer_build_status(BSGR_TX_DEVICE_ID, tx_seq++, &status, &frame) == 0) {
		(void)ble_link_submit_frame(&frame);
	}
}

static void tx_drain_imu_samples(void)
{
	struct bsgr_imu_sample *sample;

	while ((sample = imu_uart_driver_pop_sample()) != NULL) {
		struct bsgr_imu_batch_meta meta = {
			.stream_flags = BSGR_STREAM_FLAG_IMU,
			.sample_count = 1U,
			.parser_flags = sample->parser_flags,
			.reserved = 0U,
			.host_capture_ticks = sample->host_capture_ticks,
			.sample_stride = sample->raw_len,
			.raw_bytes = sample->raw_len,
		};
		struct bsgr_tx_frame frame;

		if (packet_framer_build_imu(BSGR_TX_DEVICE_ID, tx_seq++, &meta,
					    sample->raw, sample->raw_len, &frame) == 0) {
			(void)ble_link_submit_frame(&frame);
		}
		k_free(sample);
	}
}

static void tx_drain_uwb_records(void)
{
	struct bsgr_uwb_record *record;

	while ((record = uwb_driver_pop_record()) != NULL) {
		struct bsgr_uwb_report_meta meta = {
			.stream_flags = BSGR_STREAM_FLAG_UWB,
			.record_count = 1U,
			.parser_flags = record->parser_flags,
			.reserved = 0U,
			.host_capture_ticks = record->host_capture_ticks,
			.record_stride = record->raw_len,
			.raw_bytes = record->raw_len,
		};
		struct bsgr_tx_frame frame;

		if (packet_framer_build_uwb(BSGR_TX_DEVICE_ID, tx_seq++, &meta,
					    record->raw, record->raw_len, &frame) == 0) {
			(void)ble_link_submit_frame(&frame);
		}
		k_free(record);
	}
}

static void tx_service_work_handler(struct k_work *work)
{
	int64_t now_ms = k_uptime_get();

	ARG_UNUSED(work);

	imu_uart_driver_process();
	uwb_driver_process();
	tx_drain_imu_samples();
	tx_drain_uwb_records();
	ble_link_schedule_drain();
	wdt_monitor_feed();

	if ((now_ms - last_status_ms) >= BSGR_TX_STATUS_PERIOD_MS) {
		tx_submit_boot_status(BSGR_STATUS_STREAM_IDLE, 0U,
				     (uint32_t)ble_link_is_connected());
		last_status_ms = now_ms;
	}

	(void)k_work_reschedule(&tx_service_work, K_MSEC(BSGR_TX_SERVICE_PERIOD_MS));
}

int tx_main_init(void)
{
	int err;

	LOG_INF("BSGR TX framework bring-up");

	dfu_auth_init();
	tsync_init();

	err = wdt_monitor_init();
	if (err != 0) {
		return err;
	}

	err = imu_uart_driver_init();
	if (err != 0) {
		LOG_WRN("IMU driver init degraded: %d", err);
	}

	err = uwb_driver_init();
	if (err != 0) {
		LOG_WRN("UWB driver init degraded: %d", err);
	}

	(void)imu_uart_driver_start();
	(void)uwb_driver_start();

	err = ble_link_init(BSGR_TX_DEVICE_ID);
	if (err != 0) {
		return err;
	}

	k_work_init_delayable(&tx_service_work, tx_service_work_handler);
	tx_seq = 0U;
	last_status_ms = 0;
	tx_submit_boot_status(BSGR_STATUS_BOOT, 0U, tsync_get_session_id());
	(void)k_work_reschedule(&tx_service_work, K_NO_WAIT);

	return 0;
}

int main(void)
{
	int err = tx_main_init();

	if (err != 0) {
		LOG_ERR("tx_main_init failed: %d", err);
		return err;
	}

	while (1) {
		k_sleep(K_SECONDS(1));
	}

	return 0;
}
