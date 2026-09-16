from dataclasses import replace
import numpy as np
import pytest
from biospur_fusion.c2_articulated_biomechanics.hinge_temporal import (
    CANONICAL_HINGES, COVARIANCE_STATUS, DEFAULT_RETENTION_CONTRACT,
    HingeTemporalEvidenceError, HingeTemporalRetentionContract,
    HingeTemporalValidity, _CausalHingeTemporalOwner,
    ObsoleteNative200SourcePair, ObsoleteNative200SourcePairDiagnostic,
    extract_public_hinge_q_rad,
)

def _projection(values=(10.,20.,30.,40.)):
    return {"joint":{name:{"post_projection_signed_deg":v} for name,v in zip(CANONICAL_HINGES,values)}}

def _prepare(owner, sequence, time_s, values, *, generation=0, revision=0, rom=True, fk=True):
    return owner._prepare_from_pose(
        pose_revision=revision, continuity_generation=generation,
        publication_sequence=sequence, time_s=time_s,
        correction_hash="a"*64, projection_hash="b"*64,
        projection=_projection(values), rom_valid=rom, fk_valid=fk,
    )

def _publish(owner, sequence, time_s, values, **kw):
    plan=_prepare(owner,sequence,time_s,values,**kw)
    return plan,owner._commit_from_pose(plan,pose_revision=kw.get("revision",0))

def _publish_source(owner, sequence, timer_us, values, *, generation=0, revision=0):
    plan=owner._prepare_from_pose(
        pose_revision=revision,continuity_generation=generation,
        publication_sequence=sequence,time_s=timer_us*1e-6,
        correction_hash="a"*64,projection_hash="b"*64,
        projection=_projection(values),rom_valid=True,fk_valid=True,
        source_node="pelvis",source_boot_epoch=7,source_timer_us=timer_us,
        source_global_ns=timer_us*1_000,source_clock_mapping_digest="c"*64)
    return plan,owner._commit_from_pose(plan,pose_revision=revision)


def test_obsolete_native200_pair_is_typed_exact_and_generic_cadence_stays_fatal():
    owner = _CausalHingeTemporalOwner()
    _publish_source(owner, 0, 30_000, (0,) * 4, generation=2)
    before = owner._state_bytes()
    with pytest.raises(ObsoleteNative200SourcePair) as caught:
        _publish_source(owner, 1, 30_000, (1,) * 4, generation=2)
    assert caught.value.diagnostic == ObsoleteNative200SourcePairDiagnostic(
        30_000, 30_000_000, 30_000, 30_000_000, 2,
    )
    assert str(caught.value) == "OBSOLETE_NATIVE200_SOURCE_PAIR"
    assert owner._state_bytes() == before

    with pytest.raises(HingeTemporalEvidenceError, match="SOURCE_CADENCE_INVALID") as generic:
        owner._prepare_from_pose(
            pose_revision=0, continuity_generation=2, publication_sequence=1,
            time_s=.031, correction_hash="a" * 64, projection_hash="b" * 64,
            projection=_projection((1,) * 4), rom_valid=True, fk_valid=True,
            source_node="pelvis", source_boot_epoch=7, source_timer_us=30_000,
            source_global_ns=31_000_000, source_clock_mapping_digest="c" * 64,
        )
    assert type(generic.value) is HingeTemporalEvidenceError
    assert owner._state_bytes() == before

    plan, _ = _publish_source(owner, 1, 35_000, (1,) * 4, generation=2)
    assert plan.source_timer_us == 35_000

def test_exact_order_readonly_and_nonuniform_derivatives():
    q=extract_public_hinge_q_rad(_projection()); np.testing.assert_allclose(q,np.radians((10,20,30,40)))
    with pytest.raises(ValueError): q[0]=0
    owner=_CausalHingeTemporalOwner()
    _,a=_publish(owner,0,0.,(0,0,0,0)); _,b=_publish(owner,1,.01,(1,2,3,4)); _,c=_publish(owner,2,.03,(4,8,12,16))
    assert a.validity is HingeTemporalValidity.WARMUP_QDOT_QDDOT
    assert b.validity is HingeTemporalValidity.WARMUP_QDDOT
    assert c.validity is HingeTemporalValidity.QUALIFIED
    q0,q1,q2=map(np.radians,((0,0,0,0),(1,2,3,4),(4,8,12,16)))
    v1=(q1-q0)/.01; v2=(q2-q1)/.02
    np.testing.assert_allclose(c.qdot_rad_s,v2); np.testing.assert_allclose(c.qddot_rad_s2,2*(v2-v1)/.03)
    assert c.covariance_status==COVARIANCE_STATUS and c.q_covariance_rad2 is None

def test_gap_reset_reversal_and_fixed_capacity():
    owner=_CausalHingeTemporalOwner(); _publish(owner,0,0.,(0,)*4)
    _,gap=_publish(owner,2,.01,(1,)*4); assert gap.validity is HingeTemporalValidity.RESET_SEQUENCE_GAP
    _,reset=_publish(owner,3,.02,(2,)*4,generation=1); assert reset.validity is HingeTemporalValidity.RESET_CONTINUITY_GENERATION
    before=owner._history_bytes()
    with pytest.raises(HingeTemporalEvidenceError): _prepare(owner,2,.03,(3,)*4,generation=1)
    assert owner._history_bytes()==before
    for i in range(4,100): _publish(owner,i,i*.01,(i,)*4,generation=1)
    assert len(owner._CausalHingeTemporalOwner__history)<=5


def test_explicit_retention_contract_covers_authorized_delay_then_fails_one_tick_beyond():
    contract = HingeTemporalRetentionContract(maximum_source_latency_ns=245_101_000)
    owner = _CausalHingeTemporalOwner(contract)
    for sequence, timer_us in enumerate(range(0, 260_001, 5_000)):
        _publish_source(owner, sequence, timer_us, (sequence,) * 4)
    token = owner._snapshot_token()
    owner._direct_candidate_from_snapshot(
        token, pose_revision=1, continuity_generation=0, source_node="pelvis",
        source_boot_epoch=7, previous_timer_us=255_000, current_timer_us=260_000,
        previous_global_ns=255_000_000, current_global_ns=260_000_000,
        source_clock_mapping_digest="c" * 64, correction_hash="d" * 64,
        projection_hash="e" * 64, projection=_projection((3, 3, 3, 3)),
        rom_valid=True, fk_valid=True,
    )
    assert len(owner._CausalHingeTemporalOwner__history) <= contract.capacity
    _publish_source(owner, 53, 265_000, (53,) * 4)
    with pytest.raises(ObsoleteNative200SourcePair):
        owner._direct_candidate_from_snapshot(
            owner._snapshot_token(), pose_revision=1, continuity_generation=0,
            source_node="pelvis", source_boot_epoch=7,
            previous_timer_us=10_000, current_timer_us=15_000,
            previous_global_ns=10_000_000, current_global_ns=15_000_000,
            source_clock_mapping_digest="c" * 64, correction_hash="d" * 64,
            projection_hash="e" * 64, projection=_projection((3, 3, 3, 3)),
            rom_valid=True, fk_valid=True,
        )


def test_default_retention_contract_preserves_immediate_path_bytes():
    implicit = _CausalHingeTemporalOwner()
    explicit = _CausalHingeTemporalOwner(DEFAULT_RETENTION_CONTRACT)
    for sequence, timer_us in enumerate((30_000, 35_000, 40_000, 45_000, 50_000)):
        _publish_source(implicit, sequence, timer_us, (sequence,) * 4)
        _publish_source(explicit, sequence, timer_us, (sequence,) * 4)
    assert implicit._state_bytes() == explicit._state_bytes()
    assert implicit._snapshot_token().digest == explicit._snapshot_token().digest


def test_retention_contract_does_not_make_missing_gapped_or_foreign_history_valid():
    contract = HingeTemporalRetentionContract(maximum_source_latency_ns=245_101_000)
    owner = _CausalHingeTemporalOwner(contract)
    _publish_source(owner, 0, 30_000, (0,) * 4)
    _publish_source(owner, 2, 40_000, (2,) * 4)
    with pytest.raises(HingeTemporalEvidenceError, match="DIRECT_WARMUP"):
        owner._direct_candidate_from_snapshot(
            owner._snapshot_token(), pose_revision=1, continuity_generation=0,
            source_node="pelvis", source_boot_epoch=7,
            previous_timer_us=35_000, current_timer_us=40_000,
            previous_global_ns=35_000_000, current_global_ns=40_000_000,
            source_clock_mapping_digest="c" * 64, correction_hash="d" * 64,
            projection_hash="e" * 64, projection=_projection((2, 2, 2, 2)),
            rom_valid=True, fk_valid=True,
        )
    foreign = _CausalHingeTemporalOwner(contract)
    for sequence, timer_us in enumerate((30_000, 35_000, 40_000)):
        _publish_source(foreign, sequence, timer_us, (sequence,) * 4)
    with pytest.raises(HingeTemporalEvidenceError, match="SOURCE_GENERATION_MISMATCH"):
        foreign._direct_candidate_from_snapshot(
            foreign._snapshot_token(),
            pose_revision=1, continuity_generation=0, source_node="foreign",
            source_boot_epoch=99, previous_timer_us=35_000,
            current_timer_us=40_000, previous_global_ns=35_000_000,
            current_global_ns=40_000_000, source_clock_mapping_digest="f" * 64,
            correction_hash="d" * 64, projection_hash="e" * 64,
            projection=_projection(), rom_valid=True, fk_valid=True,
        )

def test_source_tick_history_retains_exact_delayed_candidate_rows_and_rebases():
    owner=_CausalHingeTemporalOwner()
    for sequence,timer in enumerate((30_000,35_000,40_000,45_000,50_000)):
        _publish_source(owner,sequence,timer,(sequence,)*4)
    token=owner._snapshot_token()
    candidate=owner._direct_candidate_from_snapshot(
        token,pose_revision=1,continuity_generation=0,source_node="pelvis",
        source_boot_epoch=7,previous_timer_us=45_000,current_timer_us=50_000,
        previous_global_ns=45_000_000,current_global_ns=50_000_000,
        source_clock_mapping_digest="c"*64,correction_hash="d"*64,
        projection_hash="e"*64,projection=_projection((4,4,4,4)),
        rom_valid=True,fk_valid=True)
    np.testing.assert_allclose(candidate.qdot_rad_s,np.radians(np.full(4,200.)))
    rebase=owner._prepare_rebase_from_candidate(
        token,pose_revision=1,continuity_generation=1,source_node="pelvis",
        source_boot_epoch=7,source_timer_us=50_000,source_global_ns=50_000_000,
        source_clock_mapping_digest="c"*64,correction_hash="d"*64,
        projection_hash="e"*64,q_rad=candidate.q_rad,rom_valid=True,fk_valid=True)
    before=owner._state_bytes(); rollback=owner._prepare_rebase_rollback()
    owner._apply_prevalidated_rebase(owner._prevalidate_rebase(rebase))
    assert len(owner._CausalHingeTemporalOwner__history)==1
    row=owner._CausalHingeTemporalOwner__history[0]
    assert row.source_timer_us==50_000 and row.continuity_generation==1
    assert row.qdot_rad_s is None and row.qddot_rad_s2 is None
    owner._rollback_prevalidated_rebase(rollback)
    assert owner._state_bytes()==before


def test_direct_candidate_rejects_real_superseded_pair_and_accepts_exact_latest():
    owner = _CausalHingeTemporalOwner()
    for sequence, timer in enumerate((30_000, 35_000, 40_000, 45_000, 50_000)):
        _publish_source(owner, sequence, timer, (sequence,) * 4)
    token = owner._snapshot_token()
    before = owner._state_bytes()
    common = dict(
        token=token, pose_revision=1, continuity_generation=0,
        source_node="pelvis", source_boot_epoch=7,
        source_clock_mapping_digest="c" * 64, correction_hash="d" * 64,
        projection_hash="e" * 64, projection=_projection((5,) * 4),
        rom_valid=True, fk_valid=True,
    )
    with pytest.raises(ObsoleteNative200SourcePair) as caught:
        owner._direct_candidate_from_snapshot(
            previous_timer_us=40_000, current_timer_us=45_000,
            previous_global_ns=40_000_000, current_global_ns=45_000_000,
            **common,
        )
    assert caught.value.diagnostic == ObsoleteNative200SourcePairDiagnostic(
        45_000, 45_000_000, 50_000, 50_000_000, 0,
    )
    assert owner._state_bytes() == before
    accepted = owner._direct_candidate_from_snapshot(
        previous_timer_us=45_000, current_timer_us=50_000,
        previous_global_ns=45_000_000, current_global_ns=50_000_000,
        **common,
    )
    assert accepted.qualified
    assert owner._state_bytes() == before

@pytest.mark.parametrize("field,value",[
    ("qdot_rad_s",np.ones(4)),("qddot_rad_s2",np.ones(4)),("provenance","forged"),
    ("covariance_status","AVAILABLE"),("q_covariance_rad2",1),
    ("rom_valid",1),("fk_valid",1),("projection_hash","BAD"),
    ("pose_revision",9),("base_owner_revision",9),
])
def test_every_prepared_field_tamper_rejects_and_preserves_history(field,value):
    owner=_CausalHingeTemporalOwner(); plan=_prepare(owner,0,0.,(0,)*4); before=owner._history_bytes()
    with pytest.raises(HingeTemporalEvidenceError): owner._commit_from_pose(replace(plan,**{field:value}),pose_revision=0)
    assert owner._history_bytes()==before

def test_foreign_authority_stale_and_idempotent():
    first=_CausalHingeTemporalOwner(); second=_CausalHingeTemporalOwner(); plan=_prepare(first,0,0.,(0,)*4)
    with pytest.raises(HingeTemporalEvidenceError): second._commit_from_pose(plan,pose_revision=0)
    evidence=first._commit_from_pose(plan,pose_revision=0); again=first._commit_from_pose(plan,pose_revision=0)
    assert evidence.q_rad.tobytes()==again.q_rad.tobytes()
    stale=_prepare(first,1,.01,(1,)*4)
    other=_prepare(first,1,.01,(2,)*4); first._commit_from_pose(other,pose_revision=0)
    with pytest.raises(HingeTemporalEvidenceError): first._commit_from_pose(stale,pose_revision=0)

def test_exact_repeat_prepare_is_complete_idempotent_and_conflicts_are_stable():
    owner=_CausalHingeTemporalOwner(); plan,evidence=_publish(owner,0,0.,(1,2,3,4))
    before=owner._history_bytes(); retained=len(owner._CausalHingeTemporalOwner__history)
    repeated=_prepare(owner,0,0.,(1,2,3,4)); repeated_evidence=owner._commit_from_pose(repeated,pose_revision=0)
    assert repeated is plan
    assert repeated_evidence.time_s==evidence.time_s
    assert repeated_evidence.validity is evidence.validity
    assert repeated_evidence.provenance==evidence.provenance
    assert repeated_evidence.covariance_status==evidence.covariance_status
    for name in ("q_rad","step_rad","qdot_rad_s","qddot_rad_s2"):
        left=getattr(repeated_evidence,name); right=getattr(evidence,name)
        assert (left is None and right is None) or left.tobytes()==right.tobytes()
    assert owner._history_bytes()==before
    assert len(owner._CausalHingeTemporalOwner__history)==retained
    conflicts=(
        {"correction_hash":"c"*64}, {"projection_hash":"d"*64},
        {"pose_revision":1},
    )
    for changes in conflicts:
        with pytest.raises(HingeTemporalEvidenceError):
            owner._prepare_from_pose(
                pose_revision=changes.get("pose_revision",0),continuity_generation=0,
                publication_sequence=0,time_s=0.,
                correction_hash=changes.get("correction_hash","a"*64),
                projection_hash=changes.get("projection_hash","b"*64),
                projection=_projection((1,2,3,4)),rom_valid=True,fk_valid=True,
            )
        assert owner._history_bytes()==before
    with pytest.raises(HingeTemporalEvidenceError): _prepare(owner,0,0.,(9,2,3,4))
    assert owner._history_bytes()==before
    with pytest.raises(HingeTemporalEvidenceError):
        owner._commit_from_pose(evidence,pose_revision=0)

def test_identical_plan_checks_pose_and_owner_revision_before_idempotence():
    owner=_CausalHingeTemporalOwner(); plan,evidence=_publish(owner,0,0.,(1,2,3,4))
    stable=(owner._history_bytes(),owner._CausalHingeTemporalOwner__revision,
            len(owner._CausalHingeTemporalOwner__history),owner._CausalHingeTemporalOwner__last_digest)
    with pytest.raises(HingeTemporalEvidenceError):
        owner._commit_from_pose(plan,pose_revision=1)
    assert (owner._history_bytes(),owner._CausalHingeTemporalOwner__revision,
            len(owner._CausalHingeTemporalOwner__history),owner._CausalHingeTemporalOwner__last_digest)==stable
    repeated=owner._commit_from_pose(plan,pose_revision=0)
    assert repeated.time_s==evidence.time_s and repeated.validity is evidence.validity
    for name in ("q_rad","step_rad","qdot_rad_s","qddot_rad_s2"):
        left=getattr(repeated,name); right=getattr(evidence,name)
        assert (left is None and right is None) or left.tobytes()==right.tobytes()
    assert (owner._history_bytes(),owner._CausalHingeTemporalOwner__revision,
            len(owner._CausalHingeTemporalOwner__history),owner._CausalHingeTemporalOwner__last_digest)==stable
    later=_prepare(owner,1,.01,(2,3,4,5)); owner._commit_from_pose(later,pose_revision=0)
    advanced=(owner._history_bytes(),owner._CausalHingeTemporalOwner__revision,
              len(owner._CausalHingeTemporalOwner__history),owner._CausalHingeTemporalOwner__last_digest)
    with pytest.raises(HingeTemporalEvidenceError):
        owner._commit_from_pose(plan,pose_revision=0)
    assert (owner._history_bytes(),owner._CausalHingeTemporalOwner__revision,
            len(owner._CausalHingeTemporalOwner__history),owner._CausalHingeTemporalOwner__last_digest)==advanced

def test_readonly_snapshot_token_tracks_exact_temporal_revision():
    owner=_CausalHingeTemporalOwner(); token=owner._snapshot_token()
    owner._validate_snapshot_token(token)
    _publish(owner,0,0.,(0,)*4)
    with pytest.raises(HingeTemporalEvidenceError,match="STALE"):
        owner._validate_snapshot_token(token)
    current=owner._snapshot_token(); owner._validate_snapshot_token(current)
    with pytest.raises(ValueError): current.evidence.q_rad[0]=1
    forged_evidence=replace(current.evidence,q_rad=np.ones(4))
    with pytest.raises(HingeTemporalEvidenceError,match="STALE"):
        owner._validate_snapshot_token(replace(current,evidence=forged_evidence))

@pytest.mark.parametrize("rom,fk",[(False,True),(True,False)])
def test_rom_fk_and_malformed_inventory_reject(rom,fk):
    owner=_CausalHingeTemporalOwner()
    with pytest.raises(HingeTemporalEvidenceError): _prepare(owner,0,0.,(0,)*4,rom=rom,fk=fk)
    bad=_projection(); bad["joint"].pop("knee_right")
    with pytest.raises(HingeTemporalEvidenceError): extract_public_hinge_q_rad(bad)
