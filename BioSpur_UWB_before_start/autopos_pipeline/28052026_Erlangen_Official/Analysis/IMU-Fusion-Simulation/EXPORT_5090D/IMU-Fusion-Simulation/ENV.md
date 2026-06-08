# Environment Setup For RTX 5090D Phase 4

Generated: 2026-06-05

Target machine:

```text
CPU: AMD Ryzen 9 9950X
RAM: 96 GB
GPU: NVIDIA GeForce RTX 5090D
OS: fresh Ubuntu, assumed 24.04 LTS unless the owner chose newer
```

This environment is for running the UWB+IMU Phase 4 simulation, not for flashing
nRF hardware.

## Storage

Recommended:

```text
NVMe SSD: 2 TB minimum, 4 TB preferred
```

Reason:

```text
source data + caches + multiseed run tables + PNG contact sheets can grow fast.
Run the project on NVMe, not a slow HDD.
```

Suggested layout:

```text
~/BioSpur_UWB_before_start/
  autopos_pipeline/28052026_Erlangen_Official/
```

## System Packages

On the 5090D machine, use its normal admin package workflow. This list is the
minimal practical Ubuntu package set:

```bash
sudo apt update
sudo apt install -y \
  build-essential cmake ninja-build pkg-config \
  git git-lfs curl wget rsync tmux zip unzip \
  python3 python3-venv python3-dev python3-pip \
  ffmpeg libgl1 libglib2.0-0 \
  htop nvtop
```

`nvtop` is optional but useful. If the package is not available on that Ubuntu
release, use `watch -n 1 nvidia-smi` instead.

## NVIDIA Driver And CUDA

Blackwell / RTX 50-series needs a new NVIDIA driver and CUDA-capable software
stack. Do not install old 535/550-era drivers and expect a 5090D to work.

Rules:

```text
1. Install the latest production NVIDIA Linux driver available for the 5090D.
2. Verify `nvidia-smi` before installing PyTorch.
3. CUDA Toolkit is optional for pure PyTorch wheels, but useful for diagnostics
   and future custom kernels.
4. If PyTorch says "invalid device function" or cannot see the GPU, the wheel
   is too old for the 5090D architecture. Install a newer PyTorch CUDA wheel.
```

Official reference points checked while writing this file:

```text
NVIDIA CUDA Linux install guide:
  https://docs.nvidia.com/cuda/cuda-installation-guide-linux/

NVIDIA CUDA architecture matrix:
  https://docs.nvidia.com/datacenter/tesla/drivers/cuda-toolkit-driver-and-architecture-matrix.html

PyTorch wheel commands:
  https://pytorch.org/get-started/previous-versions/
```

The NVIDIA matrix lists Blackwell first CUDA Toolkit support as CUDA 12.8. The
PyTorch wheel page currently lists CUDA 12.8, 12.9, and 13.0 wheel examples for
recent versions. Use the newest stable PyTorch CUDA wheel that recognizes the
5090D. If stable fails, use the official nightly CUDA wheel for the newest CUDA
runtime supported by the installed driver.

Driver verification:

```bash
nvidia-smi
```

Expected:

```text
GPU name includes RTX 5090D
Driver Version is recent enough for Blackwell
CUDA Version line is present
No nouveau driver is active
```

## Python Virtual Environment

Use a project-specific virtual environment:

```bash
python3 -m venv ~/.venvs/imu-fusion
source ~/.venvs/imu-fusion/bin/activate
python -m pip install --upgrade pip setuptools wheel
```

Install base scientific packages:

```bash
pip install \
  numpy pandas scipy matplotlib pyyaml tqdm rich psutil \
  pyarrow tables
```

Install PyTorch with CUDA using the official selector or current official wheel
page. Example only; adjust if the official selector has moved to a newer CUDA
wheel:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
```

Fallback examples if the current stable selector recommends another runtime:

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu129
```

Use only one of these PyTorch commands in a clean venv. The correct one is the
newest official CUDA wheel that passes the verification below on the 5090D.

## Python/CUDA Verification

Run:

```bash
source ~/.venvs/imu-fusion/bin/activate
python - <<'PY'
import torch
print("torch", torch.__version__)
print("torch cuda", torch.version.cuda)
print("cuda available", torch.cuda.is_available())
print("device count", torch.cuda.device_count())
if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        p = torch.cuda.get_device_properties(i)
        print(i, torch.cuda.get_device_name(i), p.major, p.minor, round(p.total_memory / 1024**3, 2), "GiB")
    x = torch.randn((4096, 4096), device="cuda")
    y = torch.randn((4096, 4096), device="cuda")
    torch.cuda.synchronize()
    z = x @ y
    torch.cuda.synchronize()
    print("matmul ok", float(z[0, 0]))
PY
```

Pass criteria:

```text
cuda available = True
device count >= 1
device name includes RTX 5090D
matmul ok prints a finite number
```

If this fails:

```text
torch.cuda.is_available() False:
  driver or PyTorch CUDA wheel mismatch.

invalid device function:
  PyTorch wheel is too old for Blackwell. Install newer stable/nightly wheel.

out of memory in tiny matmul:
  another process is using the GPU or the driver stack is broken.
```

## Project Transfer

Preferred transfer:

```bash
rsync -aH --info=progress2 \
  /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/ \
  <new-host>:~/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/
```

On the new machine:

```bash
cd ~/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/IMU-Fusion-Simulation
source ~/.venvs/imu-fusion/bin/activate
```

Verify files:

```bash
python -m py_compile scripts/*.py
python - <<'PY'
import pathlib, yaml
root = pathlib.Path.cwd()
required = [
    "NAMING_RULES.md",
    "CODEX_HANDOFF.md",
    "ENV.md",
    "configs/fusion.yaml",
    "configs/sensors.yaml",
    "runs/phase2_screening/20260604T163422Z/reports/VALIDATION_GATES.md",
    "runs/phase3_full_confirmation/20260604T180859Z/reports/PHASE3_INTERPRETED_RESULTS.md",
    "runs/phase4_algorithm_factory/nightly_1080ti_20260604T231033Z/reports/PHASE4_NIGHTLY_1080TI_BOOTSTRAP.md",
]
missing = [p for p in required if not (root / p).exists()]
print("missing", missing)
yaml.safe_load((root / "configs/fusion.yaml").read_text())
yaml.safe_load((root / "configs/sensors.yaml").read_text())
PY
```

## First Smoke Run On 5090D

Use the tiny pilot first:

```bash
python scripts/run_phase4_gpu_pilot.py \
  --phase2-run 20260604T163422Z \
  --run-id smoke_5090d_cuda0 \
  --device cuda:0 \
  --dtype float32 \
  --torch-threads 1 \
  --max-tracks 1 \
  --max-frames 20 \
  --gpu-repeat 3 \
  --rows R2:L0:I0:T6 R4:L8:I1+I2+I3+I8:T8
```

Then run a 30 minute bootstrap:

```bash
python scripts/run_phase4_nightly_bootstrap.py \
  --run-id bootstrap_5090d_$(date -u +%Y%m%dT%H%M%SZ) \
  --phase2-run 20260604T163422Z \
  --devices cuda:0 \
  --workers-per-device 4 \
  --chunk-size 8 \
  --partial-max-tracks 0 \
  --partial-max-frames 0 \
  --max-wall-time 1800 \
  --chunk-timeout-s 7200 \
  --monitor-interval 10
```

Monitor:

```bash
watch -n 1 nvidia-smi
htop
nvtop
```

Tune:

```text
If GPU util is intermittent and CPU has headroom:
  try --workers-per-device 6, then 8.

If CPU is saturated but GPU is still intermittent:
  the current path is CPU-golden limited; do not keep increasing workers forever.

If RAM grows too high:
  lower workers or chunk size.

If VRAM is low but GPU util is high:
  that is fine. VRAM usage is not the target; GPU throughput is.
```

## Production Phase 4 Rule

The current bootstrap script is not the final FULL Phase 4 production runner.
For the real 5090D run, Codex should implement a new production launcher such
as:

```text
scripts/run_phase4_full_factory.py
```

It must:

```text
build the FULL compatible manifest before execution
run CPU golden only for sampled agreement gates
run production scoring on batched torch tensors
support --resume-run
support --max-wall-time and --stop-at-local-time
write atomic chunk outputs
write final ranking only after all required chunks are done
write selected and full PNG/contact-sheet outputs
```

Do not call a partial bootstrap the final Phase 4 result.

## Resource Settings For 9950X/5090D

Start conservative:

```text
workers-per-device = 4
chunk-size = 8
torch threads per GPU feeder = 1
monitor interval = 10 s
```

Likely 5090D tuning range:

```text
workers-per-device = 4-8 for current CPU-golden-heavy bootstrap
workers-per-device = 1-4 for future large-batch tensorized production backend
```

The 9950X can feed more workers than the 8700K, but the final design should not
burn CPU doing repeated reference work. Use CPU for data prep, manifests, PNGs,
and sampled golden checks; use GPU for broad scoring/filter updates.

## Troubleshooting Quick Table

```text
Symptom:
  GPU visible in nvidia-smi, but torch cuda unavailable
Cause:
  PyTorch CPU wheel or incompatible CUDA wheel.
Fix:
  reinstall official PyTorch CUDA wheel in clean venv.

Symptom:
  invalid device function
Cause:
  wheel lacks 5090D/Blackwell support.
Fix:
  newer stable or nightly PyTorch CUDA wheel.

Symptom:
  GPU util bursty, CPU high
Cause:
  CPU data prep/reference bottleneck.
Fix:
  increase feeder workers only if CPU has headroom; otherwise implement tensorized production backend.

Symptom:
  GPU memory only 1-3 GB used, GPU util high
Cause:
  workload is compute/launch limited, not memory limited.
Fix:
  do not chase VRAM. Increase batch/chunk only if it improves measured rows/s.

Symptom:
  one CPU thread saturated and other cores idle
Cause:
  bad scheduler or single-process loop.
Fix:
  use process pool/vectorized batches. This violates Phase 4 gate G12.

Symptom:
  run stopped overnight
Cause:
  wall-time cutoff, timeout, or terminal closed.
Fix:
  use tmux and resume with --resume-run <run_id>.
```

## Recommended tmux Use

```bash
tmux new -s phase4
cd ~/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/IMU-Fusion-Simulation
source ~/.venvs/imu-fusion/bin/activate
python scripts/run_phase4_nightly_bootstrap.py --help
```

Detach:

```text
Ctrl-b then d
```

Reattach:

```bash
tmux attach -t phase4
```

## Final Checklist Before Spending 5090D Time

```text
[ ] nvidia-smi sees RTX 5090D
[ ] torch.cuda.is_available() is True
[ ] torch matmul smoke passes
[ ] scripts/*.py compile
[ ] configs/fusion.yaml and configs/sensors.yaml parse
[ ] Phase 2/3/4 existing report files are present
[ ] tiny run_phase4_gpu_pilot passes
[ ] 30 minute bootstrap produces tables and PASS/REVIEW gates
[ ] next Codex understands CODEX_HANDOFF.md
```
