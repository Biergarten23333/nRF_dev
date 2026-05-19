#pragma once

#include <stdint.h>

#define GR_PACKET_MAGIC 0xAA

#define GR_TYPE_ADS1298 'A'
#define GR_TYPE_IMU     'I'
#define GR_TYPE_GESTURE 'G'
#define GR_TYPE_STATUS  'S'
#define GR_TYPE_KEY     'K'
#define GR_TYPE_TSYNC   'T'
#define GR_TYPE_COMMAND 'C'
#define GR_TYPE_OTA     'O'

#define GR_NAME_PREFIX      "GR"
#define GR_MASTER_NAME      "GR-Master"
#define GR_ADV_MFG_MAGIC0   'G'
#define GR_ADV_MFG_MAGIC1   'R'
#define GR_ADV_MFG_VERSION  0x01

struct gr_packet_header {
	uint8_t magic;
	uint8_t type;
	uint16_t seq;
	uint16_t device_id;
	uint32_t timestamp_ms;
} __packed;

