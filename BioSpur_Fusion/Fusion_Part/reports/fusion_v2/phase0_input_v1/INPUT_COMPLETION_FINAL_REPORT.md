# Phase 0 IMU input-context completion

Prepublication qualification passed at implementation SHA `6b35b1395422d997645ada0f0e5033ad26512c94`.

The selective reader streamed NPY members without NumPy and decoded only approved scalar identity/time fields. D1 and D2 row-aligned contexts contain 800,196 and 74,142 rows respectively, cover all ten hardware IDs, and reproduce the frozen view common time with a maximum difference of 0 ns. Two independent detached runs produced byte-identical contexts and uncertainty models.

Measurement numeric decodes, measurement arrays, retained measurement fields, logged measurement values, and D3 measurement access were all zero. The sample-age model remains `UNKNOWN_BOUNDED` on `[0, 5000] us`; it is not a fixed-delay or probability claim.

Publication is pending. Transaction B was not started at the time of this tracked attestation report.
