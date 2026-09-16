"""Shared, frame-invariant body feasibility for the reduced five-IMU rig.

This is an explicit experimental envelope, not subject-specific clinical ROM.
The collision primitive follows the shape-dependent proxy approach of SMPLify
(Bogo et al., 2016); an ellipsoid replaces capsules here. Segment/ellipsoid
intersection is analytical, so sparse point sampling cannot miss a crossing.
No action labels, ten-node outputs, or sensor measurements enter this owner.
"""
import copy

import numpy as np
import torch
import torch.nn.functional as F

from .geometry import joints_from_global

SCHEMA = 'FIVE_BODY_ENVELOPE_V1'
ARM_SEGMENTS = ((16,18),(18,20),(17,19),(19,21))


def make_body_spec(geometry, rest_vertices, surface):
    """Derive the external torso proxy without replacing joint-centre widths.

    Torso bones are unchanged by subject_geometry's limb-length scaling. The
    proxy is attached at joint 3 and transported by the shared thorax frame;
    all limb endpoints still come from the measured-length authoritative FK.
    Thickness uncertainty is declared, not fitted against a failing frame.
    """
    rest=np.asarray(geometry['rest_joints_m'],dtype=float)
    vertices=np.asarray(rest_vertices,dtype=float)
    if vertices.ndim!=2 or vertices.shape[1]!=3 or not np.isfinite(vertices).all():
        raise ValueError('finite rest skin vertices required')
    shoulder=rest[[16,17]].mean(0)
    lower=rest[3]
    span=shoulder[1]-lower[1]
    if span<=.1:raise ValueError('invalid torso rest geometry')
    rows={r['measurement_id']:r for r in surface['measurements']}
    width=np.asarray([r['value_mm']/1000 for r in rows['biacromial_breadth']['observations']
                      if 'value_mm' in r])
    if not len(width) or np.any(width<=0):raise ValueError('external shoulder breadth required')
    skin=vertices[(vertices[:,1]>lower[1])&(vertices[:,1]<shoulder[1]-.04)
                  &(abs(vertices[:,0])<np.linalg.norm(rest[16]-rest[17])/2)]
    if len(skin)<20:raise ValueError('insufficient torso surface support')
    zlo,zhi=np.quantile(skin[:,2],[.02,.98])
    centre=np.array([shoulder[0],(lower[1]+shoulder[1])/2,(zlo+zhi)/2])
    # Two nested proxies. Only penetration of the shrunken inner core is a
    # rejection; outer-body proximity is a soft term. Neither is a skin scan.
    outer=np.array([width.mean()*.42,span*.58,(zhi-zlo)*.5])
    inner=outer*np.array([.75,.85,.65])
    return dict(schema=SCHEMA,anchor_joint=3,rotation_joint=9,
        centre_from_anchor_m=(centre-lower).tolist(),inner_radii_m=inner.tolist(),
        outer_radii_m=outer.tolist(),penetration_tolerance_m=.002,
        collision_scale_m=.02,collision_weight=10.,
        torso_swing_soft_deg=90.,torso_twist_soft_deg=100.,angular_scale_deg=20.,
        shoulder_posterior_soft_deg=70.,angular_weight=.2,
        provenance=dict(external_shoulder_readings_m=width.tolist(),
            thickness='SMPL mean skin quantiles; 65%-100% depth proxy, not measured chest depth',
            body_shape='nested conservative ellipsoids; not a guaranteed subject surface',
            shoulder_width='external acromion breadth scales envelope only, never joint centres',
            angular_limits='broad engineering soft envelopes; not clinical or C2-fitted ROM',
            source='https://arxiv.org/abs/1607.08128',
            limitations='rigid shoulder girdle; no independent scapula, limb radius or full-body contact model'))


def segment_core_depth(start, end, centre, radii):
    """Positive conservative depth proxy iff a segment enters the ellipsoid.

    The distance is in the ellipsoid's normalized metric, converted using its
    smallest radius. It is NOT an exact Euclidean penetration depth.
    """
    a=(start-centre)/radii
    direction=(end-start)/radii
    parameter=(-(a*direction).sum(-1)/direction.square().sum(-1).clamp_min(1e-15)).clamp(0.,1.)
    closest=a+parameter[...,None]*direction
    return F.relu(1.-torch.linalg.vector_norm(closest,dim=-1))*radii.min()


def relative_coordinates(rotation, geometry):
    """Angles depend on body-relative frames, never display/global heading."""
    j=joints_from_global(rotation,geometry)
    relative=rotation[:,0].transpose(-1,-2)@rotation[:,9]
    up=relative[:,:,1]
    swing=torch.atan2(torch.linalg.vector_norm(up[:,[0,2]],dim=-1),up[:,1])
    left=relative[:,:,0]
    supported=torch.linalg.vector_norm(left[:,[0,2]],dim=-1)>1e-6
    # atan2(0,0) has undefined derivatives: choose a finite reference only
    # where horizontal heading is unobservable, and report that support.
    twist=torch.atan2(torch.where(supported,-left[:,2],torch.zeros_like(left[:,2])),
                      torch.where(supported,left[:,0],torch.ones_like(left[:,0])))
    arms=torch.stack((j[:,18]-j[:,16],j[:,19]-j[:,17]),1)
    arms=(rotation[:,9].transpose(-1,-2)[:,None]@F.normalize(arms,dim=-1)[...,None]).squeeze(-1)
    return j,dict(torso_swing_rad=swing,torso_twist_rad=twist,
                  torso_twist_supported=supported,upper_arm_in_thorax=arms)


class BodyFeasibility:
    def __init__(self, geometry):
        self.geometry=geometry
        self.spec=copy.deepcopy(geometry.get('body_feasibility'))
        self.frame_model='rigid' if self.spec is None else self.spec.get('frame_model','rigid')
        if self.frame_model not in ('rigid','skeletal_chord'):
            raise ValueError('unknown body envelope frame')
        if geometry.get('torso_pose_model','rigid')=='relative_prior' and self.spec is not None and self.frame_model!='skeletal_chord':
            raise ValueError('relative torso requires a compatible skeletal body envelope')
        if self.spec is not None:
            s=self.spec
            if s.get('schema')!=SCHEMA or s['anchor_joint']!=3 or s['rotation_joint']!=9:
                raise ValueError('unsupported body envelope convention')
            inner,outer=np.asarray(s['inner_radii_m']),np.asarray(s['outer_radii_m'])
            if inner.shape!=(3,) or outer.shape!=(3,) or not np.isfinite([inner,outer]).all() or np.any(inner<=0) or np.any(outer<inner):
                raise ValueError('positive nested body radii required')
            if self.frame_model=='skeletal_chord':
                rest=joints_from_global(torch.eye(3,dtype=torch.float64).repeat(1,24,1,1),geometry)[0]
                span=(rest[16]+rest[17])/2-rest[3]
                if span[1]<=.1:raise ValueError('insufficient rest torso height')
                centre=torch.as_tensor(s['centre_from_anchor_m'],dtype=rest.dtype)
                self.centre_fraction=float(centre[1]/span[1])
                self.centre_offset=centre-self.centre_fraction*span

    def envelope_frame(self, rotation, joints):
        """Fixed-radius proxy follows the lower-to-upper torso chord.

        This remains one approximate ellipsoid, not a deformable skin model.
        Shoulder width does not scale its radii or create fitted body size.
        """
        if self.frame_model=='rigid':
            frame=rotation[:,9]
            offset=torch.as_tensor(self.spec['centre_from_anchor_m'],dtype=rotation.dtype,device=rotation.device)
            return frame,joints[:,3]+(frame@offset[...,None]).squeeze(-1)
        upper=(joints[:,16]+joints[:,17])/2;lower=joints[:,3]
        up=upper-lower;left=joints[:,16]-joints[:,17]
        if torch.any(up.norm(dim=-1)<1e-6):raise ValueError('degenerate torso chord')
        up=F.normalize(up,dim=-1);left=left-(left*up).sum(-1,keepdim=True)*up
        if torch.any(left.norm(dim=-1)<1e-6):raise ValueError('degenerate shoulder chord')
        left=F.normalize(left,dim=-1);forward=torch.linalg.cross(left,up)
        frame=torch.stack((left,up,forward),dim=-1)
        centre=lower+self.centre_fraction*(upper-lower)+(frame@self.centre_offset.to(rotation)[...,None]).squeeze(-1)
        return frame,centre

    def evaluate(self, rotation, valid, *, residual_blocks=None):
        zero=rotation.sum()*0.
        if self.spec is None:
            return dict(body_loss=zero,body_violation=zero,body_enabled=False)
        s=self.spec;j,c=relative_coordinates(rotation,self.geometry)
        frame,world_centre=self.envelope_frame(rotation,j)
        local=(frame.transpose(-1,-2)[:,None]@(j-world_centre[:,None])[...,None]).squeeze(-1)
        centre=torch.zeros(3,dtype=rotation.dtype,device=rotation.device)
        depths=[]
        for key in ('inner_radii_m','outer_radii_m'):
            radii=torch.as_tensor(s[key],dtype=rotation.dtype,device=rotation.device)
            depths.append(torch.stack([segment_core_depth(local[:,a],local[:,b],centre,radii)
                                       for a,b in ARM_SEGMENTS],-1))
        inner,outer=depths
        excess=F.relu(inner[valid]-s['penetration_tolerance_m'])
        scale=np.deg2rad(s['angular_scale_deg'])
        swing=F.relu(c['torso_swing_rad'][valid]-np.deg2rad(s['torso_swing_soft_deg']))/scale
        twist=F.relu(c['torso_twist_rad'][valid].abs()-np.deg2rad(s['torso_twist_soft_deg']))/scale
        # A broad posterior-reach prior in the thorax frame, jointly acting
        # on thorax and upper arm. This is not a complete shoulder ROM model.
        posterior=F.relu(-c['upper_arm_in_thorax'][valid,:,2]-np.sin(np.deg2rad(s['shoulder_posterior_soft_deg'])))
        loss=s['collision_weight']*((excess/s['collision_scale_m']).square().mean()
            +.1*(outer[valid]/s['collision_scale_m']).square().mean())
        loss=loss+s['angular_weight']*(swing.square().mean()+twist.square().mean()+posterior.square().mean())
        from .residual_blocks import record_mean
        for name, value, weight in (
            ('body_inner', excess/s['collision_scale_m'], s['collision_weight']),
            ('body_outer', outer[valid]/s['collision_scale_m'], .1*s['collision_weight']),
            ('body_swing', swing, s['angular_weight']),
            ('body_twist', twist, s['angular_weight']),
            ('body_posterior', posterior, s['angular_weight'])):
            record_mean(residual_blocks, name, value, weight)
        return dict(body_loss=loss,body_violation=excess.max(),body_enabled=True,
                    inner_depth_proxy_m=inner,outer_depth_proxy_m=outer,**c)

    def audit(self, rotation, valid):
        with torch.no_grad():terms=self.evaluate(rotation,valid)
        if not terms['body_enabled']:return dict(enabled=False,accepted=False,reason='no body envelope configured')
        depth=terms['inner_depth_proxy_m'][valid]
        return dict(enabled=True,schema=SCHEMA,envelope_frame_model=self.frame_model,accepted=float(terms['body_violation'])<=1e-9,
            body_loss=float(terms['body_loss']),max_excess_proxy_m=float(terms['body_violation']),
            intersecting_frames_by_segment=(depth>self.spec['penetration_tolerance_m']).sum(0).tolist(),
            max_inner_depth_proxy_m_by_segment=depth.max(0).values.tolist(),
            valid_frames=int(valid.sum()),segment_names=['left_upper_arm','left_forearm','right_upper_arm','right_forearm'],
            torso_swing_p95_deg=float(torch.rad2deg(terms['torso_swing_rad'][valid]).quantile(.95)),
            torso_twist_abs_p95_deg=float(torch.rad2deg(terms['torso_twist_rad'][valid].abs()).quantile(.95)),
            torso_twist_unsupported_frames=int((~terms['torso_twist_supported'][valid]).sum()),
            acceptance_scope='inner-core segment exclusion only; not full anatomy or tracking accuracy',
            geometry_policy=self.spec)


def selection_key(loss, violation=0.):
    """Feasible candidates precede infeasible ones regardless of data loss.

    Before feasibility restoration, reduce worst violation; do not label that
    best-effort fallback accepted. Optimizers must separately report failure.
    """
    value=float(violation.detach()) if torch.is_tensor(violation) else float(violation)
    energy=float(loss.detach()) if torch.is_tensor(loss) else float(loss)
    return (value>1e-9, value if value>1e-9 else 0., energy)
