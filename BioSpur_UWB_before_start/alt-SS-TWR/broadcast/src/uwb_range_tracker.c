#include "uwb_range_tracker.h"

#include <string.h>

static void uwb_range_tracker_decay_recent_counts(struct uwb_range_tracker *tracker)
{
    uint32_t recent_total =
        (uint32_t)tracker->recent_success_count + (uint32_t)tracker->recent_failure_count;

    if (recent_total < UWB_RANGE_TRACKER_QUALITY_WINDOW) {
        return;
    }

    tracker->recent_success_count = (uint16_t)((tracker->recent_success_count + 1U) / 2U);
    tracker->recent_failure_count = (uint16_t)(tracker->recent_failure_count / 2U);
}

void uwb_range_tracker_init(struct uwb_range_tracker *tracker,
                            uint16_t peer_short_addr)
{
    memset(tracker, 0, sizeof(*tracker));
    tracker->peer_short_addr = peer_short_addr;
}

uint32_t uwb_range_tracker_record_success(struct uwb_range_tracker *tracker,
                                          uint32_t raw_mm)
{
    tracker->last_raw_mm = raw_mm;
    tracker->raw_window[tracker->raw_head] = raw_mm;
    tracker->raw_head =
        (uint8_t)((tracker->raw_head + 1U) % UWB_RANGE_TRACKER_WINDOW_SIZE);
    if (tracker->raw_count < UWB_RANGE_TRACKER_WINDOW_SIZE) {
        tracker->raw_count++;
    }

    /* Raw mode: keep the latest measurement directly. */
    tracker->filtered_mm = raw_mm;
    tracker->filtered_valid = true;

    uwb_range_tracker_decay_recent_counts(tracker);
    tracker->success_count++;
    tracker->recent_success_count++;
    return tracker->filtered_mm;
}

void uwb_range_tracker_record_failure(struct uwb_range_tracker *tracker)
{
    uwb_range_tracker_decay_recent_counts(tracker);
    tracker->failure_count++;
    tracker->recent_failure_count++;
}

uint32_t uwb_range_tracker_total_count(const struct uwb_range_tracker *tracker)
{
    return tracker->success_count + tracker->failure_count;
}

uint8_t uwb_range_tracker_quality_percent(
    const struct uwb_range_tracker *tracker)
{
    uint32_t total =
        (uint32_t)tracker->recent_success_count + (uint32_t)tracker->recent_failure_count;

    if (total == 0U) {
        return 0U;
    }

    return (uint8_t)(((uint32_t)tracker->recent_success_count * 100U) / total);
}
