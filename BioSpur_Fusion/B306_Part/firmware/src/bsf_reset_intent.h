/*
 * bsf_reset_intent.h -- name every software reset, or record that we could not.
 *
 * WHY THIS EXISTS
 * ---------------
 * On 2026-08-09 BSF6C53 took a software reset (`rr=4`) that nothing in the
 * system could account for: the recovery guard had not fired (`rcv=0`), the
 * watchdog had not fired (`dog=0`), the v45 detector had not captured
 * (`present=0`), and `CONFIG_RESET_ON_FATAL_ERROR` is not set. A reset whose
 * originating path cannot be named is not a curiosity, it is a hole in the
 * boot-loop protection: a path that nobody knows about is a path the streak
 * counter does not cover, and during acceptance it is indistinguishable from
 * successful recovery or from a false trigger.
 *
 * THE LIFETIME RULE, WHICH THIS FAMILY HAS ALREADY BROKEN THREE TIMES
 * -------------------------------------------------------------------
 * `bsf_v45_frozen` was in `.bss`. `v45_flash_slot_next` was in `.bss`. The v43
 * corpse wrote its reboot fields after the CRC. Every one of them was a witness
 * whose lifetime was shorter than the event it was meant to witness, and every
 * one had to be found on hardware.
 *
 * So: the intent is written to `.noinit` and SEALED **before** the reset is
 * issued, never after. `bsf_reset_now()` exists precisely so that the ordering
 * cannot be got wrong at a call site -- it writes, seals, then resets.
 *
 * UNKNOWN_SREQ
 * ------------
 * At the earliest boot stage the raw RESETREAS is read (not cleared -- main.c
 * still owns clearing it). If it names SREQ and no intent was recorded, the
 * boot is counted as UNKNOWN_SREQ and reported in `V45 STATUS`. That number is
 * the fleet's answer to "did anything reset a board for a reason we cannot
 * name", and it must be zero before ten boards run overnight.
 */
#ifndef BSF_RESET_INTENT_H
#define BSF_RESET_INTENT_H

#include <stdbool.h>
#include <stdint.h>

/*
 * Append only; never renumber. These are written into `.noinit` and read back
 * across a reset, so a renumber silently reinterprets history.
 *
 * Enumerated from the complete call-site census, 2026-08-09:
 *   1 bsf_recovery.c   recovery guard cold reset          (v46)
 *   2 bsf_v45.c        v45 detector post-capture reboot
 *   3 main.c           stall-ring forward, from an ISR
 *   4 main.c           BT RX monitor wedge reboot
 *   5 main.c           v41 stall recovery
 *   6 main.c           REBOOT control command
 *   7 main.c           MCUboot confirmation timeout rollback
 *   8 SDK              mcumgr os-group reset (DFU). NOT application-owned:
 *                      CONFIG_MCUMGR_GRP_OS_RESET_HOOK is not enabled, so the
 *                      application cannot currently stamp an intent for it.
 *                      Recorded here so the gap is named rather than implied.
 */
#define BSF_RESET_INTENT_NONE            0u
#define BSF_RESET_INTENT_RECOVERY_GUARD  1u
#define BSF_RESET_INTENT_V45_DETECTOR    2u
#define BSF_RESET_INTENT_RING_FWD        3u
#define BSF_RESET_INTENT_BT_MONITOR      4u
#define BSF_RESET_INTENT_STALL_RECOVERY  5u
#define BSF_RESET_INTENT_CMD_REBOOT      6u
#define BSF_RESET_INTENT_BOOT_CONFIRM    7u
#define BSF_RESET_INTENT_DFU             8u
#define BSF_RESET_INTENT__MAX            9u

/*
 * Record the intent and reset. Writes and seals the witness FIRST.
 * Does not return.
 */
void bsf_reset_now(uint8_t intent);

/* Record an intent without resetting -- for paths where the reset is issued by
 * the SDK (DFU) and we only get to stamp our side. */
void bsf_reset_intent_mark(uint8_t intent);

/* For V45 STATUS. `last_intent` is the intent that produced THIS boot. */
void bsf_reset_intent_report(uint8_t *last_intent, uint32_t *unknown_sreq,
			     uint32_t *raw_resetreas, uint32_t *named);

#endif /* BSF_RESET_INTENT_H */
