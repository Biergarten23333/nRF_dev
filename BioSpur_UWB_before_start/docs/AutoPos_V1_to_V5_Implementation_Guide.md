# AutoPos: Complete Implementation Guide V1–V5
## UWB Anchor Auto-Positioning System
### For Human Developers and Codex

> Platform: DWM1001C (DW1000 + nRF52832), 8 Anchors (A–H) + Ref115 + RotArm  
> Language: Python (solver), C (firmware)  
> Goal: Automated anchor layout calibration with sub-centimeter accuracy

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Hardware Context](#hardware-context)
3. [V1: Naive Least Squares](#v1)
4. [V2: Bidirectional Fusion + Ref115 Loop](#v2)
5. [V3: SDP Init + Antenna Delay + Tukey IRLS](#v3)
6. [V4: RotArm Integration + Z-Axis Info Injection](#v4)
7. [V5: GNC + Heteroscedastic Noise + Clock Drift](#v5)
8. [Shared Utilities](#shared-utilities)
9. [File Structure](#file-structure)
10. [Key Parameters Reference](#key-parameters-reference)

---

## System Overview

AutoPos is an anchor self-calibration system. The goal is to automatically determine the 3D positions of 8 UWB anchors (A–H) without any manual measurement.

**The calibration pipeline produces:**
```
anchor_layout_vX_final.json
{
  "A": [x, y, z],
  "B": [x, y, z],
  ...
  "H": [x, y, z],
  "antenna_delays": {"A": tau_A, ...},   # V3+
  "clock_drifts":   {"A": xi_A, ...},    # V5+
  "quality": {
    "rms_dist": float,
    "rms_ref":  float,
    "condition_number": float
  }
}
```

**After calibration, this layout is used for runtime tag positioning (TS mode). AutoPos is run once per deployment, or when an anchor is moved.**

---

## Hardware Context

### Anchors
- 8x DWM1001C modules acting as anchors (A, B, C, D, E, F, G, H)
- Fixed positions, mounted on walls/ceiling
- Communicate via UWB SS-TWR (Single-Sided Two-Way Ranging)

### Ref115
- 1x DWM1001C in CM (Calibration Mode)
- Acts as a reference tag with known or estimated position
- Sweeps all anchors during calibration
- Does NOT run TS (Tag Streaming) during AutoPos — only CM

### RotArm (V4+)
- Rotating arm apparatus with 2 tags
- Arm lengths: r1 = 0.30m (short), r2 = 0.50m (long)
- Tags on opposite ends of the arm
- LIS2DH12 accelerometer (built into DWM1001C) measures tilt angle
- No encoder — rotation angle θ_k is solved from UWB data

### Key Constants
```python
C_LIGHT = 299_702_547.0   # m/s, speed of light in UWB medium
N_ANCHORS = 8
ANCHOR_IDS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
ARM_LENGTH_SHORT = 0.30   # meters, r1
ARM_LENGTH_LONG  = 0.50   # meters, r2
ARM_LENGTH_TOTAL = 0.80   # r1 + r2
```

---

## V1

### Concept
Naive one-way ranging → direct weighted least squares. No bidirectional fusion, no robustness, no reference tag.

### Math

Objective:

$$J = \sum_{(i,j) \in E} w_{ij} \left(\|\mathbf{x}_i - \mathbf{x}_j\| - d_{ij}\right)^2$$

Where:
- $\mathbf{x}_i \in \mathbb{R}^3$: position of anchor $i$
- $d_{ij}$: raw one-way ranging measurement
- $w_{ij} = 1$ (uniform weights in V1)
- $E$: set of all measured anchor pairs

### Algorithm

```
1. Collect one-way ranging for all anchor pairs
2. Fix anchor A at origin [0,0,0]
3. Fix anchor B on x-axis [d_AB, 0, 0]
4. Initialize remaining anchors with random perturbation
5. Run scipy.optimize.minimize (L-BFGS-B) on J
6. Output anchor positions
```

### Known Problems
- No bidirectional averaging → asymmetric noise
- L2 loss → single NLOS measurement can corrupt entire layout
- Depends heavily on initial values → local minima
- No absolute scale reference
- No Z-axis constraint → Z positions very inaccurate

### Implementation

```python
# v1_solver.py
import numpy as np
from scipy.optimize import minimize
from itertools import combinations

def objective_v1(x_flat, anchor_ids, distances, weights):
    """
    x_flat: flattened anchor positions [x_A, y_A, z_A, x_B, ...]
    distances: dict {(i,j): d_ij}
    weights: dict {(i,j): w_ij}
    """
    n = len(anchor_ids)
    X = x_flat.reshape(n, 3)
    idx = {a: i for i, a in enumerate(anchor_ids)}
    
    J = 0.0
    for (i, j), d_ij in distances.items():
        xi = X[idx[i]]
        xj = X[idx[j]]
        dist = np.linalg.norm(xi - xj)
        w = weights.get((i, j), 1.0)
        J += w * (dist - d_ij) ** 2
    return J

def solve_v1(distances, anchor_ids=ANCHOR_IDS):
    n = len(anchor_ids)
    # Initial layout: random in 5x5x3 box
    x0 = np.random.randn(n, 3) * 2.0
    # Fix A at origin
    x0[0] = [0, 0, 0]
    
    weights = {k: 1.0 for k in distances}
    
    result = minimize(
        objective_v1,
        x0.flatten(),
        args=(anchor_ids, distances, weights),
        method='L-BFGS-B',
        options={'maxiter': 1000, 'ftol': 1e-12}
    )
    
    X = result.x.reshape(n, 3)
    return {a: X[i].tolist() for i, a in enumerate(anchor_ids)}
```

---

## V2

### Concept
**Bidirectional sweep → fusion → iterative LS solver → Ref115 CM refine loop until convergence.**

This is the current production version.

### Pipeline

```
Step 1: Anchor Bidirectional Sweep
  → pairs_all.csv

Step 2: Bidirectional Fusion
  → final_pair_distances_v2.csv
  → inter_anchor_matrix_v2fused.json

Step 3: First/Raw Layout
  → anchor_layout_v2_raw.json

Step 4: Ref115 CM Sweep
  → ref115_cm_baseline.csv

Step 5: Refine Loop (until convergence)
  → anchor_layout_v2_iterative.json

Step 6: Final Layout
  → anchor_layout_v2_final.json
```

### Step 1: Bidirectional Sweep

Collect N measurements in each direction for every anchor pair.

**Dynamic N (V2 upgrade from fixed N=50):**

```python
# dynamic_sweep.py
def sweep_pair_dynamic(anchor_i, anchor_j,
                        eps_conv=0.5e-3,   # 0.5mm convergence threshold
                        window=10,          # must be stable for this many samples
                        n_max=100):
    """
    Sweep anchor pair (i,j) until ranging mean converges.
    Returns list of measurements.
    """
    measurements = []
    stable_count = 0
    prev_mean = None
    
    while stable_count < window and len(measurements) < n_max:
        d = ranging_measurement(anchor_i, anchor_j)  # hardware call
        measurements.append(d)
        
        if len(measurements) < 5:
            continue
            
        curr_mean = np.mean(measurements)
        
        if prev_mean is not None:
            delta = abs(curr_mean - prev_mean)
            if delta < eps_conv:
                stable_count += 1
            else:
                stable_count = 0  # must be CONSECUTIVELY stable
        
        prev_mean = curr_mean
    
    # Flag if didn't converge
    converged = stable_count >= window
    return measurements, converged
```

**Why dynamic N:**
- Your experiments showed error floor appears at N≈40–50
- Floor = systematic error (antenna delay, clock drift, fixed NLOS)
- More samples cannot reduce systematic error
- Dynamic N stops at the floor automatically
- NLOS-heavy pairs take longer → automatically get more samples

### Step 2: Bidirectional Fusion

**V2 fusion (simple weighted average):**

```python
def fuse_bidirectional_v2(d_ij_samples, d_ji_samples):
    """
    d_ij_samples: list of measurements, direction i→j
    d_ji_samples: list of measurements, direction j→i
    Returns: (fused_distance, fused_variance)
    """
    mean_ij = np.mean(d_ij_samples)
    mean_ji = np.mean(d_ji_samples)
    var_ij  = np.var(d_ij_samples, ddof=1)
    var_ji  = np.var(d_ji_samples, ddof=1)
    
    # Minimum variance fusion (optimal linear combination)
    # V2 uses simple mean; V3 upgrades to proper MVUE
    D_ij = (mean_ij + mean_ji) / 2.0
    sigma2_ij = (var_ij + var_ji) / 4.0
    
    # Bias detection
    bias = abs(mean_ij - mean_ji)
    sigma_combined = np.sqrt(var_ij + var_ji)
    high_bias = bias > 3 * sigma_combined
    
    return D_ij, sigma2_ij, high_bias
```

**Output format — inter_anchor_matrix_v2fused.json:**
```json
{
  "AB": {"distance": 3.142, "variance": 0.000025, "high_bias": false, "n_ij": 47, "n_ji": 51},
  "AC": {"distance": 5.021, "variance": 0.000031, "high_bias": false, "n_ij": 50, "n_ji": 50},
  ...
}
```

### Step 3: V2 Solver

**Objective function:**

$$J_{V2} = \underbrace{\sum_{(i,j)\in E} w_{ij}(\|\mathbf{x}_i-\mathbf{x}_j\| - D_{ij})^2}_{J_{matrix}} + \underbrace{\sum_i \lambda_i \|\mathbf{x}_i - \mathbf{x}_i^{(0)}\|^2}_{J_{prior}} + \underbrace{\sum_{i,k} \alpha_{ik}(\|\mathbf{x}_i - \mathbf{r}_k\| - R_{ik})^2}_{J_{ref}}$$

```python
# v2_solver.py
import numpy as np
from scipy.optimize import minimize

def objective_v2(x_flat, anchor_ids, fused_distances, 
                  initial_layout, ref_positions, ref_distances,
                  weights, lambdas, alphas):
    n = len(anchor_ids)
    X = x_flat.reshape(n, 3)
    idx = {a: i for i, a in enumerate(anchor_ids)}
    
    # J_matrix
    J_mat = 0.0
    for (i, j), D_ij in fused_distances.items():
        xi, xj = X[idx[i]], X[idx[j]]
        dist = np.linalg.norm(xi - xj)
        w = weights.get((i, j), 1.0)
        J_mat += w * (dist - D_ij) ** 2
    
    # J_prior
    J_prior = 0.0
    for a, i in idx.items():
        x0 = np.array(initial_layout[a])
        lam = lambdas.get(a, 0.1)
        J_prior += lam * np.sum((X[i] - x0) ** 2)
    
    # J_ref (Ref115 constraints)
    J_ref = 0.0
    for (anchor_id, k), R_ik in ref_distances.items():
        r_k = np.array(ref_positions[k])
        xi = X[idx[anchor_id]]
        dist = np.linalg.norm(xi - r_k)
        alpha = alphas.get((anchor_id, k), 1.0)
        J_ref += alpha * (dist - R_ik) ** 2
    
    return J_mat + J_prior + J_ref
```

### Step 5: Ref115 Refine Loop

**This is the core of V2 — loop until convergence, not just one pass:**

```python
def refine_loop_v2(initial_layout, fused_distances, ref115_data,
                    eps_pos=1.5e-3,    # 1.5mm position convergence
                    eps_J=1e-4,        # relative J convergence
                    max_iters=20,
                    lambda_0=1.0, gamma=0.65):
    """
    Alternating optimization:
      Step A: fix ref115 positions, optimize anchor positions
      Step B: fix anchor positions, localize ref115 positions
    """
    layout = initial_layout.copy()
    ref_positions = initialize_ref115_positions(layout, ref115_data)
    
    J_prev = np.inf
    
    for t in range(max_iters):
        lambda_t = lambda_0 * (gamma ** t)
        
        # Step A: optimize anchor positions
        layout_new = solve_anchors(
            layout, fused_distances, ref_positions, ref115_data,
            lambda_t=lambda_t
        )
        
        # Step B: re-localize ref115 using current layout
        ref_positions_new = localize_ref115(layout_new, ref115_data)
        
        # Compute J
        J_curr = compute_total_J(layout_new, fused_distances,
                                   ref_positions_new, ref115_data)
        
        # Check convergence (BOTH conditions required)
        max_disp = max(
            np.linalg.norm(np.array(layout_new[a]) - np.array(layout[a]))
            for a in ANCHOR_IDS
        )
        rel_J = abs(J_curr - J_prev) / (J_prev + 1e-12)
        
        print(f"Iter {t:2d}: J={J_curr:.4f}, "
              f"max_disp={max_disp*1000:.2f}mm, rel_J={rel_J:.2e}")
        
        layout = layout_new
        ref_positions = ref_positions_new
        J_prev = J_curr
        
        if max_disp < eps_pos and rel_J < eps_J:
            print(f"Converged at iteration {t}")
            break
    
    return layout, ref_positions
```

**Important: λ decays each iteration so prior weakens and data takes over.**

### V2 Known Problems
- Weights $w_{ij}$, $\lambda_i$, $\alpha_{ik}$ are hand-tuned
- L2 loss — one NLOS outlier can corrupt layout
- No SDP → depends on initial layout quality
- Antenna delay mixed into layout error (not separated)
- Clock drift not modeled
- Z accuracy poor — no mechanism to inject Z information

---

## V3

### What V3 Adds Over V2
1. **Minimum Variance fusion** (replaces simple average)
2. **SDP global initialization** (replaces hand-crafted initial layout)
3. **Antenna delay joint estimation** (new variable $\tau_i$)
4. **Tukey Bisquare IRLS** (replaces L2 loss)
5. **Dual convergence condition** (position + residual)
6. **Layout quality score output**

### Repo Implementation (2026-04-14)
This repo now contains a runnable V3_full implementation (distinct from the earlier V3-lite experiments):

- V3 fusion (MAD variance + MVUE-ish bidirectional fusion): `scripts/fuse_bidirectional_matrix_v3.py`
- SDP/MDS seed initializer: `scripts/sdp_init_v3.py`
- V3_full solver (Tukey IRLS + antenna-delay bias estimation): `scripts/solve_anchor_layout_v3_full.py`
- Convenience wrapper (fusion + solve in one command): `scripts/prepare_autopos_v3_full.py`

Notes:
- `sdp_init_v3.py` uses `cvxpy`+SCS if available; otherwise it falls back to classical MDS.
- Current V3_full implementation consumes Tag115 CM as **floating reference mean ranges** (aggregated capture), not a per-epoch Ref115 localization loop.

### Step 1: V3 Fusion (Minimum Variance)

```python
def fuse_bidirectional_v3(d_ij_samples, d_ji_samples):
    """
    Minimum Variance Unbiased Estimator (MVUE) fusion.
    Optimal linear combination weighted by inverse variance.
    """
    mean_ij = np.mean(d_ij_samples)
    mean_ji = np.mean(d_ji_samples)
    
    # Robust variance using MAD (not sensitive to outliers)
    var_ij = mad_variance(d_ij_samples)
    var_ji = mad_variance(d_ji_samples)
    
    # MVUE weights: w_k ∝ 1/σ²_k
    # D_ij = (σ²_ji * d̄_ij + σ²_ij * d̄_ji) / (σ²_ij + σ²_ji)
    D_ij = (var_ji * mean_ij + var_ij * mean_ji) / (var_ij + var_ji)
    
    # Fused variance: 1/σ²_fused = 1/σ²_ij + 1/σ²_ji
    sigma2_fused = (var_ij * var_ji) / (var_ij + var_ji)
    
    # Bias detection: significant if > 3σ
    bias = abs(mean_ij - mean_ji)
    high_bias = bias > 3 * np.sqrt(var_ij + var_ji)
    
    return D_ij, sigma2_fused, high_bias

def mad_variance(samples):
    """MAD-based robust variance estimate."""
    samples = np.array(samples)
    median = np.median(samples)
    mad = np.median(np.abs(samples - median))
    # Scale factor 1.4826 makes MAD consistent with Gaussian σ
    sigma = 1.4826 * mad
    return sigma ** 2
```

### Step 2: SDP Global Initialization

**Why SDP:** Iterative LS needs a good starting point. SDP is a convex relaxation that gives a globally optimal (or near-optimal) starting layout, regardless of initialization.

**Math:**

Let $\mathbf{G} = \mathbf{X}^T\mathbf{X}$ be the Gram matrix where $\mathbf{X} = [\mathbf{x}_1,\ldots,\mathbf{x}_8]$.

Key identity: $\|\mathbf{x}_i - \mathbf{x}_j\|^2 = G_{ii} - 2G_{ij} + G_{jj}$

SDP relaxation (drop rank-3 constraint, keep PSD):

$$\min_{\mathbf{G} \succeq 0} \sum_{(i,j)\in E} w_{ij}\left(G_{ii} - 2G_{ij} + G_{jj} - D_{ij}^2\right)^2$$

Recover coordinates via rank-3 truncated eigendecomposition.

```python
# sdp_init.py
import numpy as np
import cvxpy as cp

def sdp_initialization(fused_distances, anchor_ids, weights=None):
    """
    Solve SDP relaxation of anchor layout problem.
    Returns: initial anchor positions (n x 3 array)
    """
    n = len(anchor_ids)
    idx = {a: i for i, a in enumerate(anchor_ids)}
    
    # Gram matrix variable (n x n, symmetric PSD)
    G = cp.Variable((n, n), symmetric=True)
    
    constraints = [G >> 0]  # PSD constraint
    
    # Build objective
    obj_terms = []
    for (i, j), D_ij in fused_distances.items():
        ii, jj = idx[i], idx[j]
        w = weights.get((i, j), 1.0) if weights else 1.0
        
        # ||x_i - x_j||² = G_ii - 2*G_ij + G_jj
        dist_sq_expr = G[ii, ii] - 2*G[ii, jj] + G[jj, jj]
        residual = dist_sq_expr - D_ij**2
        obj_terms.append(w * cp.square(residual))
    
    objective = cp.Minimize(cp.sum(obj_terms))
    prob = cp.Problem(objective, constraints)
    
    # Solve with SCS (fast) or MOSEK (accurate, needs license)
    prob.solve(solver=cp.SCS, verbose=False,
               eps=1e-6, max_iters=10000)
    
    if prob.status not in ['optimal', 'optimal_inaccurate']:
        raise RuntimeError(f"SDP failed: {prob.status}")
    
    G_val = G.value
    
    # Rank-3 recovery via eigendecomposition
    eigenvalues, eigenvectors = np.linalg.eigh(G_val)
    # Sort descending
    idx_sort = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[idx_sort]
    eigenvectors = eigenvectors[:, idx_sort]
    
    # Check rank gap (quality indicator)
    rank_gap = eigenvalues[3] / (eigenvalues[2] + 1e-10)
    if rank_gap > 0.1:
        print(f"WARNING: rank gap = {rank_gap:.3f} > 0.1, "
              f"data quality may be poor")
    
    # Take top 3 eigenvectors
    lam = eigenvalues[:3]
    V = eigenvectors[:, :3]
    
    # X = V * sqrt(Λ)  →  shape (n, 3)
    X = V * np.sqrt(np.maximum(lam, 0))
    
    # Center at origin
    X -= X.mean(axis=0)
    
    # Align: fix A at origin, B on x-axis
    # (removes gauge freedom: 3 translation + 3 rotation DOF)
    X = align_to_gauge(X)
    
    return {a: X[i].tolist() for i, a in enumerate(anchor_ids)}

def align_to_gauge(X):
    """
    Remove gauge freedom by fixing:
    - Anchor 0 (A) at origin
    - Anchor 1 (B) on positive x-axis
    - Anchor 2 (C) in x-y plane (z=0 for C)
    """
    # Translate A to origin
    X = X - X[0]
    
    # Rotate so B is on x-axis
    b = X[1]
    b_norm = np.linalg.norm(b)
    if b_norm > 1e-10:
        # Rotation matrix to align b with [1,0,0]
        e1 = np.array([1., 0., 0.])
        R = rotation_matrix_align(b/b_norm, e1)
        X = X @ R.T
    
    # Reflect if needed so C has positive y
    if X[2, 1] < 0:
        X[:, 1] *= -1
    
    return X
```

### Step 3: Antenna Delay Joint Estimation

**Why:** DW1000 has per-module antenna delay $\tau_i \approx \pm 1\text{ns}$, causing $\pm 15\text{cm}$ ranging error. Currently absorbed by layout → layout is systematically wrong.

**Model:**

$$\hat{d}_{ij} = \|\mathbf{x}_i - \mathbf{x}_j\| + \frac{c}{2}(\tau_i + \tau_j) + \epsilon_{ij}$$

**Residual with delay correction:**

$$r_{ij} = \hat{d}_{ij} - \|\mathbf{x}_i - \mathbf{x}_j\| - \frac{c}{2}(\tau_i + \tau_j)$$

**Step C: Solve for delays analytically (linear problem):**

For each anchor pair $(i,j)$, define:
$$b_{ij} = \hat{d}_{ij} - \|\mathbf{x}_i - \mathbf{x}_j\|$$

Then: $b_{ij} \approx \frac{c}{2}(\tau_i + \tau_j)$

Build linear system $\mathbf{A}\boldsymbol{\tau} = \mathbf{b}$:
- Row for pair $(i,j)$: $A_{row,i} = c/2$, $A_{row,j} = c/2$, rest zero
- Regularization: $\mu \mathbf{I}$ prevents delay from absorbing everything
- Fix $\tau_A = 0$ (gauge freedom: all delays + constant = unobservable)

```python
def solve_antenna_delays(layout, fused_distances, anchor_ids,
                          irls_weights, mu_reg=1e-4):
    """
    Solve for antenna delays analytically given fixed anchor positions.
    
    Fix tau_A = 0 to remove gauge freedom.
    Returns: dict {anchor_id: tau_value_in_seconds}
    """
    n = len(anchor_ids)
    idx = {a: i for i, a in enumerate(anchor_ids)}
    X = np.array([layout[a] for a in anchor_ids])
    
    pairs = list(fused_distances.keys())
    m = len(pairs)
    
    # Build A matrix and b vector
    # Exclude anchor A (index 0) from unknowns → n-1 unknowns
    # tau_A = 0 fixed
    A = np.zeros((m, n-1))
    b_vec = np.zeros(m)
    W = np.zeros(m)
    
    free_ids = anchor_ids[1:]  # A is fixed, B..H are free
    free_idx = {a: i for i, a in enumerate(free_ids)}
    
    for row, (ai, aj) in enumerate(pairs):
        ii, jj = idx[ai], idx[aj]
        d_true = np.linalg.norm(X[ii] - X[jj])
        D_ij = fused_distances[(ai, aj)]
        
        b_val = D_ij - d_true  # ≈ c/2 * (tau_i + tau_j)
        b_vec[row] = b_val
        
        # Columns for tau_i and tau_j (skip A=fixed)
        if ai in free_idx:
            A[row, free_idx[ai]] = C_LIGHT / 2
        if aj in free_idx:
            A[row, free_idx[aj]] = C_LIGHT / 2
        
        W[row] = irls_weights.get((ai, aj), 1.0)
    
    # Weighted least squares with regularization
    # τ* = (A^T W A + μI)^{-1} A^T W b
    W_mat = np.diag(W)
    AtW = A.T @ W_mat
    tau_free = np.linalg.solve(
        AtW @ A + mu_reg * np.eye(n-1),
        AtW @ b_vec
    )
    
    delays = {'A': 0.0}
    for a, i in free_idx.items():
        delays[a] = tau_free[i]
    
    return delays
```

### Step 4: Tukey Bisquare IRLS

**Why Tukey over Huber:** Tukey completely zeroes out outlier weight when residual > threshold. Huber only reduces it linearly. For NLOS-contaminated UWB, hard zeroing is more appropriate.

```python
def tukey_bisquare(residuals, c_T=None, sigma_est=None):
    """
    Tukey Bisquare loss and IRLS weights.
    
    c_T = 4.685 * sigma  (95% efficiency under Gaussian)
    
    Weight function:
      w(r) = (1 - (r/c_T)²)²   if |r| ≤ c_T
      w(r) = 0                   if |r| > c_T
    """
    residuals = np.array(residuals)
    
    if c_T is None:
        if sigma_est is None:
            # Robust sigma estimate from MAD
            sigma_est = 1.4826 * np.median(np.abs(residuals - np.median(residuals)))
        c_T = 4.685 * sigma_est
    
    weights = np.zeros_like(residuals)
    mask = np.abs(residuals) <= c_T
    u = residuals[mask] / c_T
    weights[mask] = (1 - u**2)**2
    
    return weights, c_T

def irls_iteration(residuals, current_weights, c_T):
    """One IRLS weight update step."""
    new_weights, _ = tukey_bisquare(residuals, c_T=c_T)
    return new_weights
```

### V3 Complete Solver Loop

```python
def solve_v3(fused_distances, ref115_data, anchor_ids=ANCHOR_IDS,
              max_iters=20, eps_pos=1.0e-3, eps_J=1e-4,
              lambda_0=1.0, gamma=0.65, mu_delay=1e-4):
    """
    V3 complete alternating optimization loop.
    
    Variables optimized:
      - {x_i}: anchor positions
      - {tau_i}: antenna delays  
      - {r_k}: Ref115 positions
    
    Steps per iteration:
      A: optimize anchor positions (LM, Tukey IRLS)
      B: localize Ref115 (independent per time step)
      C: solve antenna delays (analytic)
    """
    
    # Phase 1: SDP global initialization
    print("Running SDP initialization...")
    layout = sdp_initialization(fused_distances, anchor_ids)
    
    # Initialize delays to zero
    delays = {a: 0.0 for a in anchor_ids}
    delays['ref'] = 0.0
    
    # Initialize Ref115 positions using current layout
    ref_positions = localize_ref115(layout, delays, ref115_data)
    
    # Initialize IRLS weights to uniform
    irls_weights = {k: 1.0 for k in fused_distances}
    
    J_prev = np.inf
    
    for t in range(max_iters):
        lambda_t = lambda_0 * (gamma ** t)
        
        print(f"\n--- Iteration {t} | lambda={lambda_t:.4f} ---")
        
        # Step A: optimize anchor positions (fixed delays, ref positions)
        layout_new = optimize_anchor_positions_v3(
            layout, fused_distances, delays, ref_positions, ref115_data,
            irls_weights, lambda_t
        )
        
        # Step B: re-localize Ref115 with updated layout
        ref_positions_new = localize_ref115(layout_new, delays, ref115_data)
        
        # Step C: solve antenna delays analytically
        delays_new = solve_antenna_delays(
            layout_new, fused_distances, anchor_ids, irls_weights, mu_delay
        )
        
        # Recompute residuals with new delays
        residuals = compute_all_residuals(
            layout_new, delays_new, fused_distances,
            ref_positions_new, ref115_data
        )
        
        # Update IRLS weights
        all_res = np.array(list(residuals.values()))
        sigma_est = 1.4826 * np.median(np.abs(all_res))
        c_T = 4.685 * sigma_est
        
        new_weights = {}
        for k, r in residuals.items():
            w, _ = tukey_bisquare(np.array([r]), c_T=c_T)
            new_weights[k] = float(w[0])
        
        # Compute J
        J_curr = compute_total_J_v3(
            layout_new, delays_new, fused_distances,
            ref_positions_new, ref115_data, new_weights
        )
        
        # Convergence check (BOTH required)
        max_disp = max(
            np.linalg.norm(np.array(layout_new[a]) - np.array(layout[a]))
            for a in anchor_ids
        )
        rel_J = abs(J_curr - J_prev) / (J_prev + 1e-12)
        
        rms_dist = compute_rms_residual(layout_new, delays_new, fused_distances)
        
        print(f"J={J_curr:.6f}, max_disp={max_disp*1000:.2f}mm, "
              f"rel_J={rel_J:.2e}, RMS_dist={rms_dist*1000:.2f}mm")
        print(f"Delays: { {a: f'{v*1e9:.3f}ns' for a,v in delays_new.items()} }")
        
        # Update state
        layout = layout_new
        delays = delays_new
        ref_positions = ref_positions_new
        irls_weights = new_weights
        J_prev = J_curr
        
        if max_disp < eps_pos and rel_J < eps_J:
            print(f"\nConverged at iteration {t}")
            break
    
    # Compute quality metrics
    quality = compute_layout_quality(layout, delays, fused_distances, ref_positions, ref115_data)
    
    return layout, delays, quality
```

### V3 Quality Metrics

```python
def compute_layout_quality(layout, delays, fused_distances,
                             ref_positions, ref115_data):
    # RMS of inter-anchor distance residuals
    res_dist = []
    for (i, j), D_ij in fused_distances.items():
        xi = np.array(layout[i])
        xj = np.array(layout[j])
        tau_corr = C_LIGHT/2 * (delays[i] + delays[j])
        r = D_ij - np.linalg.norm(xi - xj) - tau_corr
        res_dist.append(r)
    rms_dist = np.sqrt(np.mean(np.array(res_dist)**2))
    
    # RMS of Ref115 residuals
    res_ref = compute_ref115_residuals(layout, delays, ref_positions, ref115_data)
    rms_ref = np.sqrt(np.mean(np.array(res_ref)**2))
    
    # Jacobian condition number
    J_mat = build_jacobian(layout, fused_distances)
    sv = np.linalg.svd(J_mat, compute_uv=False)
    cond = sv[0] / (sv[-1] + 1e-12)
    
    # Pass/Fail
    passed = (rms_dist < 0.008 and rms_ref < 0.015 and cond < 500)
    
    return {
        'rms_dist_mm': rms_dist * 1000,
        'rms_ref_mm': rms_ref * 1000,
        'condition_number': cond,
        'passed': passed
    }
```

---

## V4

### What V4 Adds Over V3
1. **RotArm geometric model** — rigid body constraint
2. **$J_{arm}$ constraint term** — circle arc measurements injected into solver
3. **Step D in alternating loop** — RotArm parameter optimization
4. **$\theta_k$ self-calibration** — rotation angles solved from UWB data (no encoder)
5. **Z-axis information injection** — tilted rotation fills FIM Z component

### Why Z-Axis Needs Special Treatment

FIM Z component:

$$F_{zz}(\mathbf{p}) = \sum_{i=1}^{8} \frac{1}{\sigma_i^2} \left(\frac{p_z - x_{i,z}}{\|\mathbf{p}-\mathbf{x}_i\|}\right)^2$$

When all anchors are at the same height: $p_z \approx x_{i,z}$ → $u_{i,z} \approx 0$ → $F_{zz} \approx 0$

Z-axis CRLB blows up. The rotating arm at tilt angle $\alpha$ creates:

$$\Delta z = 2r\sin\alpha$$

At $r=0.50\text{m}$, $\alpha=45°$: $\Delta z = 0.71\text{m}$ of Z variation per revolution. This continuously fills $F_{zz}$.

### RotArm Geometry Model

```python
# rotarm_geometry.py
import numpy as np

class RotArm:
    """
    Rotating arm with two tags at opposite ends.
    
    Tag 1 (short arm): distance r1 from center
    Tag 2 (long arm):  distance r2 from center
    Tags are on opposite sides: p2 = c - r2 * direction
    """
    
    def __init__(self, r1=0.30, r2=0.50):
        self.r1 = r1   # short arm
        self.r2 = r2   # long arm
    
    def compute_tag_positions(self, center, n_hat, theta):
        """
        Compute tag positions given rotation state.
        
        center: np.array [cx, cy, cz], rotation center
        n_hat:  np.array [nx, ny, nz], rotation axis (unit vector)
                from LIS2DH12 accelerometer measurement
        theta:  float, rotation angle in radians
        
        Returns: (p1, p2) — positions of short-arm and long-arm tags
        """
        # Build rotation plane basis vectors
        e1, e2 = self._build_basis(n_hat)
        
        # Direction in rotation plane at angle theta
        direction = np.cos(theta) * e1 + np.sin(theta) * e2
        
        p1 = center + self.r1 * direction
        p2 = center - self.r2 * direction  # opposite side
        
        return p1, p2
    
    def _build_basis(self, n_hat):
        """Build orthonormal basis for rotation plane."""
        n_hat = n_hat / np.linalg.norm(n_hat)
        
        # Choose auxiliary vector not parallel to n_hat
        if abs(n_hat[0]) < 0.9:
            aux = np.array([1., 0., 0.])
        else:
            aux = np.array([0., 1., 0.])
        
        e1 = np.cross(n_hat, aux)
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(n_hat, e1)
        e2 /= np.linalg.norm(e2)
        
        return e1, e2
    
    def rigid_body_check(self, p1, p2):
        """Verify rigid body constraint."""
        dist_12 = np.linalg.norm(p1 - p2)
        expected = self.r1 + self.r2
        error = abs(dist_12 - expected)
        return error  # should be < 1mm
```

### Reading Tilt Angle from LIS2DH12

```python
def read_rotation_axis_from_imu(acc_samples_static):
    """
    Read tilt angle from LIS2DH12 when arm is STATIONARY.
    
    Before each rotation session:
    1. Hold arm still for 1 second
    2. Read accelerometer
    3. Gravity direction = rotation axis n_hat
    
    acc_samples_static: list of (ax, ay, az) readings during static period
    Returns: n_hat (unit vector of rotation axis)
    """
    # Filter: only use samples where device is truly static
    acc = np.array(acc_samples_static)
    
    # Check static: variance should be very small
    acc_norm = np.linalg.norm(acc, axis=1)
    variance = np.var(acc_norm - 9.81)
    
    if variance > 0.01:  # m/s² threshold
        print("WARNING: Device not static during IMU reading")
    
    # Mean gravity vector
    g_mean = np.mean(acc, axis=0)
    n_hat = g_mean / np.linalg.norm(g_mean)
    
    return n_hat
```

### Step D: θ_k Self-Calibration

**Key insight:** With 8 anchors and 2 tags, we have 16 distance measurements per time step for just 1 unknown ($\theta_k$). Highly overdetermined → robust recovery.

```python
from scipy.optimize import minimize_scalar

def solve_theta_k(anchor_positions, anchor_delays,
                   arm, center, n_hat,
                   d_i1k, d_i2k, anchor_ids,
                   irls_weights, c_T):
    """
    Solve for rotation angle theta_k at time step k.
    
    Uses Golden Section Search over [0, 2π].
    Each theta_k is independent given center and n_hat.
    
    d_i1k: dict {anchor_id: measured_distance_to_tag1}
    d_i2k: dict {anchor_id: measured_distance_to_tag2}
    """
    
    def objective_theta(theta):
        p1, p2 = arm.compute_tag_positions(center, n_hat, theta)
        
        J = 0.0
        for a_id in anchor_ids:
            xi = np.array(anchor_positions[a_id])
            tau_i = anchor_delays.get(a_id, 0.0)
            tau_tag = anchor_delays.get('tag1', 0.0)
            
            # Tag 1
            if a_id in d_i1k:
                d_meas = d_i1k[a_id]
                d_pred = np.linalg.norm(xi - p1) + C_LIGHT/2*(tau_i + tau_tag)
                r = d_meas - d_pred
                w, _ = tukey_bisquare(np.array([r]), c_T=c_T)
                J += float(w[0]) * r**2
            
            # Tag 2
            if a_id in d_i2k:
                d_meas = d_i2k[a_id]
                d_pred = np.linalg.norm(xi - p2) + C_LIGHT/2*(tau_i + tau_tag)
                r = d_meas - d_pred
                w, _ = tukey_bisquare(np.array([r]), c_T=c_T)
                J += float(w[0]) * r**2
        
        return J
    
    # Golden Section Search over [0, 2π]
    result = minimize_scalar(
        objective_theta,
        bounds=(0, 2*np.pi),
        method='bounded',
        options={'xatol': 1e-6}
    )
    
    return result.x, result.fun
```

### V4 Additional Constraint Term

$$J_{arm} = \sum_{m}\sum_{i}\sum_{k} \left[\rho_T(r_{i,1,mk}) + \rho_T(r_{i,2,mk})\right]$$

Where:
- $m$: RotArm session index (different placements)
- $k$: time step within session
- $r_{i,1,mk} = d_{i,1,mk} - \|\mathbf{x}_i - \mathbf{p}_1(\theta_{mk})\| - \frac{c}{2}(\tau_i + \tau_{tag1})$

### V4 Complete Loop

```python
def solve_v4(fused_distances, ref115_data, rotarm_sessions,
              anchor_ids=ANCHOR_IDS, max_iters=20,
              eps_pos=1.0e-3, eps_J=1e-4,
              lambda_0=1.0, gamma=0.65):
    """
    V4 alternating optimization with RotArm.
    
    rotarm_sessions: list of {
        'center_init': [cx, cy, cz],  # rough initial guess
        'n_hat': [nx, ny, nz],         # from LIS2DH12
        'measurements': [              # per time step
            {
                'tag1': {anchor_id: distance, ...},
                'tag2': {anchor_id: distance, ...}
            }, ...
        ]
    }
    
    Steps:
      A: optimize anchor positions
      B: localize Ref115
      C: solve antenna delays (analytic)
      D1: optimize RotArm centers {c_m}
      D2: solve rotation angles {theta_mk} (1D search, independent)
    """
    
    # Initialize
    layout = sdp_initialization(fused_distances, anchor_ids)
    delays = {a: 0.0 for a in anchor_ids}
    ref_positions = localize_ref115(layout, delays, ref115_data)
    
    # Initialize RotArm parameters
    arm = RotArm(r1=ARM_LENGTH_SHORT, r2=ARM_LENGTH_LONG)
    rotarm_params = []
    for session in rotarm_sessions:
        n_steps = len(session['measurements'])
        rotarm_params.append({
            'center': np.array(session['center_init']),
            'n_hat':  np.array(session['n_hat']),
            'thetas': np.linspace(0, 2*np.pi, n_steps)  # uniform init
        })
    
    irls_weights = {k: 1.0 for k in fused_distances}
    J_prev = np.inf
    
    for t in range(max_iters):
        lambda_t = lambda_0 * (gamma ** t)
        
        # Estimate sigma for Tukey threshold
        all_res = compute_all_residuals_v4(
            layout, delays, fused_distances,
            ref_positions, ref115_data,
            rotarm_params, rotarm_sessions, arm
        )
        sigma_est = 1.4826 * np.median(np.abs(all_res))
        c_T = 4.685 * sigma_est
        
        # Step A: optimize anchor positions
        layout_new = optimize_anchor_positions_v4(
            layout, fused_distances, delays,
            ref_positions, ref115_data,
            rotarm_params, rotarm_sessions, arm,
            irls_weights, lambda_t, c_T
        )
        
        # Step B: re-localize Ref115
        ref_positions_new = localize_ref115(layout_new, delays, ref115_data)
        
        # Step C: solve antenna delays
        delays_new = solve_antenna_delays(
            layout_new, fused_distances, anchor_ids, irls_weights
        )
        
        # Step D1: optimize RotArm centers
        for m, (session, params) in enumerate(
                zip(rotarm_sessions, rotarm_params)):
            center_new = optimize_rotarm_center(
                layout_new, delays_new, arm,
                params['n_hat'], params['thetas'],
                session['measurements'], irls_weights, c_T
            )
            rotarm_params[m]['center'] = center_new
        
        # Step D2: solve theta_k for each session and time step
        for m, (session, params) in enumerate(
                zip(rotarm_sessions, rotarm_params)):
            thetas_new = []
            for k, meas_k in enumerate(session['measurements']):
                theta_k, _ = solve_theta_k(
                    layout_new, delays_new, arm,
                    params['center'], params['n_hat'],
                    meas_k['tag1'], meas_k['tag2'],
                    anchor_ids, irls_weights, c_T
                )
                thetas_new.append(theta_k)
            rotarm_params[m]['thetas'] = np.array(thetas_new)
        
        # Update IRLS weights
        all_res_new = compute_all_residuals_v4(
            layout_new, delays_new, fused_distances,
            ref_positions_new, ref115_data,
            rotarm_params, rotarm_sessions, arm
        )
        new_irls = {}
        for k_idx, (k, _) in enumerate(fused_distances.items()):
            r = all_res_new[k_idx] if k_idx < len(all_res_new) else 0.0
            w, _ = tukey_bisquare(np.array([r]), c_T=c_T)
            new_irls[k] = float(w[0])
        
        # Convergence check
        max_disp = max(
            np.linalg.norm(np.array(layout_new[a]) - np.array(layout[a]))
            for a in anchor_ids
        )
        J_curr = compute_total_J_v4(
            layout_new, delays_new, fused_distances,
            ref_positions_new, ref115_data,
            rotarm_params, rotarm_sessions, arm, new_irls
        )
        rel_J = abs(J_curr - J_prev) / (J_prev + 1e-12)
        
        print(f"Iter {t:2d}: J={J_curr:.6f}, "
              f"max_disp={max_disp*1000:.2f}mm, rel_J={rel_J:.2e}")
        
        layout = layout_new
        delays = delays_new
        ref_positions = ref_positions_new
        irls_weights = new_irls
        J_prev = J_curr
        
        if max_disp < eps_pos and rel_J < eps_J:
            print(f"Converged at iteration {t}")
            break
    
    # Validate rigid body constraint
    validate_rotarm_rigid_body(rotarm_params, rotarm_sessions, arm)
    
    quality = compute_layout_quality_v4(
        layout, delays, fused_distances,
        ref_positions, ref115_data,
        rotarm_params, rotarm_sessions, arm
    )
    
    return layout, delays, rotarm_params, quality
```

---

## V5

### What V5 Adds Over V4
1. **GNC (Graduated Non-Convexity)** — replaces Tukey IRLS, provably global optimum
2. **Heteroscedastic noise model** — per-edge σ from MAD, not uniform
3. **Clock drift $\xi_i$ estimation** — separates TWR systematic error from layout
4. **RotArm global joint optimization** — all sessions optimized together
5. **RotArm smoothness constraint** — prevents θ_k from jumping
6. **Bias Learning outer loop** — removes systematic per-pair offsets
7. **Analytic Jacobian covariance** — replaces slow Bootstrap

### GNC: Graduated Non-Convexity

**Core idea:** Instead of directly minimizing non-convex Tukey loss, start with a convex surrogate and gradually tighten it to the target loss.

```python
class GNCOptimizer:
    """
    Graduated Non-Convexity optimizer.
    
    Loss family: ρ_μ(r) = μr² / (r² + μ)
    
    μ → ∞: approaches L2 (convex, easy)
    μ → 0: approaches truncated L2 (target robust loss)
    
    IRLS weight: w(r,μ) = μ / (r² + μ)²
    """
    
    def __init__(self, mu_max=1e4, mu_min=1e-2, mu_factor=1.4):
        self.mu_max = mu_max
        self.mu_min = mu_min
        self.mu_factor = mu_factor
    
    def loss(self, r, mu):
        return mu * r**2 / (r**2 + mu)
    
    def weight(self, r, mu):
        return mu / (r**2 + mu)**2
    
    def run(self, initial_params, inner_solver, max_inner_iters=10):
        """
        GNC outer loop.
        
        initial_params: starting point (from SDP)
        inner_solver: function(params, weights, mu) → new_params
        """
        params = initial_params
        mu = self.mu_max
        
        gnc_iter = 0
        while mu >= self.mu_min:
            print(f"GNC: mu={mu:.4f}")
            
            # Inner loop: solve with current mu
            for _ in range(max_inner_iters):
                residuals = compute_residuals(params)
                weights = {k: self.weight(r, mu) 
                          for k, r in residuals.items()}
                params_new = inner_solver(params, weights, mu)
                
                # Inner convergence
                inner_change = max_param_change(params, params_new)
                params = params_new
                if inner_change < 1e-4:
                    break
            
            mu /= self.mu_factor
            gnc_iter += 1
        
        return params
```

### Clock Drift Estimation (V5 New)

**Problem:** SS-TWR systematic error from crystal oscillator drift:

$$\hat{d}_{ij} = d_{true} + \frac{c}{2}\xi_j \cdot T_{reply,ij} + \frac{c}{2}(\tau_i + \tau_j)$$

- $\xi_j$: fractional frequency offset of node $j$ (typ. ±20 ppm)
- $T_{reply,ij}$: response delay time (readable from DW1000 timestamps)
- Effect: at $T_{reply}=3\text{ms}$, $\xi=20\text{ppm}$ → error ≈ 9mm

**V5 extended residual:**

$$r_{ij} = \hat{d}_{ij} - \|\mathbf{x}_i - \mathbf{x}_j\| - \frac{c}{2}(\tau_i + \tau_j) - \frac{c}{2}\xi_j T_{reply,ij}$$

```python
def solve_delays_and_drifts(layout, fused_distances, T_reply,
                              anchor_ids, irls_weights, 
                              mu_tau=1e-4, mu_xi=1e-6):
    """
    V5 Step C: Solve antenna delays AND clock drifts jointly.
    Both are linear → single analytic solve.
    
    T_reply: dict {(i,j): reply_time_seconds}
             Read from DW1000 timestamp registers.
             Direction (i,j) means j replied to i.
    
    Fix tau_A = 0 AND xi_A = 0 (gauge freedom).
    """
    n = len(anchor_ids)
    idx = {a: i for i, a in enumerate(anchor_ids)}
    X = np.array([layout[a] for a in anchor_ids])
    
    pairs = list(fused_distances.keys())
    m = len(pairs)
    
    # Unknowns: [tau_B, ..., tau_H, xi_A, xi_B, ..., xi_H]
    # tau_A = 0 fixed (n-1 delay unknowns)
    # xi_A = 0 fixed (n-1 drift unknowns)
    # Total: 2(n-1) unknowns
    n_free = 2 * (n - 1)
    
    A = np.zeros((m, n_free))
    b_vec = np.zeros(m)
    W = np.zeros(m)
    
    free_ids = anchor_ids[1:]
    free_tau_idx = {a: i for i, a in enumerate(free_ids)}
    free_xi_idx  = {a: i + (n-1) for i, a in enumerate(free_ids)}
    
    for row, (ai, aj) in enumerate(pairs):
        ii, jj = idx[ai], idx[aj]
        d_true = np.linalg.norm(X[ii] - X[jj])
        D_ij = fused_distances[(ai, aj)]
        T_rep = T_reply.get((ai, aj), 3e-3)  # default 3ms if missing
        
        b_val = D_ij - d_true
        b_vec[row] = b_val
        
        # Antenna delay columns: c/2 * (tau_i + tau_j)
        if ai in free_tau_idx:
            A[row, free_tau_idx[ai]] = C_LIGHT / 2
        if aj in free_tau_idx:
            A[row, free_tau_idx[aj]] = C_LIGHT / 2
        
        # Clock drift column: c/2 * xi_j * T_reply
        # (j is the responding node)
        if aj in free_xi_idx:
            A[row, free_xi_idx[aj]] = C_LIGHT / 2 * T_rep
        
        W[row] = irls_weights.get((ai, aj), 1.0)
    
    # Regularization: separate for delays and drifts
    Lambda = np.zeros(n_free)
    Lambda[:n-1]   = mu_tau  # delay regularization
    Lambda[n-1:]   = mu_xi   # drift regularization
    
    W_mat = np.diag(W)
    AtW = A.T @ W_mat
    solution = np.linalg.solve(
        AtW @ A + np.diag(Lambda),
        AtW @ b_vec
    )
    
    delays = {'A': 0.0}
    drifts = {'A': 0.0}
    
    for a in free_ids:
        delays[a] = solution[free_tau_idx[a]]
        drifts[a] = solution[free_xi_idx[a]]
    
    return delays, drifts
```

### Bias Learning Outer Loop

```python
def bias_learning_loop(fused_distances_orig, ref115_data,
                        rotarm_sessions, anchor_ids,
                        inner_solver,  # V4 or V5 inner loop
                        delta_bias=1e-3, max_bias_iters=10):
    """
    V5 outer loop: learn and remove systematic per-pair biases.
    
    Systematic bias sources:
    - Fixed partial obstruction between anchor pair
    - Antenna directivity pattern
    - Firmware-level ranging bias
    
    These are constant across measurements → learn and subtract.
    """
    fused_distances = fused_distances_orig.copy()
    biases = {k: 0.0 for k in fused_distances}
    
    for outer_iter in range(max_bias_iters):
        # Run inner solver with current (bias-corrected) distances
        layout, delays, drifts, *rest = inner_solver(
            fused_distances, ref115_data, rotarm_sessions, anchor_ids
        )
        
        # Compute per-pair residual means
        new_biases = {}
        max_bias_change = 0.0
        
        for (i, j), D_ij in fused_distances.items():
            xi = np.array(layout[i])
            xj = np.array(layout[j])
            tau_corr = C_LIGHT/2 * (delays[i] + delays[j])
            r = D_ij - np.linalg.norm(xi - xj) - tau_corr
            new_biases[(i,j)] = r  # mean residual = systematic bias
            max_bias_change = max(max_bias_change, 
                                   abs(r - biases[(i,j)]))
        
        print(f"Bias iter {outer_iter}: "
              f"max_bias={max(abs(v) for v in new_biases.values())*1000:.2f}mm, "
              f"max_change={max_bias_change*1000:.2f}mm")
        
        # Subtract biases from measurements
        for k in fused_distances:
            fused_distances[k] = fused_distances_orig[k] - new_biases[k]
        
        biases = new_biases
        
        if max_bias_change < delta_bias:
            print(f"Bias learning converged at iter {outer_iter}")
            break
    
    return layout, delays, drifts, biases
```

### V5 Analytic Covariance (replaces Bootstrap)

```python
def compute_analytic_covariance(layout, delays, fused_distances,
                                  irls_weights, anchor_ids):
    """
    Compute anchor position covariance from Jacobian.
    
    Σ_x = (J^T W J)^{-1}  (block for position variables)
    
    Returns: dict {anchor_id: 3x3 covariance matrix}
    """
    n = len(anchor_ids)
    idx = {a: i for i, a in enumerate(anchor_ids)}
    X = np.array([layout[a] for a in anchor_ids])
    
    # Build Jacobian: ∂r_{ij} / ∂x_k
    pairs = list(fused_distances.keys())
    m = len(pairs)
    n_pos = 3 * n  # position variables only
    
    J = np.zeros((m, n_pos))
    W_diag = np.zeros(m)
    
    for row, (ai, aj) in enumerate(pairs):
        ii, jj = idx[ai], idx[aj]
        xi = X[ii]
        xj = X[jj]
        diff = xi - xj
        dist = np.linalg.norm(diff)
        
        if dist < 1e-6:
            continue
        
        unit = diff / dist  # unit vector i→j
        
        # ∂r/∂x_i = -unit (residual decreases as x_i moves toward x_j)
        J[row, 3*ii:3*ii+3] = -unit
        J[row, 3*jj:3*jj+3] =  unit
        
        W_diag[row] = irls_weights.get((ai, aj), 1.0)
    
    W_mat = np.diag(W_diag)
    
    # Fisher Information Matrix for positions
    FIM = J.T @ W_mat @ J  # (3n x 3n)
    
    # Add small regularization for stability
    FIM += 1e-8 * np.eye(n_pos)
    
    Sigma = np.linalg.inv(FIM)
    
    # Extract per-anchor 3x3 blocks
    covariances = {}
    for a, i in idx.items():
        covariances[a] = Sigma[3*i:3*i+3, 3*i:3*i+3]
    
    # Condition number
    sv = np.linalg.svd(J, compute_uv=False)
    cond = sv[0] / (sv[-1] + 1e-12)
    
    return covariances, cond

def confidence_ellipsoid_radii(cov_3x3, confidence=0.95):
    """
    Compute 95% confidence ellipsoid semi-axes from covariance.
    Uses chi-squared distribution with 3 DOF.
    """
    from scipy.stats import chi2
    chi2_val = chi2.ppf(confidence, df=3)
    
    eigenvalues = np.linalg.eigvalsh(cov_3x3)
    radii = np.sqrt(chi2_val * np.maximum(eigenvalues, 0))
    
    return sorted(radii, reverse=True)  # [major, middle, minor] axes
```

---

## Shared Utilities

### CRLB Heatmap

```python
def compute_crlb_heatmap(layout, delays, sigma_ranging,
                           workspace_bounds, grid_resolution=0.1):
    """
    Compute GDOP and Z-accuracy heatmap over workspace.
    
    workspace_bounds: {'x': (xmin, xmax), 'y': (ymin, ymax), 'z': z_tag}
    grid_resolution: meters between grid points
    
    Returns: 2D arrays of GDOP and sigma_z values
    """
    x_range = np.arange(*workspace_bounds['x'], grid_resolution)
    y_range = np.arange(*workspace_bounds['y'], grid_resolution)
    z_tag = workspace_bounds.get('z', 1.0)
    
    anchor_positions = np.array([layout[a] for a in ANCHOR_IDS])
    
    GDOP = np.zeros((len(y_range), len(x_range)))
    SIGMA_Z = np.zeros_like(GDOP)
    
    for yi, y in enumerate(y_range):
        for xi, x in enumerate(x_range):
            p = np.array([x, y, z_tag])
            
            # Fisher Information Matrix
            F = np.zeros((3, 3))
            for x_anc in anchor_positions:
                diff = p - x_anc
                dist = np.linalg.norm(diff)
                if dist < 0.1:
                    continue
                u = diff / dist
                F += (1.0 / sigma_ranging**2) * np.outer(u, u)
            
            # Invert FIM
            try:
                F_inv = np.linalg.inv(F + 1e-10 * np.eye(3))
                GDOP[yi, xi] = np.sqrt(np.trace(F_inv))
                SIGMA_Z[yi, xi] = np.sqrt(F_inv[2, 2])
            except np.linalg.LinAlgError:
                GDOP[yi, xi] = np.inf
                SIGMA_Z[yi, xi] = np.inf
    
    return x_range, y_range, GDOP, SIGMA_Z
```

### File I/O

```python
import json
import csv

def save_layout(layout, delays, drifts, quality, version, path):
    output = {
        'version': version,
        'anchors': {a: list(pos) for a, pos in layout.items()},
        'antenna_delays_ns': {a: v*1e9 for a, v in delays.items()},
        'clock_drifts_ppm': {a: v*1e6 for a, v in drifts.items()},
        'quality': quality
    }
    with open(path, 'w') as f:
        json.dump(output, f, indent=2)
    print(f"Layout saved to {path}")

def load_pairs_csv(path):
    """Load bidirectional sweep CSV."""
    data = {}
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            i, j = row['anchor_i'], row['anchor_j']
            d = float(row['distance_m'])
            key = (i, j)
            if key not in data:
                data[key] = []
            data[key].append(d)
    return data
```

---

## File Structure

```
autopos/
├── v1_solver.py              # V1: naive LS
├── v2_solver.py              # V2: bidirectional + Ref115 loop
├── v3_solver.py              # V3: SDP + antenna delay + Tukey
├── v4_solver.py              # V4: + RotArm
├── v5_solver.py              # V5: + GNC + clock drift + bias learning
│
├── sdp_init.py               # SDP global initialization (shared V3+)
├── rotarm_geometry.py        # RotArm geometry model (V4+)
├── gnc_optimizer.py          # GNC outer loop (V5)
├── crlb.py                   # CRLB heatmap and FIM computation
├── quality.py                # Layout quality scoring
│
├── fuse_bidirectional.py     # V2/V3 fusion (simple / MVUE)
├── dynamic_sweep.py          # Dynamic N convergence sweep
│
├── prepare_autopos_v2.py     # Existing V2 entry point (keep)
├── prepare_autopos_v3.py     # V3 entry point
├── prepare_autopos_v4.py     # V4 entry point
├── prepare_autopos_v5.py     # V5 entry point
│
└── data/
    ├── pairs_all.csv
    ├── final_pair_distances_v2.csv
    ├── inter_anchor_matrix_v2fused.json
    ├── ref115_cm_baseline.csv
    └── anchor_layout_vX_final.json
```

---

## Key Parameters Reference

| Parameter | V2 | V3 | V4 | V5 | Notes |
|-----------|----|----|----|----|-------|
| N sweep per pair | 50 (fixed) | dynamic | dynamic | dynamic | Your experiments: floor at 40–50 |
| ε_conv sweep | — | 0.5mm | 0.5mm | 0.5mm | Dynamic N threshold |
| λ_0 (prior weight) | 1.0 | 1.0 | 1.0 | 1.0 | Initial prior strength |
| γ (prior decay) | 0.65 | 0.65 | 0.65 | 0.65 | Per-iteration decay |
| ε_pos (convergence) | 1.5mm | 1.0mm | 1.0mm | 1.0mm | Position change threshold |
| ε_J (convergence) | 1e-4 | 1e-4 | 1e-4 | 1e-4 | Relative J change |
| max_iters | 8 | 20 | 20 | 20 | |
| c_T (Tukey) | — | 4.685σ | 4.685σ | — | replaced by GNC in V5 |
| μ_max (GNC) | — | — | — | 1e4 | GNC start (near L2) |
| μ_min (GNC) | — | — | — | 1e-2 | GNC end (near Tukey) |
| μ_factor (GNC) | — | — | — | 1.4 | Annealing rate |
| μ_delay reg | — | 1e-4 | 1e-4 | 1e-4 | Antenna delay regularization |
| μ_drift reg | — | — | — | 1e-6 | Clock drift regularization |
| r1 (arm short) | — | — | 0.30m | 0.30m | Measured precisely |
| r2 (arm long) | — | — | 0.50m | 0.50m | Measured precisely |
| δ_bias | — | — | — | 1mm | Bias learning convergence |

---

## Expected Performance

| Version | Layout RMS | Tag RMS | Z Accuracy | Key Limitation |
|---------|-----------|---------|------------|----------------|
| V1 | ~50mm | ~100mm | Very poor | No fusion, L2, local minima |
| V2 | ~10mm | ~20mm | Poor (3–5× XY) | Hand-tuned weights, L2 loss |
| V3 | ~5mm | ~12mm | Poor (2–3× XY) | No Z injection |
| V4 | ~3mm | ~8mm | ≈ XY | No clock drift, local optima possible |
| V5 | ~1.5mm | ~4mm | ≈ XY | Hardware limit |

---

*AutoPos Implementation Guide v0.1*  
*DWM1001C 8-Anchor UWB System*

---

## Fresh Sweep + Tag115 CM + V1/V2/V3-lite Compare (2026-04-13)

目标: **每次都重新采集**一份全量 A-H sweep (100 set) + Tag115 (例如 `BSF66F`) 的 CM 聚合输出 (100 行), 然后离线跑:

- V1: `fuse_bidirectional_matrix_v1.py`
- V2: `prepare_autopos_v2.py`
- V3-lite: `prepare_autopos_v3_lite.py`

并产出 **V1/V2/V3 对比报告** (pair distance diff)。

一键命令:

```bash
python3 scripts/run_autopos_capture_once_and_solve_v1_v2_v3.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 100 \
  --tag-name BSF66F \
  --cm-lines 100
```

重要参数:
- `--timeout-s <sec>`: 不填时会根据 `--sw-sets` 自动放大 (由 `run_autopos_sweep_loop.py` 决定)。
- `--quiet-tag-name BSF66F` / `--quiet-tag-name -`:
  - 默认会 best-effort 把 Tag quarantine 到 `MODE AOTA` (并尝试 `STREAM OFF`) 以降低 sweep 期间 Tag 对 anchor sweep 的影响。
  - **即使 quiet-tag 失败也不会再硬退出 sweep**，会记录 `PRECHECK WARN: tag quarantine not reached ...; continuing sweep`。
- `--floating-reference-z-prior-mm <mm>`:
  - 如果提供了 Tag115 CM 的 floating reference (`ranges.csv`)，solver 会对该 reference 的 `z` 加一个软先验。
  - 不填时默认取 `820mm`（历史 Ref115 floor-height prior，用于改善 Z 轴可观测性不足导致的漂移）。

主要产物:
- `autopos_V3/logs/v123_fresh_YYYYmmdd_HHMMSS/`
  - `capture_*/sweep/summary.json`
  - `capture_*/tag115_cm/run.log`
  - `solve_*/pairs_all.csv`
  - `solve_*/v1/final_pair_distances.csv`
  - `solve_*/v1/anchor_layout_v1_soft_iterative.json` (V1-soft: 也走 iterative solver 的软约束版本)
  - `solve_*/v2/v2_fused/final_pair_distances_v2.csv`
  - `solve_*/v3_lite/v3_fused/final_pair_distances_v2.csv`
  - `compare_v1_v2_v3_pairs.md`
  - `compare_v1_v2_v3_layouts.md` (V1/V2/V3 的 layout 刚体对齐后对比)
  - `run_manifest.json` (记录本次 run 的所有路径)
