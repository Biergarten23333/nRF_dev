#ifndef BIOSPUR_UART_LINK_H
#define BIOSPUR_UART_LINK_H

#include <stdbool.h>
#include <stdint.h>

#include "biospur_link.h"

struct biospur_uart_link_stats {
	uint32_t frames_generated;
	uint32_t tx_started;
	uint32_t tx_completed;
	uint32_t tx_dropped;
	uint32_t tx_failed;
	uint32_t tx_aborted;
	uint32_t strobe_count;
	int32_t last_tx_error;
};

int biospur_uart_link_init(void);
int biospur_uart_link_submit(const bsl_uwb_t *body);
bool biospur_uart_link_strobe_pulse(void);
int biospur_uart_link_suspend(void);
void biospur_uart_link_resume(void);
bool biospur_uart_link_is_active(void);
void biospur_uart_link_get_stats(struct biospur_uart_link_stats *stats);

#endif /* BIOSPUR_UART_LINK_H */
