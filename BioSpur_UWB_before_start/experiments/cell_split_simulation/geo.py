"""
geo.py — GPU-first geometry kernels for the cell-split layout simulation.

PURE SIMULATION. No hardware. All heavy math (elevation angles, DOP Jacobian
4x4 inverse) runs batched on the GPU in FP32. CPU is capped at 2 threads.

Conventions (see REPORT.md 'Provenance & assumptions'):
  - Positions in millimetres in the solved anchor frame.
  - Up = -z  (high ring E-H sits at z ~ -1500..-1780; floor datum = low-ring
    z=0 plane). A tag at height h above the floor has z = -h.
  - elevation(link) = asin(|dz| / range)  (angle of the link above horizontal).
  - DOP: 4-unknown pseudo-range model (x,y,z,clock). Row_i = [ux,uy,uz, 1].
    Cov = (H^T H)^-1 (unit weight). GDOP=sqrt(trace), HDOP=sqrt(Cxx+Cyy),
    VDOP=sqrt(Czz). The clock unknown is included because this system carries a
    real per-epoch common-mode range bias (the common-mode / scale-lock saga),
    and the prompt asks for the 4x4 formulation. A clock-free 3x3 TOA VDOP is
    also returned as a secondary (lower) reference.
"""
import torch

DEG = 180.0 / 3.141592653589793


def get_device(idx=0):
    if torch.cuda.is_available():
        return torch.device(f"cuda:{idx}")
    return torch.device("cpu")


def make_grid(x_lo, x_hi, y_lo, y_hi, heights_mm, hstep_mm=100.0, device="cpu"):
    """Build a 3D grid of tag positions (mm). Horizontal step hstep_mm (0.1m),
    vertical slices given explicitly. Returns (P[N,3], nx, ny, nz, xs, ys)."""
    xs = torch.arange(x_lo, x_hi + 1e-6, hstep_mm, dtype=torch.float32)
    ys = torch.arange(y_lo, y_hi + 1e-6, hstep_mm, dtype=torch.float32)
    zs = torch.tensor([-h for h in heights_mm], dtype=torch.float32)  # up=-z
    gx, gy, gz = torch.meshgrid(xs, ys, zs, indexing="ij")
    P = torch.stack([gx.reshape(-1), gy.reshape(-1), gz.reshape(-1)], dim=1)
    return P.to(device), len(xs), len(ys), len(zs), xs, ys


def link_geometry(P, anchors, eps=1e-6):
    """P[N,3] tags, anchors[M,3]. Returns:
        rng[N,M]      3D range (mm)
        elev_deg[N,M] elevation angle per link (deg)
        uvec[N,M,3]   unit LOS vector tag->anchor
    All on P.device, FP32."""
    d = anchors[None, :, :] - P[:, None, :]          # [N,M,3]
    rng = torch.linalg.vector_norm(d, dim=2)          # [N,M]
    rng_safe = torch.clamp(rng, min=eps)
    dz = torch.abs(d[:, :, 2])
    elev = torch.arcsin(torch.clamp(dz / rng_safe, -1.0, 1.0)) * DEG
    uvec = d / rng_safe[:, :, None]
    return rng, elev, uvec


def dop_from_uvec(uvec, mask=None, clock=True, clip=1e4, rank_guard=False):
    """Batched DOP.
      uvec[N,M,3] unit LOS vectors.
      mask[N,M] bool or None — which anchors are usable at each point.
      clock=True -> 4-unknown (x,y,z,clock) 4x4; else 3-unknown TOA 3x3.
    Returns dict of GDOP,HDOP,VDOP,PDOP [N] and n_used[N] (int).
    Points with <(4 if clock else 3) usable anchors or singular geometry -> NaN.
    """
    N, M, _ = uvec.shape
    dev = uvec.device
    ncol = 4 if clock else 3
    if mask is None:
        mask = torch.ones(N, M, dtype=torch.bool, device=dev)
    fmask = mask.float()                                   # [N,M]
    n_used = mask.sum(dim=1)                               # [N]

    # Build H rows; zero out unused anchors so they contribute nothing to H^T H.
    if clock:
        ones = torch.ones(N, M, 1, device=dev)
        H = torch.cat([uvec, ones], dim=2)                # [N,M,4]
    else:
        H = uvec                                          # [N,M,3]
    H = H * fmask[:, :, None]                              # zero unused rows

    HtH = torch.matmul(H.transpose(1, 2), H)              # [N,ncol,ncol]

    ok = n_used >= ncol
    # Regularise/guard singular systems: add tiny jitter only where ok, else NaN.
    eye = torch.eye(ncol, device=dev)[None].expand(N, ncol, ncol)
    HtH_j = HtH + eye * 1e-9

    cov = torch.full((N, ncol, ncol), float("nan"), device=dev)
    # rank_guard=False (default): legacy path — invert every ok point. Correct
    #   and byte-identical for geometries whose feasible sets are never exactly
    #   singular (e.g. cell_split_simulation, which always spans both rings).
    # rank_guard=True: a feasible set of >=ncol anchors can still be RANK-
    #   DEFICIENT (e.g. all feasible anchors coplanar -> no vertical/clock
    #   separability) and would make inv() throw. Such sets are physically
    #   UNSOLVABLE -> map to NaN (0 coverage). Rank test = batched Cholesky on
    #   the un-jittered normal matrix (info>0 <=> not positive-definite). Cheap,
    #   no large workspace. Needed once d_close gating can leave a coplanar-only
    #   feasible set (court_cube_cell_simulation).
    if ok.any():
        if rank_guard:
            _, info = torch.linalg.cholesky_ex(HtH)      # info>0 -> rank-deficient
            well = ok & (info == 0)
        else:
            well = ok
        if well.any():
            cov[well] = torch.linalg.inv(HtH_j[well])

    diag = torch.diagonal(cov, dim1=1, dim2=2)            # [N,ncol]
    # numerical negatives from near-singular -> nan
    diag = torch.where(diag < 0, torch.full_like(diag, float("nan")), diag)
    vdop = torch.sqrt(diag[:, 2])
    hdop = torch.sqrt(diag[:, 0] + diag[:, 1])
    pdop = torch.sqrt(diag[:, 0] + diag[:, 1] + diag[:, 2])
    gdop = torch.sqrt(diag.sum(dim=1))

    def _finish(x):
        x = torch.where(torch.isfinite(x), x, torch.full_like(x, float("nan")))
        x = torch.clamp(x, max=clip)
        x[~ok] = float("nan")
        return x

    return {
        "GDOP": _finish(gdop), "HDOP": _finish(hdop),
        "VDOP": _finish(vdop), "PDOP": _finish(pdop),
        "n_used": n_used,
    }


def band_fractions(max_elev_deg):
    """Given per-point max-elevation (deg), return volume fractions in the three
    Erlangen bands: <=25 (safe), 25-37 (unsampled risk), >=37 (Layer-2 danger)."""
    v = max_elev_deg[torch.isfinite(max_elev_deg)]
    n = v.numel()
    if n == 0:
        return dict(safe=float("nan"), risk=float("nan"), danger=float("nan"), n=0)
    safe = (v <= 25.0).float().mean().item()
    risk = ((v > 25.0) & (v < 37.0)).float().mean().item()
    danger = (v >= 37.0).float().mean().item()
    return dict(safe=safe, risk=risk, danger=danger, n=int(n))


def pct(x, q):
    v = x[torch.isfinite(x)]
    if v.numel() == 0:
        return float("nan")
    return torch.quantile(v, q).item()


def nanmedian(x):
    v = x[torch.isfinite(x)]
    if v.numel() == 0:
        return float("nan")
    return v.median().item()
