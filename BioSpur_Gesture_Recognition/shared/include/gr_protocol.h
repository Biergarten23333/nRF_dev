#pragma once

#include <stdint.h>

#ifndef GR_PACKED
#define GR_PACKED __attribute__((__packed__))
#endif

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
#define GR_MASTER_NAME      "BioSpur-GR"
#define GR_ADV_MFG_MAGIC0   'G'
#define GR_ADV_MFG_MAGIC1   'R'
#define GR_ADV_MFG_VERSION  0x01

struct gr_packet_header {
	uint8_t magic;
	uint8_t type;
	uint16_t seq;
	uint16_t device_id;
	uint32_t timestamp_ms;
} GR_PACKED;
