#include "anchor_config.h"

#include <errno.h>
#include <hal/nrf_ficr.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/flash.h>
#include <zephyr/sys/util.h>
#include <string.h>
#include <zephyr/sys/printk.h>

#ifndef APP_ANCHOR_CONFIG_ADDR
#define APP_ANCHOR_CONFIG_ADDR 0x0007E000U
#endif

#ifndef APP_ANCHOR_CONFIG_PAGE_SIZE
#define APP_ANCHOR_CONFIG_PAGE_SIZE 4096U
#endif

typedef struct {
    uint32_t magic;
    uint8_t anchor_id;       /* 1..8 only */
    uint8_t role;            /* 0..3 */
    uint8_t reserved[2];
    uint8_t device_uuid[16];
    uint32_t crc32;
} __attribute__((packed)) anchor_config_v1_legacy_t;

static const struct device *anchor_flash_dev(void)
{
    return DEVICE_DT_GET(DT_CHOSEN(zephyr_flash_controller));
}

static bool uuid_is_zero(const uint8_t *uuid, size_t len)
{
    size_t i;
    for (i = 0; i < len; i++) {
        if (uuid[i] != 0U) {
            return false;
        }
    }
    return true;
}

uint32_t anchor_config_crc32(const uint8_t *data, size_t len)
{
    uint32_t crc = 0xFFFFFFFFU;
    size_t i;
    int bit;

    for (i = 0; i < len; i++) {
        crc ^= data[i];
        for (bit = 0; bit < 8; bit++) {
            uint32_t mask = (uint32_t)-(int32_t)(crc & 1U);
            crc = (crc >> 1) ^ (0xEDB88320U & mask);
        }
    }
    return ~crc;
}

bool anchor_config_label_is_valid(uint8_t anchor_id)
{
    return anchor_id <= 8U;
}

char anchor_config_label_char(uint8_t anchor_id)
{
    if (anchor_id == 0U) {
        return 'U';
    }
    if (anchor_id <= 8U) {
        return (char)('A' + (anchor_id - 1U));
    }
    return '?';
}

void anchor_config_get_mcu_uid(uint8_t out_uid[8])
{
    const uint32_t seed0 = NRF_FICR->DEVICEID[0];
    const uint32_t seed1 = NRF_FICR->DEVICEID[1];

    out_uid[0] = (uint8_t)(seed0 & 0xFFU);
    out_uid[1] = (uint8_t)((seed0 >> 8) & 0xFFU);
    out_uid[2] = (uint8_t)((seed0 >> 16) & 0xFFU);
    out_uid[3] = (uint8_t)((seed0 >> 24) & 0xFFU);
    out_uid[4] = (uint8_t)(seed1 & 0xFFU);
    out_uid[5] = (uint8_t)((seed1 >> 8) & 0xFFU);
    out_uid[6] = (uint8_t)((seed1 >> 16) & 0xFFU);
    out_uid[7] = (uint8_t)((seed1 >> 24) & 0xFFU);
}

void anchor_config_get_device_uuid(uint8_t out_uuid[16], const anchor_config_t *cfg,
                                   bool cfg_valid)
{
    uint8_t uid[8];
    size_t i;

    if (cfg_valid && cfg != NULL && !uuid_is_zero(cfg->device_uuid, sizeof(cfg->device_uuid))) {
        memcpy(out_uuid, cfg->device_uuid, sizeof(cfg->device_uuid));
        return;
    }

    anchor_config_get_mcu_uid(uid);
    for (i = 0; i < 8; i++) {
        out_uuid[i] = uid[i];
        out_uuid[i + 8] = (uint8_t)(uid[i] ^ (uint8_t)(0xA5U + (uint8_t)i));
    }
}

void anchor_config_get_bs_code(const uint8_t device_uuid[16], char out_bs_code[7])
{
    uint16_t crc = 0xFFFFU;
    size_t i;
    int bit;

    for (i = 0; i < 16U; i++) {
        crc ^= (uint16_t)device_uuid[i] << 8;
        for (bit = 0; bit < 8; bit++) {
            if ((crc & 0x8000U) != 0U) {
                crc = (uint16_t)((crc << 1) ^ 0x1021U);
            } else {
                crc <<= 1;
            }
        }
    }

    snprintk(out_bs_code, 7, "BS%04X", (unsigned int)crc);
}

bool anchor_config_load(anchor_config_t *out_cfg)
{
    anchor_config_t cfg = {0};
    int rc;

    if (out_cfg == NULL) {
        return false;
    }

    rc = anchor_config_read(&cfg);
    if (rc != 0) {
        return false;
    }
    if (!anchor_config_is_valid(&cfg)) {
        return false;
    }

    *out_cfg = cfg;
    return true;
}

bool anchor_config_is_valid(const anchor_config_t *cfg)
{
    uint32_t crc_calc;

    if (cfg == NULL) {
        return false;
    }
    if (cfg->magic != CONFIG_MAGIC) {
        return false;
    }
    if (cfg->schema_version == 0U || cfg->schema_version > ANCHOR_CONFIG_SCHEMA_VERSION) {
        return false;
    }
    if (!anchor_config_label_is_valid(cfg->anchor_id)) {
        return false;
    }
    if (cfg->role > ANCHOR_ROLE_RESPONDER) {
        return false;
    }

    crc_calc = anchor_config_crc32((const uint8_t *)cfg,
                                   offsetof(anchor_config_t, crc32));
    if (crc_calc != cfg->crc32) {
        return false;
    }
    return true;
}

int anchor_config_read(anchor_config_t *out_cfg)
{
    const struct device *flash_dev = anchor_flash_dev();
    uint8_t raw[sizeof(anchor_config_t)] = {0};
    anchor_config_t v2 = {0};
    anchor_config_v1_legacy_t v1 = {0};
    uint32_t crc_v1;
    uint32_t crc_v2;
    int rc;

    if (out_cfg == NULL) {
        return -EINVAL;
    }
    if (!device_is_ready(flash_dev)) {
        return -ENODEV;
    }
    rc = flash_read(flash_dev, APP_ANCHOR_CONFIG_ADDR, raw, sizeof(raw));
    if (rc != 0) {
        return rc;
    }

    memcpy(&v2, raw, sizeof(v2));
    crc_v2 = anchor_config_crc32((const uint8_t *)&v2, offsetof(anchor_config_t, crc32));
    if (v2.magic == CONFIG_MAGIC && crc_v2 == v2.crc32 &&
        anchor_config_label_is_valid(v2.anchor_id) && v2.role <= ANCHOR_ROLE_RESPONDER &&
        v2.schema_version >= 1U && v2.schema_version <= ANCHOR_CONFIG_SCHEMA_VERSION) {
        *out_cfg = v2;
        return 0;
    }

    memcpy(&v1, raw, sizeof(v1));
    crc_v1 = anchor_config_crc32((const uint8_t *)&v1, offsetof(anchor_config_v1_legacy_t, crc32));
    if (v1.magic == CONFIG_MAGIC && crc_v1 == v1.crc32 &&
        v1.anchor_id >= 1U && v1.anchor_id <= 8U && v1.role <= ANCHOR_ROLE_RESPONDER) {
        memset(out_cfg, 0, sizeof(*out_cfg));
        out_cfg->magic = CONFIG_MAGIC;
        out_cfg->schema_version = 1U;
        out_cfg->anchor_id = v1.anchor_id;
        out_cfg->role = v1.role;
        out_cfg->flags = 0U;
        out_cfg->generation = 0U;
        memcpy(out_cfg->device_uuid, v1.device_uuid, sizeof(out_cfg->device_uuid));
        out_cfg->crc32 = anchor_config_crc32((const uint8_t *)out_cfg,
                                             offsetof(anchor_config_t, crc32));
        return 0;
    }

    memset(out_cfg, 0, sizeof(*out_cfg));
    return -EILSEQ;
}

int anchor_config_write(const anchor_config_t *cfg)
{
    const struct device *flash_dev = anchor_flash_dev();
    uint8_t wr_buf[ROUND_UP(sizeof(anchor_config_t), 4)];
    anchor_config_t verify_cfg = {0};
    size_t wr_len = sizeof(wr_buf);
    int rc;

    if (cfg == NULL) {
        return -EINVAL;
    }
    if (!device_is_ready(flash_dev)) {
        return -ENODEV;
    }
    if (!anchor_config_is_valid(cfg)) {
        return -EINVAL;
    }

    memset(wr_buf, 0xFF, sizeof(wr_buf));
    memcpy(wr_buf, cfg, sizeof(anchor_config_t));

    rc = flash_erase(flash_dev, APP_ANCHOR_CONFIG_ADDR, APP_ANCHOR_CONFIG_PAGE_SIZE);
    if (rc != 0) {
        return rc;
    }

    rc = flash_write(flash_dev, APP_ANCHOR_CONFIG_ADDR, wr_buf, wr_len);
    if (rc != 0) {
        return rc;
    }

    rc = anchor_config_read(&verify_cfg);
    if (rc != 0) {
        return rc;
    }
    if (memcmp(&verify_cfg, cfg, sizeof(anchor_config_t)) != 0) {
        return -EIO;
    }
    if (!anchor_config_is_valid(&verify_cfg)) {
        return -EILSEQ;
    }
    return 0;
}
