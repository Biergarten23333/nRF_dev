#include <assert.h>
#include "bounded_notify_policy.h"

int main(void)
{
	struct bsf_bounded_notify_policy p = {0};

	/* Healthy acceptance submits exactly once. */
	assert(bsf_notify_submit(&p));
	assert(p.submitted == 1u && p.timeout_drops == 0u);

	/* Exhaustion/expiry drops and never submits a retry. */
	assert(!bsf_notify_submit(&p));
	assert(p.submitted == 1u && p.timeout_drops == 1u && p.fast_drop);
	assert(!bsf_notify_submit(&p));
	assert(p.submitted == 1u && p.timeout_drops == 2u);

	/* A late completion re-opens the single slot without replay. */
	bsf_notify_complete(&p);
	assert(!p.worker_busy && !p.fast_drop);
	assert(bsf_notify_submit(&p));
	assert(p.submitted == 2u && p.timeout_drops == 2u);
	return 0;
}
