"""Hard back deletion must affect both root factors and nuisance evidence."""
from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.antenna_los import hard_back_facing_mask
from biospur_fusion.c2_uwb_root_world.tight_range import (
    PersistentRangeBiasTracker, prepare_raw_range_update, update_raw_ranges,
)
from test_c2_continuous_full_state_feedback import ANCHORS, CLOCK, row_at
from biospur_fusion.root_r3.models import RootState


def test_hard_mask_drops_negative_and_keeps_exact_zero():
    scores=np.array([-1,-.1,0,.1,1,-.5,.5,0])
    assert hard_back_facing_mask(255,scores)==0b11011100
    assert hard_back_facing_mask(0,scores)==0
    assert hard_back_facing_mask(0b00000101,scores)==0b00000100


@pytest.mark.parametrize('mask',[0,1,3,7])
def test_zero_or_few_links_reject_without_recovery_or_bias_learning(mask):
    p=np.array([2.,1.3,1.])
    state=RootState(1.,np.r_[p,np.zeros(6)],np.eye(9))
    row=replace(row_at(1.,p),valid_mask=mask)
    updated,decision=update_raw_ranges(state,row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=1.)
    assert not decision.accepted and decision.reason=='FEWER_THAN_FOUR_LINKS'
    assert updated is state
    tracker=PersistentRangeBiasTracker()
    np.testing.assert_array_equal(tracker.update(row.node,decision),np.zeros(8))


def test_cut_preserves_original_reference_and_measured_kept_epochs():
    p=np.array([2.,1.3,1.])
    raw=replace(row_at(1.,p),t_round_us=tuple(1000+1000*i for i in range(8)))
    epochs=np.array([CLOCK.seconds(raw.strobe_us+.5*x) for x in raw.t_round_us])
    original_reference=float(np.median(epochs))
    state=RootState(original_reference,np.r_[p+[-.1,.1,-.1],np.zeros(6)],np.eye(9))
    selected=replace(raw,valid_mask=hard_back_facing_mask(255,np.array([-1]*4+[1]*4)))
    _,bad=update_raw_ranges(state,selected,anchors_m=ANCHORS,clock=CLOCK)
    assert bad.reason=='STATE_NOT_AT_SWEEP_REFERENCE_EPOCH'
    updated,decision=update_raw_ranges(state,selected,anchors_m=ANCHORS,clock=CLOCK,
        reference_epoch_s=original_reference)
    assert decision.accepted and decision.anchors==(4,5,6,7)
    assert updated.time_s==original_reference
    np.testing.assert_array_equal(decision.link_epochs_s,epochs[4:])
    # No external information reduction: sensor sigma remains the nominal .12 m.
    np.testing.assert_array_equal(decision.sensor_sigma_m,np.full(4,.12))
    tracker=PersistentRangeBiasTracker()
    result=tracker.update(raw.node,decision)
    np.testing.assert_array_equal(result[:4],np.zeros(4))
    np.testing.assert_array_equal(tracker._last_time[raw.node][:4],np.full(4,np.nan))


def test_explicit_reference_cannot_reuse_unbound_prepared_factors():
    p=np.array([2.,1.3,1.])
    row=row_at(1.,p)
    state=RootState(1.,np.r_[p,np.zeros(6)],np.eye(9))
    prepared=prepare_raw_range_update(state,row,anchors_m=ANCHORS,clock=CLOCK)
    with pytest.raises(ValueError,match='incompatible with prepared'):
        update_raw_ranges(state,row,anchors_m=ANCHORS,clock=CLOCK,prepared=prepared,reference_epoch_s=1.)


def test_default_reference_matches_explicit_unmasked_reference():
    p=np.array([2.,1.3,1.])
    row=row_at(1.,p)
    state=RootState(1.,np.r_[p+[-.1,.1,-.1],np.zeros(6)],np.eye(9))
    default,_=update_raw_ranges(state,row,anchors_m=ANCHORS,clock=CLOCK)
    reference=float(np.median([CLOCK.seconds(row.strobe_us+.5*x) for x in row.t_round_us]))
    explicit,_=update_raw_ranges(state,row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=reference)
    np.testing.assert_array_equal(default.vector,explicit.vector)
    np.testing.assert_array_equal(default.covariance,explicit.covariance)
