#include "uwb_anchor_matrix.h"

#include "uwb_anchor_topology.h"

#include <string.h>

#include <zephyr/sys/printk.h>

void uwb_anchor_matrix_init(struct uwb_anchor_matrix *matrix)
{
    memset(matrix, 0, sizeof(*matrix));
}

void uwb_anchor_matrix_update(struct uwb_anchor_matrix *matrix,
                              uint8_t anchor_a_id, uint8_t anchor_b_id,
                              const struct uwb_range_tracker *tracker)
{
    struct uwb_anchor_matrix_cell *cell_ab;
    struct uwb_anchor_matrix_cell *cell_ba;

    if (matrix == NULL || tracker == NULL || anchor_a_id >= UWB_MAX_ANCHORS ||
        anchor_b_id >= UWB_MAX_ANCHORS || anchor_a_id == anchor_b_id) {
        return;
    }

    cell_ab = &matrix->cells[anchor_a_id][anchor_b_id];
    cell_ba = &matrix->cells[anchor_b_id][anchor_a_id];

    cell_ab->valid = tracker->filtered_valid;
    cell_ab->filtered_mm = tracker->filtered_mm;
    cell_ab->last_raw_mm = tracker->last_raw_mm;
    cell_ab->success_count = tracker->success_count;
    cell_ab->failure_count = tracker->failure_count;
    cell_ab->quality_percent = uwb_range_tracker_quality_percent(tracker);

    *cell_ba = *cell_ab;
}

void uwb_anchor_matrix_print_row(const struct uwb_anchor_matrix *matrix,
                                 uint8_t anchor_id)
{
    if (matrix == NULL || anchor_id >= UWB_MAX_ANCHORS) {
        return;
    }

    printk("Matrix row %c:", uwb_anchor_label(anchor_id));

    for (uint8_t peer_id = 0U; peer_id < UWB_MAX_ANCHORS; ++peer_id) {
        const struct uwb_anchor_matrix_cell *cell;

        if (peer_id == anchor_id) {
            continue;
        }

        cell = &matrix->cells[anchor_id][peer_id];

        if (cell->valid) {
            printk(" %c=%lu", uwb_anchor_label(peer_id),
                   (unsigned long)cell->filtered_mm);
        } else {
            printk(" %c=--", uwb_anchor_label(peer_id));
        }
    }

    printk(" mm\n");
}

void uwb_anchor_matrix_print_valid_edges(const struct uwb_anchor_matrix *matrix)
{
    if (matrix == NULL) {
        return;
    }

    printk("Valid edges:");

    for (uint8_t row = 0U; row < UWB_MAX_ANCHORS; ++row) {
        for (uint8_t col = (uint8_t)(row + 1U); col < UWB_MAX_ANCHORS; ++col) {
            const struct uwb_anchor_matrix_cell *cell = &matrix->cells[row][col];

            if (!cell->valid) {
                continue;
            }

            printk(" %c-%c=%lu", uwb_anchor_label(row), uwb_anchor_label(col),
                   (unsigned long)cell->filtered_mm);
        }
    }

    printk(" mm\n");
}
