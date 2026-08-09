/* See bsf_reset_intent.h for why the witness is sealed before the reset. */
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/init.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/crc.h>
#include <zephyr/sys/reboot.h>
#include <helpers/nrfx_reset_reason.h>

#include "bsf_reset_intent.h"

LOG_MODULE_REGISTER(bsf_reset_intent, LOG_LEVEL_INF);

#define INTENT_MAGIC 0x52494e54u   /* "RINT" */

struct bsf_reset_intent_state {
	uint32_t magic;
	uint8_t  intent;          /* armed for the NEXT reset               */
	uint8_t  last_intent;     /* what produced THIS boot                */
	uint8_t  pad[2];
	uint32_t unknown_sreq;    /* cumulative, survives soft resets       */
	uint32_t named;           /* cumulative attributed software resets  */
	uint32_t raw_resetreas;   /* raw register value for THIS boot       */
	uint32_t crc32;
};

__attribute__((section(".noinit")))
static struct bsf_reset_intent_state bsf_ri;

static uint32_t ri_crc(void)
{
	return crc32_ieee((const uint8_t *)&bsf_ri,
			  offsetof(struct bsf_reset_intent_state, crc32));
}

static void ri_seal(void)
{
	bsf_ri.magic = INTENT_MAGIC;
	bsf_ri.crc32 = ri_crc();
}

static bool ri_valid(void)
{
	return bsf_ri.magic == INTENT_MAGIC && bsf_ri.crc32 == ri_crc();
}

/*
 * PRE_KERNEL_1, and it READS RESETREAS without clearing it. main.c still owns
 * the clear; nrfx_reset_reason_get() is a plain register read, so doing it
 * twice is safe and this one happens first.
 */
static int bsf_reset_intent_early(void)
{
	uint32_t reas;

	if (!ri_valid()) {
		/* Power-on, or a corrupted witness. Either way nothing here can
		 * be trusted, and a power-on is not an unattributed SREQ. */
		memset(&bsf_ri, 0, sizeof(bsf_ri));
	}

	reas = nrfx_reset_reason_get();
	bsf_ri.raw_resetreas = reas;
	bsf_ri.last_intent = bsf_ri.intent;

	if (reas & NRFX_RESET_REASON_SREQ_MASK) {
		if (bsf_ri.intent == BSF_RESET_INTENT_NONE) {
			bsf_ri.unknown_sreq++;
		} else {
			bsf_ri.named++;
		}
	}

	/* Consumed exactly once: the next reset must stamp its own intent. */
	bsf_ri.intent = BSF_RESET_INTENT_NONE;
	ri_seal();
	return 0;
}
SYS_INIT(bsf_reset_intent_early, PRE_KERNEL_1, 0);

void bsf_reset_intent_mark(uint8_t intent)
{
	bsf_ri.intent = intent;
	ri_seal();
}

void bsf_reset_now(uint8_t intent)
{
	/* Sealed BEFORE the reset. This ordering is the whole point of the
	 * function existing rather than being open-coded at seven call sites. */
	bsf_reset_intent_mark(intent);
	sys_reboot(SYS_REBOOT_COLD);
	CODE_UNREACHABLE;
}

void bsf_reset_intent_report(uint8_t *last_intent, uint32_t *unknown_sreq,
			     uint32_t *raw_resetreas, uint32_t *named)
{
	*last_intent = bsf_ri.last_intent;
	*unknown_sreq = bsf_ri.unknown_sreq;
	*raw_resetreas = bsf_ri.raw_resetreas;
	*named = bsf_ri.named;
}
