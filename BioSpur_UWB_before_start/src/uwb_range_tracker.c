#include "uwb_range_tracker.h"

#include <string.h>

static uint32_t uwb_range_tracker_filtered_input(
    const struct uwb_range_tracker *tracker)
{
    uint32_t values[UWB_RANGE_TRACKER_WINDOW_SIZE];

    if (tracker->raw_count == 0U) {
        return 0U;
    }

    if (tracker->raw_count == 1U) {
        return tracker->raw_window[0];
    }

    if (tracker->raw_count == 2U) {
        return (tracker->raw_window[0] + tracker->raw_window[1]) / 2U;
    }

    memcpy(values, tracker->raw_window, sizeof(values));

    if (values[0] > values[1]) {
        uint32_t tmp = values[0];
        values[0] = values[1];
        values[1] = tmp;
    }

    if (values[1] > values[2]) {
        uint32_t tmp = values[1];
        values[1] = values[2];
        values[2] = tmp;
    }

    if (values[0] > values[1]) {
        uint32_t tmp = values[0];
        values[0] = values[1];
        values[1] = tmp;
    }

    return values[1];
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
    uint32_t filter_input;

    tracker->last_raw_mm = raw_mm;
    tracker->raw_window[tracker->raw_head] = raw_mm;
    tracker->raw_head =
        (uint8_t)((tracker->raw_head + 1U) % UWB_RANGE_TRACKER_WINDOW_SIZE);
    if (tracker->raw_count < UWB_RANGE_TRACKER_WINDOW_SIZE) {
        tracker->raw_count++;
    }

    filter_input = uwb_range_tracker_filtered_input(tracker);

    if (!tracker->filtered_valid) {
        tracker->filtered_mm = filter_input;
        tracker->filtered_valid = true;
    } else {
        tracker->filtered_mm =
            ((tracker->filtered_mm * 3U) + filter_input + 2U) / 4U;
    }

    tracker->success_count++;
    return tracker->filtered_mm;
}

void uwb_range_tracker_record_failure(struct uwb_range_tracker *tracker)
{
    tracker->failure_count++;
}

uint32_t uwb_range_tracker_total_count(const struct uwb_range_tracker *tracker)
{
    return tracker->success_count + tracker->failure_count;
}

uint8_t uwb_range_tracker_quality_percent(
    const struct uwb_range_tracker *tracker)
{
    uint32_t total = uwb_range_tracker_total_count(tracker);

    if (total == 0U) {
        return 0U;
    }

    return (uint8_t)((tracker->success_count * 100U) / total);
}
