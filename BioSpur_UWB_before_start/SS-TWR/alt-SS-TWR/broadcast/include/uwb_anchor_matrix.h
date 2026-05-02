#ifndef UWB_ANCHOR_MATRIX_H
#define UWB_ANCHOR_MATRIX_H

#include <stdbool.h>
#include <stdint.h>

#include "uwb_range_tracker.h"
#include "uwb_ss_twr_shared.h"

struct uwb_anchor_matrix_cell {
    bool valid;
    uint32_t filtered_mm;
    uint32_t last_raw_mm;
    uint32_t success_count;
    uint32_t failure_count;
    uint8_t quality_percent;
};

struct uwb_anchor_matrix {
    struct uwb_anchor_matrix_cell cells[UWB_MAX_ANCHORS][UWB_MAX_ANCHORS];
};

void uwb_anchor_matrix_init(struct uwb_anchor_matrix *matrix);
void uwb_anchor_matrix_update(struct uwb_anchor_matrix *matrix,
                              uint8_t anchor_a_id, uint8_t anchor_b_id,
                              const struct uwb_range_tracker *tracker);
void uwb_anchor_matrix_print_row(const struct uwb_anchor_matrix *matrix,
                                 uint8_t anchor_id);
void uwb_anchor_matrix_print_valid_edges(const struct uwb_anchor_matrix *matrix);

#endif /* UWB_ANCHOR_MATRIX_H */
