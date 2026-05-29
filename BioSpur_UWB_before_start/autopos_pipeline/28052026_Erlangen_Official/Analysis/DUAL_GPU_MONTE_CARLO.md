# Dual GPU Monte Carlo Keep-K Workflow

This folder is prepared for the Erlangen official CUDA keep-k Monte Carlo run.

## Current Split Strategy

The unit of work is one block:

```text
layout version x tag solver method x capture kind
```

For the full Erlangen job this is:

```text
5 layouts x 4 tag methods x 2 kinds = 40 blocks
```

The dual GPU launcher splits those 40 blocks by block index:

```text
GPU0: shard 0/2
GPU1: shard 1/2
```

Each worker writes only its own layout/tag/kind folders, so the workers do not
write the same CSVs.

## Resume After The 22/40 Pause

The current partial run was intentionally stopped at:

```text
completed: 22/40
last complete block: v3-lite / T3 / roto
next block: v3-lite / T4 / static
```

After installing the second GTX 1080 Ti, resume with:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 autopos_pipeline/28052026_Erlangen_Official/Analysis/scripts/launch_cuda_keepk_dual_gpu.py \
  --gpus 0,1 \
  --out autopos_pipeline/28052026_Erlangen_Official/Analysis/Monte-Carlo-Simulation \
  --repeats 5000 \
  --repeat-batch 1000 \
  --layout-versions all \
  --tag-methods all
```

The launcher defaults to:

```text
--summary-only
--skip-existing
```

So the first 22 complete blocks are skipped automatically, and the partial
`v3-lite/T4/static` block is recomputed cleanly because it has no complete
summary CSV.

## Output Files

Main manifest:

```text
Analysis/Monte-Carlo-Simulation/dual_gpu_run_manifest.json
```

Worker logs:

```text
Analysis/Monte-Carlo-Simulation/run_gpu0_shard0_of_2.log
Analysis/Monte-Carlo-Simulation/run_gpu1_shard1_of_2.log
```

Worker pid files:

```text
Analysis/Monte-Carlo-Simulation/run_gpu0_shard0_of_2.pid
Analysis/Monte-Carlo-Simulation/run_gpu1_shard1_of_2.pid
```

## Dry Run

Before launching a real run:

```bash
python3 autopos_pipeline/28052026_Erlangen_Official/Analysis/scripts/launch_cuda_keepk_dual_gpu.py \
  --dry-run \
  --gpus 0,1 \
  --out autopos_pipeline/28052026_Erlangen_Official/Analysis/Monte-Carlo-Simulation
```

