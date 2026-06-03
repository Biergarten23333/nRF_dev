#include "anchor_cir_output.h"

#include "anchor_ble_ctrl.h"
#include "uwb_ss_twr_shared.h"

#include <string.h>

#include <deca_regs.h>
#include <zephyr/sys/util.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/atomic.h>

#ifndef APP_ANCHOR_CIR_FEATURE_OUTPUT_ENABLE
#define APP_ANCHOR_CIR_FEATURE_OUTPUT_ENABLE 0U
#endif

#ifndef APP_ANCHOR_CIR_FEATURE_OUTPUT_BLE_ENABLE
#define APP_ANCHOR_CIR_FEATURE_OUTPUT_BLE_ENABLE 1U
#endif

#ifndef APP_ANCHOR_CIR_FEATURE_OUTPUT_CDC_ENABLE
#define APP_ANCHOR_CIR_FEATURE_OUTPUT_CDC_ENABLE 0U
#endif

#ifndef APP_ANCHOR_CIR_FULL_OUTPUT_ENABLE
#define APP_ANCHOR_CIR_FULL_OUTPUT_ENABLE 0U
#endif

#ifndef APP_ANCHOR_CIR_FULL_OUTPUT_CDC_ENABLE
#define APP_ANCHOR_CIR_FULL_OUTPUT_CDC_ENABLE 1U
#endif

#ifndef APP_ANCHOR_CIR_FULL_CHUNK_BYTES
#define APP_ANCHOR_CIR_FULL_CHUNK_BYTES 48U
#endif

#define ANCHOR_CIR_ACC_DATA_LEN ACC_MEM_LEN

static atomic_t g_anchor_cir_output_mode;

void anchor_cir_output_set_mode(enum anchor_cir_output_mode mode)
{
    if (mode != ANCHOR_CIR_OUTPUT_COMPACT &&
        mode != ANCHOR_CIR_OUTPUT_FULL) {
        mode = ANCHOR_CIR_OUTPUT_OFF;
    }
    atomic_set(&g_anchor_cir_output_mode, (atomic_val_t)mode);
}

enum anchor_cir_output_mode anchor_cir_output_get_mode(void)
{
    atomic_val_t value = atomic_get(&g_anchor_cir_output_mode);

    if (value == ANCHOR_CIR_OUTPUT_COMPACT ||
        value == ANCHOR_CIR_OUTPUT_FULL) {
        return (enum anchor_cir_output_mode)value;
    }
    return ANCHOR_CIR_OUTPUT_OFF;
}

int anchor_cir_output_parse_mode(const char *text, enum anchor_cir_output_mode *mode)
{
    const char *value = text;

    if (text == NULL || mode == NULL) {
        return -1;
    }
    if (strncmp(text, "CIR=", 4) == 0 || strncmp(text, "cir=", 4) == 0) {
        value = text + 4;
    }
    if (strcmp(value, "0") == 0 || strcmp(value, "OFF") == 0 ||
        strcmp(value, "off") == 0 || strcmp(value, "NONE") == 0 ||
        strcmp(value, "none") == 0) {
        *mode = ANCHOR_CIR_OUTPUT_OFF;
        return 0;
    }
    if (strcmp(value, "COMPACT") == 0 || strcmp(value, "compact") == 0 ||
        strcmp(value, "FEATURE") == 0 || strcmp(value, "feature") == 0 ||
        strcmp(value, "1") == 0) {
        *mode = ANCHOR_CIR_OUTPUT_COMPACT;
        return 0;
    }
    if (strcmp(value, "FULL") == 0 || strcmp(value, "full") == 0 ||
        strcmp(value, "RAW") == 0 || strcmp(value, "raw") == 0 ||
        strcmp(value, "2") == 0) {
        *mode = ANCHOR_CIR_OUTPUT_FULL;
        return 0;
    }
    return -1;
}

static void anchor_cir_source_info(uint16_t source_addr, char *kind,
                                   uint8_t *source_id)
{
    if (kind == NULL || source_id == NULL) {
        return;
    }

    if (uwb_short_addr_is_anchor(source_addr)) {
        *kind = 'A';
        *source_id = uwb_anchor_id_from_addr(source_addr);
    } else if (uwb_short_addr_is_tag(source_addr)) {
        *kind = 'T';
        *source_id = uwb_tag_id_from_addr(source_addr);
    } else {
        *kind = 'U';
        *source_id = 0xffU;
    }
}

void anchor_cir_output_publish_feature(uint32_t seq,
                                       uint8_t receiver_anchor_id,
                                       uint16_t source_addr,
                                       long raw_distance_mm,
                                       uint32_t rx_timestamp,
                                       int32_t carrier_integrator,
                                       const dwt_rxdiag_t *diag)
{
#if APP_ANCHOR_CIR_FEATURE_OUTPUT_ENABLE != 0U
    char line[224];
    char source_kind = 'U';
    uint8_t source_id = 0xffU;

    if (anchor_cir_output_get_mode() != ANCHOR_CIR_OUTPUT_COMPACT) {
        return;
    }
    if (diag == NULL || receiver_anchor_id >= UWB_MAX_ANCHORS) {
        return;
    }

    anchor_cir_source_info(source_addr, &source_kind, &source_id);
    snprintk(line, sizeof(line),
             "ACRX;1;%lu;%u;%c;%u;0x%04x;%ld;%lu;%ld;%u;%u;%u;%u;%u;%u;%u;%u",
             (unsigned long)seq,
             (unsigned int)receiver_anchor_id,
             source_kind,
             (unsigned int)source_id,
             (unsigned int)source_addr,
             raw_distance_mm,
             (unsigned long)rx_timestamp,
             (long)carrier_integrator,
             (unsigned int)diag->firstPath,
             (unsigned int)diag->firstPathAmp1,
             (unsigned int)diag->firstPathAmp2,
             (unsigned int)diag->firstPathAmp3,
             (unsigned int)diag->maxGrowthCIR,
             (unsigned int)diag->rxPreamCount,
             (unsigned int)diag->stdNoise,
             (unsigned int)diag->maxNoise);

#if APP_ANCHOR_CIR_FEATURE_OUTPUT_CDC_ENABLE != 0U
    printk("%s\n", line);
#endif
#if APP_ANCHOR_CIR_FEATURE_OUTPUT_BLE_ENABLE != 0U
    (void)anchor_ble_ctrl_publish_result_line(line);
#endif
#else
    ARG_UNUSED(seq);
    ARG_UNUSED(receiver_anchor_id);
    ARG_UNUSED(source_addr);
    ARG_UNUSED(raw_distance_mm);
    ARG_UNUSED(rx_timestamp);
    ARG_UNUSED(carrier_integrator);
    ARG_UNUSED(diag);
#endif
}

void anchor_cir_output_publish_full(uint32_t seq,
                                    uint8_t receiver_anchor_id,
                                    uint16_t source_addr,
                                    long raw_distance_mm,
                                    uint32_t rx_timestamp,
                                    int32_t carrier_integrator,
                                    const dwt_rxdiag_t *diag)
{
#if APP_ANCHOR_CIR_FULL_OUTPUT_ENABLE != 0U && \
    APP_ANCHOR_CIR_FULL_OUTPUT_CDC_ENABLE != 0U
    static const char hex[] = "0123456789ABCDEF";
    uint8_t chunk[49];
    uint16_t offset = 0U;
    uint16_t chunk_bytes = APP_ANCHOR_CIR_FULL_CHUNK_BYTES;
    char source_kind = 'U';
    uint8_t source_id = 0xffU;
    uint16_t fp_amp_sum;

    if (anchor_cir_output_get_mode() != ANCHOR_CIR_OUTPUT_FULL) {
        return;
    }
    if (diag == NULL || receiver_anchor_id >= UWB_MAX_ANCHORS) {
        return;
    }

    if (chunk_bytes == 0U || chunk_bytes > 48U) {
        chunk_bytes = 48U;
    }

    anchor_cir_source_info(source_addr, &source_kind, &source_id);
    fp_amp_sum = (uint16_t)(diag->firstPathAmp1 + diag->firstPathAmp2 +
                            diag->firstPathAmp3);

    printk("ACIRM;1;%lu;%u;%c;%u;0x%04x;%ld;%lu;%ld;%u;%u;%u;%u;%u\n",
           (unsigned long)seq,
           (unsigned int)receiver_anchor_id,
           source_kind,
           (unsigned int)source_id,
           (unsigned int)source_addr,
           raw_distance_mm,
           (unsigned long)rx_timestamp,
           (long)carrier_integrator,
           (unsigned int)diag->firstPath,
           (unsigned int)fp_amp_sum,
           (unsigned int)diag->maxGrowthCIR,
           (unsigned int)diag->stdNoise,
           (unsigned int)ANCHOR_CIR_ACC_DATA_LEN);

    while (offset < ANCHOR_CIR_ACC_DATA_LEN) {
        uint16_t len =
            MIN(chunk_bytes, (uint16_t)(ANCHOR_CIR_ACC_DATA_LEN - offset));
        char line[160];
        size_t pos = 0U;

        memset(chunk, 0, (size_t)len + 1U);
        dwt_readaccdata(chunk, (uint16)(len + 1U), offset);

        pos += (size_t)snprintk(line + pos, sizeof(line) - pos,
                                "ACIRD;1;%lu;%u;%c;%u;%u;%u;",
                                (unsigned long)seq,
                                (unsigned int)receiver_anchor_id,
                                source_kind,
                                (unsigned int)source_id,
                                (unsigned int)offset,
                                (unsigned int)len);
        for (uint16_t i = 0U; i < len && pos + 2U < sizeof(line); ++i) {
            uint8_t b = chunk[1U + i];
            line[pos++] = hex[(b >> 4) & 0x0fU];
            line[pos++] = hex[b & 0x0fU];
        }
        line[pos < sizeof(line) ? pos : sizeof(line) - 1U] = '\0';
        printk("%s\n", line);
        offset = (uint16_t)(offset + len);
    }

    printk("ACIRE;1;%lu;%u;%c;%u;%u\n",
           (unsigned long)seq,
           (unsigned int)receiver_anchor_id,
           source_kind,
           (unsigned int)source_id,
           (unsigned int)ANCHOR_CIR_ACC_DATA_LEN);
#else
    ARG_UNUSED(seq);
    ARG_UNUSED(receiver_anchor_id);
    ARG_UNUSED(source_addr);
    ARG_UNUSED(raw_distance_mm);
    ARG_UNUSED(rx_timestamp);
    ARG_UNUSED(carrier_integrator);
    ARG_UNUSED(diag);
#endif
}
