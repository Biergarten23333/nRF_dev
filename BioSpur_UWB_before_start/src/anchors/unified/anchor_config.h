#ifndef BIOSPUR_ANCHOR_CONFIG_H_
#define BIOSPUR_ANCHOR_CONFIG_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define CONFIG_MAGIC 0xB105F00DU
#define ANCHOR_CONFIG_SCHEMA_VERSION 2U

typedef struct {
    uint32_t magic;          /* CONFIG_MAGIC */
    uint8_t schema_version;  /* schema version for forward evolution */
    uint8_t anchor_id;       /* 0=unassigned, 1..8 => A..H */
    uint8_t role;            /* 0=unset,1=master,2=matrix,3=responder */
    uint8_t flags;           /* reserved for control-plane semantics */
    uint32_t generation;     /* monotonic config generation */
    uint8_t device_uuid[16]; /* Optional, if all-zero we derive from MCU UID */
    uint32_t crc32;
} __attribute__((packed)) anchor_config_t;

enum anchor_runtime_role {
    ANCHOR_ROLE_UNSET = 0U,
    ANCHOR_ROLE_MASTER = 1U,
    ANCHOR_ROLE_MATRIX = 2U,
    ANCHOR_ROLE_RESPONDER = 3U,
};

bool anchor_config_load(anchor_config_t *out_cfg);
bool anchor_config_is_valid(const anchor_config_t *cfg);
int anchor_config_read(anchor_config_t *out_cfg);
int anchor_config_write(const anchor_config_t *cfg);
void anchor_config_get_mcu_uid(uint8_t out_uid[8]);
void anchor_config_get_device_uuid(uint8_t out_uuid[16], const anchor_config_t *cfg,
                                   bool cfg_valid);
void anchor_config_get_bs_code(const uint8_t device_uuid[16], char out_bs_code[7]);
uint32_t anchor_config_crc32(const uint8_t *data, size_t len);
bool anchor_config_label_is_valid(uint8_t anchor_id);
char anchor_config_label_char(uint8_t anchor_id);

#endif
