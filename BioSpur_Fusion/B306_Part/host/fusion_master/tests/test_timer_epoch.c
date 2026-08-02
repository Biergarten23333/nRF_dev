#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include "../src/timer_epoch.h"

int main(void)
{
	const uint64_t wrap = UINT64_C(1) << 32;

	assert(bsf_extend_low32_near(123u, 100u) == 123u);
	assert(bsf_extend_low32_near(UINT32_MAX - 5u,
				     UINT32_MAX - 10u) == UINT32_MAX - 5u);
	assert(bsf_extend_low32_near(4u, UINT32_MAX - 5u) == wrap + 4u);
	assert(bsf_extend_low32_near(UINT32_MAX - 4u, wrap + 5u) ==
	       UINT32_MAX - 4u);
	assert(bsf_extend_low32_near(0x100u, 3u * wrap + 0x80u) ==
	       3u * wrap + 0x100u);

	puts("timer epoch tests passed");
	return 0;
}
