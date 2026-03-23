#ifndef UWB_RANGE_TRACKER_H
#define UWB_RANGE_TRACKER_H

#include <stdbool.h>
#include <stdint.h>

#define UWB_RANGE_TRACKER_WINDOW_SIZE 3U
#define UWB_RANGE_TRACKER_QUALITY_WINDOW 32U

struct uwb_range_tracker {
    uint16_t peer_short_addr;
    uint32_t raw_window[UWB_RANGE_TRACKER_WINDOW_SIZE];
    uint32_t last_raw_mm;
    uint32_t filtered_mm;
    uint32_t success_count;
    uint32_t failure_count;
    uint16_t recent_success_count;
    uint16_t recent_failure_count;
    uint8_t raw_count;
    uint8_t raw_head;
    bool filtered_valid;
};

void uwb_range_tracker_init(struct uwb_range_tracker *tracker,
                            uint16_t peer_short_addr);
uint32_t uwb_range_tracker_record_success(struct uwb_range_tracker *tracker,
                                          uint32_t raw_mm);
void uwb_range_tracker_record_failure(struct uwb_range_tracker *tracker);
uint32_t uwb_range_tracker_total_count(const struct uwb_range_tracker *tracker);
uint8_t uwb_range_tracker_quality_percent(
    const struct uwb_range_tracker *tracker);

#endif /* UWB_RANGE_TRACKER_H */
