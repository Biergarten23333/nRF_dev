#ifndef BIOSPUR_ANCHOR_CIR_OUTPUT_H_
#define BIOSPUR_ANCHOR_CIR_OUTPUT_H_

#include <stdint.h>

#include <deca_device_api.h>

enum anchor_cir_output_mode {
    ANCHOR_CIR_OUTPUT_OFF = 0,
    ANCHOR_CIR_OUTPUT_COMPACT = 1,
    ANCHOR_CIR_OUTPUT_FULL = 2,
};

void anchor_cir_output_set_mode(enum anchor_cir_output_mode mode);
enum anchor_cir_output_mode anchor_cir_output_get_mode(void);
int anchor_cir_output_parse_mode(const char *text, enum anchor_cir_output_mode *mode);

void anchor_cir_output_publish_feature(uint32_t seq,
                                       uint8_t receiver_anchor_id,
                                       uint16_t source_addr,
                                       long raw_distance_mm,
                                       uint32_t rx_timestamp,
                                       int32_t carrier_integrator,
                                       const dwt_rxdiag_t *diag);

void anchor_cir_output_publish_full(uint32_t seq,
                                    uint8_t receiver_anchor_id,
                                    uint16_t source_addr,
                                    long raw_distance_mm,
                                    uint32_t rx_timestamp,
                                    int32_t carrier_integrator,
                                    const dwt_rxdiag_t *diag);

#endif /* BIOSPUR_ANCHOR_CIR_OUTPUT_H_ */
