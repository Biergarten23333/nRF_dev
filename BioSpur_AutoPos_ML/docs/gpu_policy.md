# GPU Policy

Current layout evaluation scripts are CPU-only and must not use GPU resources.

If a future step needs GPU:

1. Use GPU0 only when explicitly authorized for the current work.
2. Do not use GPU1, because it is reserved for `dinardPCB` / another training job.
3. Check available memory before launching.
4. Set `CUDA_VISIBLE_DEVICES=0` for the process.
5. Prefer a dry run or small smoke test before a long job.

The current CPU pipeline explicitly clears `CUDA_VISIBLE_DEVICES`.

Current overnight status: GPU0 is allowed only if a justified training job is
reached, but the generated ML candidate table has only 5 real OptiTrack layout
labels and `train_allowed=false` for every row. GPU training is therefore not
started yet.
