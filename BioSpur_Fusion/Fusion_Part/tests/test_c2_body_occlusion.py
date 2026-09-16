from __future__ import annotations

import numpy as np

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_uwb_calibration.body_occlusion import (
    BodyOcclusionEvidence,
    OnlineLinkReliability,
    body_occlusion_evidence,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    SEGMENTS,
    corrected_proxy_points,
)


def _geometry() -> DisplayProxyGeometry:
    return DisplayProxyGeometry(
        torso_height_m=0.50,
        hip_span_m=0.22,
        shoulder_span_m=0.38,
        segment_length_m={
            "upper_arm_left": 0.30,
            "forearm_left": 0.25,
            "upper_arm_right": 0.30,
            "forearm_right": 0.25,
            "thigh_left": 0.43,
            "shank_left": 0.40,
            "thigh_right": 0.43,
            "shank_right": 0.40,
        },
    )


def _points() -> dict[str, np.ndarray]:
    return corrected_proxy_points(
        {segment: np.eye(3) for segment in SEGMENTS},
        {segment: np.zeros(3) for segment in SEGMENTS},
        _geometry(),
    )


def test_central_torso_crossing_scores_above_grazing_path() -> None:
    points = _points()
    tag = np.array([-0.8, 0.0, 0.25])
    central = body_occlusion_evidence(
        node="BSFAA61",
        tag_position_world_m=tag,
        anchor_position_world_m=np.array([0.8, 0.0, 0.25]),
        points_world_m=points,
        geometry=_geometry(),
    )
    grazing = body_occlusion_evidence(
        node="BSFAA61",
        tag_position_world_m=tag,
        anchor_position_world_m=np.array([0.8, 0.8, 0.25]),
        points_world_m=points,
        geometry=_geometry(),
    )
    assert central.torso_score > 0.8
    assert central.score > grazing.score
    assert central.dominant_segment in {"torso", "upper_arm_right"}


def test_local_segment_is_not_mislabeled_as_other_body_occlusion() -> None:
    points = _points()
    tag = points["wrist_left"]
    along_forearm = body_occlusion_evidence(
        node="BSFEC35",
        tag_position_world_m=tag,
        anchor_position_world_m=tag + np.array([0.0, 0.0, 1.0]),
        points_world_m=points,
        geometry=_geometry(),
    )
    assert along_forearm.dominant_segment != "forearm_left"


def test_geometry_alone_never_downweights_a_nominal_range() -> None:
    tracker = OnlineLinkReliability()
    body = BodyOcclusionEvidence(0.95, 0.95, 0.0, "torso", 0.0)
    decision = tracker.assess(
        node="BSFAA61",
        anchor=0,
        innovation_m=0.05,
        sigma_m=0.08,
        facing_score=-1.0,
        body=body,
    )
    assert decision.information_weight == 1.0
    assert decision.reason == "NOMINAL_OR_NONPOSITIVE_INNOVATION"


def test_body_supported_positive_innovation_is_softly_downweighted() -> None:
    tracker = OnlineLinkReliability()
    clear = BodyOcclusionEvidence(0.0, 0.0, 0.0, None, 10.0)
    blocked = BodyOcclusionEvidence(0.95, 0.95, 0.0, "torso", 0.0)
    clear_decision = tracker.assess(
        node="BSFAA61", anchor=0, innovation_m=0.48, sigma_m=0.08,
        facing_score=1.0, body=clear,
    )
    blocked_decision = tracker.assess(
        node="BSFAA61", anchor=1, innovation_m=0.48, sigma_m=0.08,
        facing_score=1.0, body=blocked,
    )
    assert 0.05 <= blocked_decision.information_weight < clear_decision.information_weight
    assert blocked_decision.information_weight > 0.0


def test_link_history_is_committed_after_assessment_and_bounded() -> None:
    tracker = OnlineLinkReliability()
    body = BodyOcclusionEvidence(0.0, 0.0, 0.0, None, 10.0)
    first = tracker.assess(
        node="BSFAA61", anchor=0, innovation_m=0.48, sigma_m=0.08,
        facing_score=1.0, body=body,
    )
    assert first.prior_history_probability == 0.0
    tracker.observe(first, postfit_innovation_m=0.48, sigma_m=0.08)
    second = tracker.assess(
        node="BSFAA61", anchor=0, innovation_m=0.48, sigma_m=0.08,
        facing_score=1.0, body=body,
    )
    assert second.prior_history_probability > 0.0
    assert second.information_weight < first.information_weight
    assert tracker.state_size == 1
