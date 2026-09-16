"""Transactional chronological owner for the synthetic upper-arm prototype.

An input adapter sends only arrived chunks. Finalized phases add evidence
once; fitting replaces the cumulative estimate. No full-session cache,
ten-node artifact or neural state is accepted by this narrow prototype.
"""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from .arm_factors import PHASES, PHASE_IDS, build_phase, rows_digest, validate_rows
from .arm_model import residual_terms, update_arm


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def implementation_binding():
    from biospur_fusion.c2_sparse_nodes import calibration
    files=[Path(__file__).with_name(n) for n in ('session.py','arm_factors.py','arm_model.py')]
    files.append(Path(calibration.__file__))
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


class ArmProgressiveSession:
    SCHEMA = 'C2_SYNTHETIC_ARM_PROGRESSIVE_V1'

    def __init__(self, *, source_kind):
        if source_kind!='synthetic_orientation_and_gyro':
            raise ValueError('real raw-six-axis adapter is not implemented')
        self._state = dict(schema=self.SCHEMA,source_kind=source_kind,phase_cursor=0,
            implementation_binding=implementation_binding(),
            ledger={},factors=[],snapshots=[],receipts={},pending=None,last_stop=None,
            arms=[update_arm([],i) for i in range(2)])

    @property
    def snapshots(self):
        return copy.deepcopy(self._state['snapshots'])

    @property
    def state_digest(self):
        return digest(self._state)

    def snapshot(self):
        return copy.deepcopy(self._state['snapshots'][-1]) if self._state['snapshots'] else None

    def ingest(self, phase_id, start, stop, rows, *, chunk_id, final):
        if not isinstance(final,bool) or not isinstance(chunk_id,str) or not chunk_id:
            raise ValueError('explicit chunk identity and final boolean required')
        time = validate_rows(rows)
        receipt = dict(phase=phase_id,start=float(start),stop=float(stop),final=final,rows=rows_digest(rows))
        if chunk_id in self._state['receipts']:
            if self._state['receipts'][chunk_id]!=receipt:
                raise ValueError('chunk identity reused with different evidence')
            return self.snapshot()
        cursor = self._state['phase_cursor']
        if cursor>=len(PHASE_IDS) or phase_id!=PHASE_IDS[cursor]:
            raise ValueError('only the exact next chronological phase is accepted')
        if (not np.isfinite([start,stop]).all() or stop-start!=PHASES[cursor][2]
                or time[0]<start or time[-1]>=stop):
            raise ValueError('invalid formal phase time support')
        pending = copy.deepcopy(self._state['pending'])
        if pending is None:
            if self._state['last_stop'] is not None and start<self._state['last_stop']:
                raise ValueError('phase clock moved backwards')
            pending=dict(phase=phase_id,start=float(start),stop=float(stop),rows={n:[] for n in NODES})
        if (pending['phase'],pending['start'],pending['stop'])!=(phase_id,start,stop):
            raise ValueError('chunk changed the pending phase contract')
        if pending['rows'][NODES[0]] and time[0]<=pending['rows'][NODES[0]][-1][0]:
            raise ValueError('overlapping or repeated physical sample support')
        for node in NODES:
            pending['rows'][node].extend(np.asarray(rows[node],dtype=float).tolist())
        if not final:
            self._state['pending']=pending
            self._state['receipts'][chunk_id]=receipt
            return self.snapshot()
        full={n:np.asarray(v) for n,v in pending['rows'].items()}
        t=full[NODES[0]][:,0]
        # This fixture gate checks phase completion, not a production gap policy.
        if abs(t[0]-start)>1e-6 or abs(t[-1]+.005-stop)>1e-6 or not np.allclose(np.diff(t),.005,atol=1e-6,rtol=0):
            raise ValueError('complete native-200 synthetic phase required')
        increment,diagnostics=build_phase(phase_id,start,stop,full)
        ledger=copy.deepcopy(self._state['ledger'])
        source=rows_digest(full)
        if phase_id in ledger:
            raise ValueError('physical phase already registered')
        ledger[phase_id]=dict(source_sha256=source,start=float(t[0]),max_evidence_time=float(t[-1]),
                             rows_per_node=len(t),factor_ids=[f['id'] for f in increment])
        factors=self._state['factors']+increment
        if len({f['id'] for f in factors})!=len(factors):
            raise ValueError('duplicate cumulative factor')
        arms=[]; prequential=[]
        for limb,old in enumerate(self._state['arms']):
            new=[f for f in increment if f['limb']==limb]
            previous=old.get('selected')
            prediction=(sum(float(v@v/2) for v in residual_terms(previous['parameters'],new).values())
                        if previous is not None and new else None)
            prequential.append(prediction)
            arms.append(update_arm([f for f in factors if f['limb']==limb],limb,old['candidates'])
                        if new else copy.deepcopy(old))
        snapshot=dict(index=cursor,phase=phase_id,max_evidence_time=float(t[-1]),
            ledger_sha256=digest(ledger),evidence_phase_count=len(ledger),factor_count=len(factors),
            arms=arms,new_phase_prediction_cost_before_update=prequential,diagnostics=diagnostics,
            update_semantics='REPLACE_CUMULATIVE_FIT; previous values are starts, not observations',
            prototype_phase_progress=[cursor+1,len(PHASE_IDS)],full_C2_complete=False,
            calibration_accepted=False,reference_used=False,statistical_confidence_claimed=False)
        # Commit only after all factors, fits and snapshot serialization succeed.
        digest(snapshot)
        self._state.update(phase_cursor=cursor+1,ledger=ledger,factors=factors,arms=arms,
                           pending=None,last_stop=float(stop))
        self._state['snapshots'].append(copy.deepcopy(snapshot))
        self._state['receipts'][chunk_id]=receipt
        return copy.deepcopy(snapshot)

    def save(self, path):
        path=Path(path)
        envelope=dict(state=self._state,sha256=self.state_digest)
        with path.open('x') as f:
            json.dump(envelope,f,allow_nan=False)

    @classmethod
    def load(cls,path):
        value=json.loads(Path(path).read_text())
        state=value['state']
        if value['sha256']!=digest(state) or state['schema']!=cls.SCHEMA:
            raise ValueError('checkpoint integrity/schema mismatch')
        result=cls(source_kind=state['source_kind'])
        if state.get('implementation_binding')!=result._state['implementation_binding']:
            raise ValueError('checkpoint producer implementation changed')
        if len(state['snapshots'])!=state['phase_cursor'] or tuple(state['ledger'])!=PHASE_IDS[:state['phase_cursor']]:
            raise ValueError('checkpoint chronology mismatch')
        result._state=state
        return result

    def batch_check(self):
        """Fresh fixed-start fit of the same ledger; never a calibration update."""
        return [update_arm([f for f in self._state['factors'] if f['limb']==i],i) for i in range(2)]
