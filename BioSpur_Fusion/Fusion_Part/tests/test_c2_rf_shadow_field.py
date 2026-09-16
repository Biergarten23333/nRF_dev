from __future__ import annotations

from dataclasses import replace
import inspect
import itertools

import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.rf_shadow_field import (
    ARM_SEGMENTS,
    EMITTER_LOCAL_SEGMENTS,
    FORBIDDEN_MODEL_LANDMARK_TOKENS,
    LEG_SEGMENTS,
    MORPHOLOGY_BOUNDS,
    MORPHOLOGY_PARAMETER_NAMES,
    NUISANCE_NAMES,
    REQUIRED_LANDMARKS,
    ShadowMorphology,
    compile_shadow_geometry,
    common_nuisance_vector,
    nested_design_vectors,
    nested_shadow_shift_m,
    shadow_features,
    shadow_features_from_compiled,
)


def _skeleton() -> dict[str, np.ndarray]:
    return {
        "pelvis_center": np.array([0.0, 0.0, 0.0]),
        "shoulder_mid": np.array([0.0, 0.0, 0.62]),
        "shoulder_left": np.array([-0.22, 0.0, 0.60]),
        "shoulder_right": np.array([0.22, 0.0, 0.60]),
        "hip_left": np.array([-0.12, 0.0, 0.0]),
        "hip_right": np.array([0.12, 0.0, 0.0]),
        "elbow_left": np.array([-0.42, 0.04, 0.34]),
        "elbow_right": np.array([0.42, 0.04, 0.34]),
        "wrist_left": np.array([-0.58, 0.08, 0.08]),
        "wrist_right": np.array([0.58, 0.08, 0.08]),
        "knee_left": np.array([-0.13, 0.03, -0.46]),
        "knee_right": np.array([0.13, 0.03, -0.46]),
        "ankle_left": np.array([-0.14, 0.01, -0.91]),
        "ankle_right": np.array([0.14, 0.01, -0.91]),
    }


def _morphology() -> ShadowMorphology:
    return ShadowMorphology(0.50, 0.40, 0.25, 0.16, 0.13, 0.12, 0.14)


def _feature(
    *,
    tag: np.ndarray | None = None,
    anchor: np.ndarray | None = None,
    landmarks: dict[str, np.ndarray] | None = None,
    node: str = "BSFEC35",
    morphology: ShadowMorphology | None = None,
):
    return shadow_features(
        tag_origin_m=np.array([0.0, -2.0, 0.30]) if tag is None else tag,
        anchor_position_m=np.array([0.0, 2.0, 0.30]) if anchor is None else anchor,
        landmarks=_skeleton() if landmarks is None else landmarks,
        emitter_node=node,
        morphology=_morphology() if morphology is None else morphology,
    )


def test_rigid_transform_invariance() -> None:
    before = _feature()
    angle = 0.73
    rotation = np.array([
        [np.cos(angle), -np.sin(angle), 0.0],
        [np.sin(angle), np.cos(angle), 0.0],
        [0.0, 0.0, 1.0],
    ])
    translation = np.array([1.3, -0.7, 0.8])
    skeleton = {
        key: rotation @ value + translation for key, value in _skeleton().items()
    }
    after = _feature(
        tag=rotation @ np.array([0.0, -2.0, 0.30]) + translation,
        anchor=rotation @ np.array([0.0, 2.0, 0.30]) + translation,
        landmarks=skeleton,
    )
    assert np.allclose(
        [before.torso_exposure, before.arm_exposure, before.leg_exposure],
        [after.torso_exposure, after.arm_exposure, after.leg_exposure],
        rtol=0.0,
        atol=2e-15,
    )


def test_side_permutation_symmetry() -> None:
    before = _feature(node="BSFEC35")
    skeleton = _skeleton()
    mirrored: dict[str, np.ndarray] = {}
    for key, value in skeleton.items():
        swapped = key.replace("left", "TEMP").replace("right", "left").replace(
            "TEMP", "right"
        )
        copied = value.copy()
        copied[0] *= -1.0
        mirrored[swapped] = copied
    after = _feature(node="BSFB165", landmarks=mirrored)
    assert np.allclose(
        [before.torso_exposure, before.arm_exposure, before.leg_exposure],
        [after.torso_exposure, after.arm_exposure, after.leg_exposure],
        rtol=0.0,
        atol=2e-15,
    )


def test_center_edge_far_decay_is_smooth() -> None:
    center = _feature()
    edge = _feature(
        tag=np.array([0.22, -2.0, 0.30]),
        anchor=np.array([0.22, 2.0, 0.30]),
    )
    far = _feature(
        tag=np.array([1.20, -2.0, 0.30]),
        anchor=np.array([1.20, 2.0, 0.30]),
    )
    assert center.torso_exposure > edge.torso_exposure > far.torso_exposure > 0.0


def test_large_small_fields_are_separate_and_local_segment_is_excluded() -> None:
    before = _feature(node="BSFAA61")
    moved = _skeleton()
    moved["shoulder_left"] = np.array([-1.2, 0.9, 0.6])
    after = _feature(node="BSFAA61", landmarks=moved)
    # The local upper arm is excluded from the arm union. Its shoulder may still
    # alter the explicitly separate torso frame, which does not leak into small.
    assert np.isclose(before.arm_exposure, after.arm_exposure, atol=1e-15)
    assert before.small_field_local_segment_excluded
    torso_local = _feature(node="BSFC2CC")
    assert torso_local.large_field_unobservable_local
    assert torso_local.torso_exposure == 0.0


def test_strict_model_nesting_and_zero_opacity_equalities() -> None:
    features = _feature()
    common = common_nuisance_vector(
        node_index=4,
        anchor_index=3,
        own_facing_score=0.2,
        predicted_path_length_m=4.2,
        quality=81.0,
        t_round_us=7200.0,
    )
    zero_large = replace(_morphology(), torso_opacity_m=0.0)
    shifts = nested_shadow_shift_m(features, zero_large)
    assert shifts["B0"] == shifts["B1"] == 0.0
    zero_small = replace(_morphology(), arm_opacity_m=0.0, leg_opacity_m=0.0)
    shifts = nested_shadow_shift_m(features, zero_small)
    assert shifts["B1"] == shifts["B2"]
    design = nested_design_vectors(common, features, _morphology())
    assert np.array_equal(design["B0"], design["B1"][: len(common)])
    assert np.array_equal(design["B0"], design["B2"][: len(common)])


def test_fixed_segment_order_and_repeat_are_deterministic() -> None:
    first = _feature()
    for _ in range(10):
        assert _feature() == first
    assert tuple(name for name, _, _ in ARM_SEGMENTS) == (
        "upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right"
    )
    assert tuple(name for name, _, _ in LEG_SEGMENTS) == (
        "thigh_left", "shank_left", "thigh_right", "shank_right"
    )


def test_api_cannot_consume_current_range_and_forbidden_landmarks_absent() -> None:
    parameters = inspect.signature(shadow_features).parameters
    assert "range" not in " ".join(parameters).lower()
    names = " ".join(REQUIRED_LANDMARKS).lower()
    assert not any(token in names for token in FORBIDDEN_MODEL_LANDMARK_TOKENS)
    assert len(EMITTER_LOCAL_SEGMENTS) == 10
    assert set(item for values in EMITTER_LOCAL_SEGMENTS.values() for item in values) == {
        "torso", "upper_arm_left", "upper_arm_right", "forearm_left",
        "forearm_right", "thigh_left", "thigh_right", "shank_left", "shank_right",
    }


@pytest.mark.parametrize("name", MORPHOLOGY_PARAMETER_NAMES)
def test_parameters_have_finite_bounds_and_fail_closed(name: str) -> None:
    lower, upper = MORPHOLOGY_BOUNDS[name]
    assert np.isfinite(lower) and np.isfinite(upper) and upper > lower
    if "scale" in name:
        assert lower > 0.0
    values = _morphology().__dict__
    with pytest.raises(ValueError):
        ShadowMorphology(**{**values, name: np.nan})
    with pytest.raises(ValueError):
        ShadowMorphology(**{**values, name: upper + 1.0})


def test_malformed_geometry_and_degenerate_segments_fail_closed() -> None:
    with pytest.raises(ValueError):
        _feature(anchor=np.array([0.0, -2.0, 0.30]))
    missing = _skeleton()
    del missing["elbow_left"]
    with pytest.raises(ValueError):
        _feature(landmarks=missing)
    degenerate = _skeleton()
    degenerate["elbow_right"] = degenerate["shoulder_right"].copy()
    with pytest.raises(ValueError):
        _feature(landmarks=degenerate)


def test_nuisance_sumzero_basis_is_identifiable() -> None:
    rng = np.random.default_rng(20260905)
    rows = []
    for node, anchor in itertools.product(range(10), range(8)):
        rows.append(common_nuisance_vector(
            node_index=node,
            anchor_index=anchor,
            own_facing_score=float(rng.uniform(-1.0, 1.0)),
            predicted_path_length_m=float(rng.uniform(1.0, 9.0)),
            quality=float(rng.uniform(20.0, 100.0)),
            t_round_us=float(rng.uniform(2500.0, 12000.0)),
        ))
    matrix = np.vstack(rows)
    assert matrix.shape[1] == len(NUISANCE_NAMES) == 21
    assert np.linalg.matrix_rank(matrix) == matrix.shape[1]


def test_morphology_finite_difference_jacobian_is_finite_and_rank_seven() -> None:
    base = _morphology()
    rng = np.random.default_rng(20260905)
    samples = []
    for index in range(28):
        skeleton = _skeleton()
        for key, point in skeleton.items():
            skeleton[key] = point + rng.normal(0.0, 0.035, 3)
        tag = np.array([rng.uniform(-1.2, 1.2), -2.0, rng.uniform(-0.7, 0.7)])
        anchor = np.array([rng.uniform(-1.2, 1.2), 2.0, rng.uniform(-0.7, 0.7)])
        node = tuple(EMITTER_LOCAL_SEGMENTS)[index % 10]
        compiled = compile_shadow_geometry(
            tag_origin_m=tag, anchor_position_m=anchor,
            landmarks=skeleton, emitter_node=node,
        )
        samples.append(compiled)

    def response(morphology: ShadowMorphology) -> np.ndarray:
        output = []
        for compiled in samples:
            feature = shadow_features_from_compiled(compiled, morphology)
            output.append(nested_shadow_shift_m(feature, morphology)["B2"])
        return np.asarray(output)

    columns = []
    for name in MORPHOLOGY_PARAMETER_NAMES:
        value = getattr(base, name)
        step = 1e-5 * max(1.0, abs(value))
        plus = replace(base, **{name: value + step})
        minus = replace(base, **{name: value - step})
        columns.append((response(plus) - response(minus)) / (2.0 * step))
    jacobian = np.column_stack(columns)
    assert np.all(np.isfinite(jacobian))
    assert np.linalg.matrix_rank(jacobian, tol=1e-8) == 7


def test_all_emitters_exclude_every_incident_segment() -> None:
    expected = {
        "BSF31CC": ("torso",), "BSFC2CC": ("torso",),
        "BSFAA61": ("upper_arm_left", "forearm_left"),
        "BSF1120": ("upper_arm_right", "forearm_right"),
        "BSFEC35": ("forearm_left",), "BSFB165": ("forearm_right",),
        "BSF44AD": ("thigh_left", "shank_left"),
        "BSF3C79": ("thigh_right", "shank_right"),
        "BSF6C53": ("shank_left",), "BSF8BC4": ("shank_right",),
    }
    assert EMITTER_LOCAL_SEGMENTS == expected
    perturb_landmark = {
        "BSFAA61": "elbow_left", "BSF1120": "elbow_right",
        "BSFEC35": "wrist_left", "BSFB165": "wrist_right",
        "BSF44AD": "knee_left", "BSF3C79": "knee_right",
        "BSF6C53": "ankle_left", "BSF8BC4": "ankle_right",
    }
    for node, landmark in perturb_landmark.items():
        before = _feature(node=node)
        moved = _skeleton()
        moved[landmark] = moved[landmark] + np.array([0.0, 0.8, 0.0])
        after = _feature(node=node, landmarks=moved)
        if "arm" in " ".join(expected[node]) or "forearm" in expected[node][0]:
            assert np.isclose(before.arm_exposure, after.arm_exposure, atol=2e-10)
        else:
            assert np.isclose(before.leg_exposure, after.leg_exposure, atol=2e-10)
    for node in ("BSF31CC", "BSFC2CC"):
        assert _feature(node=node).torso_exposure == 0.0


def test_adaptive_integration_agrees_with_frozen_dense_reference_grid() -> None:
    # Independent high-order composite Legendre reference for representative
    # centimetric fields, grazing offsets, endpoints, and 1--10 m rays.
    from biospur_fusion.c2_uwb_calibration import rf_shadow_field as field

    x, w = np.polynomial.legendre.leggauss(64)
    skeleton = _skeleton()
    cases = [
        (1.0, 0.0, 0.01), (10.0, 0.0, 0.01),
        (10.0, 0.18, 0.01), (10.0, 0.0, 0.75),
        (10.0, -0.58, 0.01),
    ]
    for ray_length, x_offset, scale in cases:
        morphology = replace(
            _morphology(), arm_transverse_scale=scale, leg_transverse_scale=scale
        )
        tag = np.array([x_offset, -0.5 * ray_length, 0.34])
        anchor = np.array([x_offset, 0.5 * ray_length, 0.34])
        result = _feature(tag=tag, anchor=anchor, morphology=morphology)
        panels = 256
        fractions = []
        weights = []
        for panel in range(panels):
            lo, hi = panel / panels, (panel + 1) / panels
            fractions.append(0.5 * (lo + hi) + 0.5 * (hi - lo) * x)
            weights.append(0.5 * (hi - lo) * w)
        fraction = np.concatenate(fractions)
        weight = np.concatenate(weights)
        points = tag[None, :] + fraction[:, None] * (anchor - tag)[None, :]
        arms = [
            field._segment_field(
                points, skeleton[start], skeleton[stop], scale
            )
            for segment, start, stop in ARM_SEGMENTS
            if segment != "forearm_left"
        ]
        reference = float(weight @ field._bounded_union(arms, len(points)))
        assert np.isclose(result.arm_exposure, reference, atol=2e-8, rtol=2e-6)


def test_nonadditive_component_partition_is_bounded_and_zero_increment_nests() -> None:
    features = _feature()
    assert np.isclose(
        features.torso_exposure
        + features.arm_incremental_exposure
        + features.leg_incremental_exposure,
        features.total_occupancy_exposure,
        atol=2e-8,
    )
    assert features.total_occupancy_exposure <= 1.0
    maximum = replace(
        _morphology(), torso_opacity_m=2.0, arm_opacity_m=2.0, leg_opacity_m=2.0
    )
    assert nested_shadow_shift_m(features, maximum)["B2"] <= 2.0 + 2e-8
    no_small = replace(maximum, arm_opacity_m=0.0, leg_opacity_m=0.0)
    shifts = nested_shadow_shift_m(features, no_small)
    assert shifts["B2"] == shifts["B1"]


def test_joint_nuisance_and_morphology_synthetic_information_is_full_rank() -> None:
    rng = np.random.default_rng(20260905)
    morphology = _morphology()
    rows = []
    for index, (node_index, anchor_index) in enumerate(itertools.product(range(10), range(8))):
        skeleton = _skeleton()
        for key in skeleton:
            skeleton[key] = skeleton[key] + rng.normal(0.0, 0.06, 3)
        tag = np.array([rng.uniform(-1.5, 1.5), -2.0, rng.uniform(-0.8, 0.8)])
        anchor = np.array([rng.uniform(-1.5, 1.5), 2.0, rng.uniform(-0.8, 0.8)])
        nuisance = common_nuisance_vector(
            node_index=node_index,
            anchor_index=anchor_index,
            own_facing_score=float(rng.uniform(-1, 1)),
            predicted_path_length_m=float(rng.uniform(1, 9)),
            quality=float(rng.uniform(20, 100)),
            t_round_us=float(rng.uniform(2500, 12000)),
        )
        node = tuple(EMITTER_LOCAL_SEGMENTS)[node_index]
        compiled = compile_shadow_geometry(
            tag_origin_m=tag, anchor_position_m=anchor,
            landmarks=skeleton, emitter_node=node,
        )

        def response(candidate: ShadowMorphology) -> float:
            feat = shadow_features_from_compiled(compiled, candidate)
            return nested_shadow_shift_m(feat, candidate)["B2"]

        gradient = []
        for name in MORPHOLOGY_PARAMETER_NAMES:
            value = getattr(morphology, name)
            step = 1e-5 * max(1.0, abs(value))
            gradient.append((
                response(replace(morphology, **{name: value + step}))
                - response(replace(morphology, **{name: value - step}))
            ) / (2.0 * step))
        rows.append(np.r_[nuisance, gradient])
    design = np.vstack(rows)
    rms = np.sqrt(np.mean(design * design, axis=0))
    normalized = design / rms
    information = normalized.T @ normalized
    assert np.linalg.matrix_rank(information, tol=1e-8) == 28
    assert np.linalg.cond(information) < 1e8
