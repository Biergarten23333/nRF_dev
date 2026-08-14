# Body model contract V1

The ten estimated rigid segments are Torso, Pelvis, left/right UpperArm,
Forearm, Thigh and Shank. Placement labels such as Elbow or Wrist identify the
sensor group; they are not point-joint states.

The tree has torso-pelvis, virtual shoulders, elbows, hips and knees. Shoulder
centres are conditional torso/upper-arm parameters because no shoulder sensors
exist. There are no foot segments, so ankle/foot orientation is unavailable.

Static calibration parameters are sensor-to-segment rotations, antenna lever
arms, adjacent-frame joint offsets, upper-arm/forearm/thigh/shank lengths,
torso/pelvis shoulder and hip geometry, hinge axes and `R_N<-V4`. Calibration
windows alone fit these quantities. The freeze hash is written before held-out
data is opened.

Forward kinematics derives child origins from parent pose, fixed joint offsets
and fixed segment dimensions. Bone length therefore cannot change in a dynamic
state. A model that estimates independent sensor XYZ and connects them only for
display does not satisfy this contract.

IK/FK may report elbow/knee flexion, pelvis-torso relative orientation and
preliminary hip/shoulder motion with observability flags. It must report global
yaw gauge, conditional shoulder/hip quantities, unavailable foot orientation
and absence of clinical validation.
