/*
 * bsf_recovery.h -- v46 wedge recovery guard.
 *
 * WHAT THIS IS FOR
 * ----------------
 * Until v46 the fleet had NO recovery capability, and that is measured rather
 * than assumed: across 169 836 N8 telemetry samples every `reset_reason` is 0
 * (power-on/brownout) or 1 (reset pin) -- never 2 (watchdog), never 4
 * (software). BSFEC35 sat wedged for 5 h 27 min and was rescued by a battery
 * dying, not by any code.
 *
 * WHY A RESET AND NOT A DISCONNECT
 * --------------------------------
 * Measured on BSF6C53, 2026-08-09, phase A invariant 4: a wedged node that is
 * disconnected by the controller does NOT re-advertise. Zero advertising
 * reports across 130 s of scanning, and no reconnection after the master
 * returned to RECV. `bt_conn_disconnect()` converts a wedged-but-reachable
 * node into a wedged-and-invisible one. It is not a recovery action.
 *
 * Wedge #2 says the same thing from the other side: the host had released its
 * `conn` object entirely (state DISCONNECTED, ref=0) while the controller still
 * held the link, so `disconnected()` never ran and `start_advertising()` was
 * never reached. Only a full reset puts host and controller back in agreement.
 *
 * WHERE IT RUNS
 * -------------
 * Its own thread. Not sysworkq, not MPSL Work, not BT RX WQ, not the notify
 * worker, not the publisher -- every one of those is a context a wedge has been
 * observed to take down or park. 1 Hz, atomics and snapshots only, no
 * allocation and no blocking Bluetooth calls.
 *
 * The N8 data says the system workqueue actually survives fleet wedges (a 30 s
 * watchdog fed 1:1 with uptime, never firing across 5.5 h), so this placement
 * is insurance rather than necessity. It costs a stack and a 1 Hz tick, and it
 * covers the one class the fleet data cannot speak for.
 */
#ifndef BSF_RECOVERY_H
#define BSF_RECOVERY_H

#include <stdbool.h>
#include <stdint.h>

/*
 * THE TRIGGER, AND WHY THERE IS NO PRODUCER TERM.
 *
 * v45's arm C required "producer advancing while delivery frozen". That term
 * cannot be evaluated during the events it is meant to catch: in all four fleet
 * wedges the producer counters (`frames`, `imu_records`) are themselves carried
 * by telemetry, so the moment the criterion matters is the moment both inputs
 * stop being observable. Requiring it would have missed four of the five known
 * events.
 *
 * Telemetry is a 1 Hz heartbeat on the same notify path, so a healthy connected
 * board advances notify_ok at >= 1/s with no producer running at all. Frozen
 * delivery on a connected, subscribed link is therefore sufficient on its own.
 */
#define BSF_RECOVERY_FREEZE_MS      12000u
#define BSF_RECOVERY_NOTCONN_STREAK 320u
#define BSF_RECOVERY_GRACE_MS       10000u   /* after connect, before arming  */
#define BSF_RECOVERY_TICK_MS        1000u
#define BSF_RECOVERY_JITTER_MS      500u     /* bounded, node-derived         */
#define BSF_RECOVERY_MAX_STREAK     3u       /* consecutive guard resets      */
#define BSF_RECOVERY_HEALTHY_MS     1800000u /* 30 min clears the streak      */

/* cause codes -- appended only, never renumbered */
#define BSF_RECOVERY_CAUSE_NONE          0u
/*
 * v46r2. What each cause MEANS when it fires in the field:
 *
 *  NOTIFY_FROZEN  attempts advancing, completions frozen >= 12 s on a
 *                 connected+subscribed link. The notify path is broken. This
 *                 is the fleet-wedge signature.
 *  NOTCONN        the node contradicting itself -- the application believes it
 *                 is connected while >= 320 consecutive sends return -ENOTCONN.
 *                 Wedge #2's signature (it reached 19412). No dwell: this is
 *                 an inconsistency, not a slow symptom.
 *
 * IDLE is NOT a cause and never triggers: attempts not advancing means there
 * was nothing to send, which is not a fault. It is enumerated only so the
 * distinction is explicit in the code rather than implied by an absence.
 */
#define BSF_RECOVERY_CAUSE_NOTIFY_FROZEN 1u
#define BSF_RECOVERY_CAUSE_NOTCONN       2u
#define BSF_RECOVERY_CAUSE_IDLE_NOT_A_FAULT 3u

void bsf_recovery_start(void);

/* For V45 STATUS: how many times we recovered, why, and whether we gave up. */
void bsf_recovery_report(uint32_t *resets, uint8_t *cause, uint32_t *frozen_ms,
			 uint8_t *streak, uint8_t *latched);

#endif /* BSF_RECOVERY_H */
