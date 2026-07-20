/*
 * BioSpur Fusion B306 -> Fusion Master diagnostic data contract.
 *
 * This is an internal BLE contract owned by Task B. The UWB UART contract
 * remains biospur_link.h v2 and is not changed by this header.
 */

#ifndef BIOSPUR_FUSION_BLE_H
#define BIOSPUR_FUSION_BLE_H

#include <stdint.h>

#include "biospur_link.h"

#ifdef __cplusplus
extern "C" {
#endif

#define BSF_BLE_PROTOCOL_VERSION 1u

#define BSF_BLE_KIND_UWB        1u
#define BSF_BLE_KIND_TELEMETRY  2u

/*
 * 7b120001-4e77-4a71-a045-7b4d3f2a9000 service
 * 7b120002-4e77-4a71-a045-7b4d3f2a9000 UWB data
 * 7b120003-4e77-4a71-a045-7b4d3f2a9000 telemetry
 *
 * Expand with BT_UUID_128_ENCODE() after including Zephyr's uuid.h.
 */
#define BSF_BLE_UUID_SERVICE_W32   0x7b120001u
#define BSF_BLE_UUID_DATA_W32      0x7b120002u
#define BSF_BLE_UUID_TELEMETRY_W32 0x7b120003u
#define BSF_BLE_UUID_W16_1         0x4e77u
#define BSF_BLE_UUID_W16_2         0x4a71u
#define BSF_BLE_UUID_W16_3         0xa045u
#define BSF_BLE_UUID_W48           0x7b4d3f2a9000ULL

typedef struct __attribute__((packed)) {
	uint8_t version;
	uint8_t kind;
	uint16_t len;
	uint32_t node_sequence;
	uint32_t node_uptime_ms; /* diagnostic only; not the fusion timebase */
	bsl_uwb_t uwb;
} bsf_ble_uwb_packet_t;

typedef struct __attribute__((packed)) {
	uint8_t version;
	uint8_t kind;
	uint16_t len;
	uint32_t node_uptime_ms;
	uint32_t uart_bytes;
	uint32_t valid_frames;
	uint32_t crc_errors;
	uint32_t header_errors;
	uint32_t ring_dropped_bytes;
	uint32_t dropped_sweeps;
	uint32_t duplicate_sweeps;
	uint32_t out_of_order_sweeps;
	uint32_t notify_ok;
	uint32_t notify_dropped;
	uint32_t uart_restarts;
	int32_t last_uart_error;
	uint32_t last_sweep;
	uint8_t have_last_sweep;
	uint8_t data_subscribed;
	uint8_t reserved[2];
} bsf_ble_telemetry_t;

_Static_assert(sizeof(bsf_ble_uwb_packet_t) == 102u,
	       "Fusion BLE UWB packet size drifted");
_Static_assert(sizeof(bsf_ble_telemetry_t) == 64u,
	       "Fusion BLE telemetry packet size drifted");

#ifdef __cplusplus
}
#endif

#endif /* BIOSPUR_FUSION_BLE_H */
