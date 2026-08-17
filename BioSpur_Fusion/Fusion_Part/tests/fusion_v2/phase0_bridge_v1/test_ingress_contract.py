import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.phase1.ingress import ImuObservation,NODES
def test_identity_scope():assert len(NODES)==10 and len(set(NODES))==10
def test_abi_has_no_forbidden_modalities():
 fields=set(ImuObservation.__dataclass_fields__)
 assert not fields & {"uwb","q1","t4","position","anatomical_role","historical_pose"}
def test_mapping_defaults():
 o=ImuObservation(NODES[0],0,1,0,1,10,10000,100,(0.,0.,9.8),(0.,0.,0.),"D1","still","m","a")
 assert o.logical_role is None and o.mapping_status=="UNASSIGNED"
