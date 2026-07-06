#!/usr/bin/env python3
"""Shared orthogonal-mode decomposition library (extracted from diag_erlangen_modes.py, DIAG 5d).

Pure functions only; no top-level side effects, no file I/O. Both diag_erlangen_modes.py
(§5d, single v4-io case) and diag_erlangen_ablation.py (§5e, arm ladder) import this so every
arm/report uses the IDENTICAL Procrustes / anisotropic-scale / shape-PCA / energy-budget math
and the identical det=-1 reflection handling.

decompose_vs_truth(P, Q, order) is the one entry point that matters: given 8x3 solved-layout
positions P and 8x3 Vicon truth Q (both in `order` anchor-label order), it returns every number
needed for a §5d-style report: s_iso, diagonal anisotropic tensor, shape-PCA modes (post-iso and
post-aniso), and an energy budget that closes to E0 exactly by construction (nested affine chain).
"""
import math
import numpy as np

ORD_DEFAULT = list("ABCDEFGH")
LOWER_DEFAULT, UPPER_DEFAULT = list("ABCD"), list("EFGH")


def umeyama(src, tgt, with_scale, allow_reflection=True):
    """map src->tgt: aligned = s*R@src + t, minimizing sum||aligned-tgt||^2."""
    n = src.shape[0]
    mu_s, mu_t = src.mean(0), tgt.mean(0)
    Sc, Tc = src - mu_s, tgt - mu_t
    C = (Sc.T @ Tc) / n
    U, D, Vt = np.linalg.svd(C)
    d = np.ones(3)
    if (not allow_reflection) and np.linalg.det(Vt.T @ U.T) < 0:
        d[-1] = -1.0
    R = Vt.T @ np.diag(d) @ U.T
    if with_scale:
        var = float(np.sum(Sc * Sc) / n)
        s = float(np.sum(D * d) / var)
    else:
        s = 1.0
    t = mu_t - s * (R @ mu_s)
    aligned = (s * (R @ src.T)).T + t
    return dict(s=s, R=R, t=t, aligned=aligned, det=float(np.linalg.det(R)))


def rms(res):    return float(math.sqrt(np.mean(np.sum(res * res, 1))))
def energy(res): return float(np.sum(res * res))


def field_svd(M):
    """SVD of an 8x3 displacement field: M = sum_k sigma_k * u_k (x) v_k."""
    U, S, Vt = np.linalg.svd(M, full_matrices=False)
    return S, U, Vt


def fro_cos(A, B):
    return float(np.sum(A * B) / (np.linalg.norm(A) * np.linalg.norm(B)))


def upper_lift_template(ref_pts, order=ORD_DEFAULT, lower=LOWER_DEFAULT, upper=UPPER_DEFAULT):
    """unit 8x3 field: each anchor displaced +n(upper)/-n(lower) along the layer normal n."""
    lo = ref_pts[[order.index(a) for a in lower]].mean(0)
    hi = ref_pts[[order.index(a) for a in upper]].mean(0)
    n = hi - lo; n /= np.linalg.norm(n)
    sgn = np.array([1.0 if a in upper else -1.0 for a in order])
    T = sgn[:, None] * n[None, :]
    return T / np.linalg.norm(T), n


def local_basis(ref_pts, order=ORD_DEFAULT, lower=LOWER_DEFAULT, upper=UPPER_DEFAULT):
    """room-independent (e1,e2,n) basis: n=lower->upper layer normal, e1=A->B in-plane, e2=n x e1."""
    lo = ref_pts[[order.index(a) for a in lower]].mean(0)
    hi = ref_pts[[order.index(a) for a in upper]].mean(0)
    n = hi - lo; n /= np.linalg.norm(n)
    ab = ref_pts[order.index('B')] - ref_pts[order.index('A')]
    e1 = ab - (ab @ n) * n; e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    return np.vstack([e1, e2, n])


def to_local(field, ref_pts, order=ORD_DEFAULT, lower=LOWER_DEFAULT, upper=UPPER_DEFAULT):
    B = local_basis(ref_pts, order, lower, upper)
    return field @ B.T


def decompose_vs_truth(P, Q, order=ORD_DEFAULT, lower=LOWER_DEFAULT, upper=UPPER_DEFAULT):
    """Core §5d decomposition: P (8x3 solved) vs Q (8x3 Vicon truth), same anchor-label order.

    Returns a dict with every number needed to report or tabulate one arm:
      s_iso, expansion_pct, rigid_rms/sim_rms, s_ax (diag tensor), s_h, aniso_ratio,
      E0/E1/E2, iso_E/aniso_E/shape_E, shape_modes (post-iso r1, 3 entries),
      post_aniso_modes (r2, 3 entries, this is what the energy budget closes on),
      per_anchor (rigid/post-iso/post-aniso residual vectors), r1, r2 (raw fields for
      further cross-checks), and rigid/similarity fit dicts (R, t, det) for provenance.
    """
    rigid = umeyama(P, Q, with_scale=False)
    simil = umeyama(P, Q, with_scale=True)
    res_rigid = rigid["aligned"] - Q
    res_sim = simil["aligned"] - Q
    s_iso = simil["s"]

    Ar = rigid["aligned"]
    c = Q.mean(0)
    a_t = Ar - c
    q_t = Q - c
    E0 = energy(a_t - q_t)

    s_fix = float(np.sum(a_t * q_t) / np.sum(a_t * a_t))
    r1 = s_fix * a_t - q_t
    E1 = energy(r1)

    s_ax = np.array([np.sum(a_t[:, k] * q_t[:, k]) / np.sum(a_t[:, k] ** 2) for k in range(3)])
    r2 = a_t * s_ax - q_t
    E2 = energy(r2)

    iso_E = E0 - E1
    aniso_E = E1 - E2
    shape_E = E2
    s_h = math.sqrt(s_ax[0] * s_ax[2])

    S1, U1, Vt1 = field_svd(r1)
    S2, U2, Vt2 = field_svd(r2)
    T_lift, nrm = upper_lift_template(Q, order, lower, upper)

    shape_modes = []
    for k in range(3):
        ek = S1[k] ** 2
        v = Vt1[k] * np.sign(Vt1[k][np.argmax(np.abs(Vt1[k]))])
        field_k = S1[k] * np.outer(U1[:, k], Vt1[k])
        u = U1[:, k] * np.sign(Vt1[k][np.argmax(np.abs(Vt1[k]))])
        shape_modes.append(dict(energy=float(ek), pct_of_shape=float(100 * ek / E1) if E1 > 0 else float('nan'),
                                 pct_of_total=float(100 * ek / E0), direction=v.tolist(),
                                 cos_upper_lift=fro_cos(field_k, T_lift), loadings=u.tolist(), field=field_k))

    post_aniso_modes = []
    for k in range(3):
        ek = S2[k] ** 2
        v = Vt2[k] * np.sign(Vt2[k][np.argmax(np.abs(Vt2[k]))])
        field_k = S2[k] * np.outer(U2[:, k], Vt2[k])
        u = U2[:, k] * np.sign(Vt2[k][np.argmax(np.abs(Vt2[k]))])
        post_aniso_modes.append(dict(energy=float(ek), pct_of_total=float(100 * ek / E0),
                                      direction=v.tolist(), cos_upper_lift=fro_cos(field_k, T_lift),
                                      loadings=u.tolist(), field=field_k))

    per_anchor = []
    for i, a in enumerate(order):
        per_anchor.append(dict(anchor=a, rigid=res_rigid[i].tolist(), post_iso=r1[i].tolist(),
                                post_aniso=r2[i].tolist(), rigid_norm=float(np.linalg.norm(res_rigid[i]))))

    return dict(
        order=list(order), rigid=rigid, similarity=simil,
        s_iso=float(s_iso), expansion_pct=float((1.0 / s_iso - 1.0) * 100.0),
        rigid_rms=rms(res_rigid), sim_rms=rms(res_sim),
        s_ax=s_ax.tolist(), s_h=float(s_h), aniso_ratio=float(s_ax[1] / s_h),
        E0=E0, E1=E1, E2=E2, iso_E=iso_E, aniso_E=aniso_E, shape_E=shape_E,
        scale_pct_of_total=float(100 * (iso_E + aniso_E) / E0), shape_pct_of_total=float(100 * shape_E / E0),
        shape_modes=shape_modes, post_aniso_modes=post_aniso_modes,
        per_anchor=per_anchor, r1=r1, r2=r2, layer_normal=nrm.tolist(),
        closure_residual=float(E0 - (iso_E + aniso_E + sum(m['energy'] for m in post_aniso_modes))),
    )


def flatcos(A, B):
    return float(np.sum(A * B) / (np.linalg.norm(A) * np.linalg.norm(B)))
