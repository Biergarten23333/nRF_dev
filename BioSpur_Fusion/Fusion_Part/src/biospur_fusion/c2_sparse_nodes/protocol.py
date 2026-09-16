"""Complete 00–19 calibration ledger for the five-node measurement model."""
import json
import numpy as np
from .inputs import DATASET,NODES,sha

# Calibration labels constrain parameter identification only. They are never
# accepted as runtime motion-state inputs by the frozen replay solver.
ROLES=(
 ('00_initial_still','fit','重力参考、静态偏置和噪声；自然站立，不强制直臂'),
 ('01_neutral_sway','availability','自然微摆稳定性；必须记录缺采状态，不生成替代样本'),
 ('02_t_pose','fit','左右前臂侧向平面和初始朝向；允许自然下沉'),
 ('03_pelvis_hula_circle','validate','骨盆多轴激励、不同幅度和换向下的坐标与加速度一致性'),
 ('04_shoulder_left','fit','左肩运动对安装偏移和传感器运动半径的约束'),
 ('05_shoulder_right','fit','右肩运动对安装偏移和传感器运动半径的约束'),
 ('06_elbow_left','fit','0–15 秒肘轴；15–30 秒前臂纵轴，分别识别'),
 ('07_elbow_right','fit','0–15 秒肘轴；15–30 秒前臂纵轴，分别识别'),
 ('08_hip_left','validate','小腿接近下垂时的左腿平移激励；不能只看姿态变化'),
 ('09_hip_right','validate','小腿接近下垂时的右腿平移激励；不能只看姿态变化'),
 ('10_knee_left_seated','fit','全 30 秒左膝轴、屈伸方向及膝到 IMU 安装偏移'),
 ('11_knee_right_seated','fit','全 30 秒右膝轴、屈伸方向及膝到 IMU 安装偏移'),
 ('12_heel_raise_left','validate','左下肢支撑与足端运动；没有足部 IMU，不宣称测得踝角'),
 ('13_heel_raise_right','validate','右下肢支撑与足端运动；没有足部 IMU，不宣称测得踝角'),
 ('14_trunk_flex_extend','fit','通过骨盆实际参与的屈伸识别骨盆横轴和前后方向'),
 ('15_trunk_axial_rotation','validate','五节点共同旋转与相对旋转；检查躯干估计而非锁死骨盆'),
 ('16_squat','validate','双腿与骨盆耦合运动、前后方向及骨长动力学一致性'),
 ('17_final_still','fit','仅估计共同航向闭合与静态漂移，不能逐肢段拉回直立'),
 ('18_heel_to_butt_left','validate','左膝轴跨姿态一致性及屈曲分支'),
 ('19_heel_to_butt_right','validate','右膝轴跨姿态一致性及屈曲分支'),
)


def calibration_ledger(episodes,calibration):
    definitions={}
    for path in sorted((DATASET/'actions').glob('*/ACTION_DEFINITION.json')):
        doc=json.loads(path.read_text());definitions[doc['action_id']]=(path,doc)
    rows=[]
    for name,role,purpose in ROLES:
        path,definition=definitions[name]
        acquired=name in episodes
        row=dict(action=name,role=role,purpose=purpose,acquired=acquired,
            definition_sha256=sha(path),instruction=definition['instruction_zh'])
        if acquired:
            row['node_evidence']={n:dict(samples=len(episodes[name][n]['imu']),
                gyro_rms_rads=float(np.sqrt(np.mean(episodes[name][n]['imu'][:,8:]**2))),
                accelerometer_axis_std_mps2=np.std(episodes[name][n]['imu'][:,5:8],axis=0).tolist()) for n in NODES}
        else:
            if definition.get('status')!='OPERATOR_SKIPPED_NOT_ACQUIRED':
                raise ValueError('unexplained missing calibration action '+name)
            row.update(status=definition['status'],reason=definition['skip_reason'])
        rows.append(row)
    return dict(planned_actions=20,acquired_actions=sum(r['acquired'] for r in rows),
        node_order=list(NODES),actions=rows,
        runtime_action_labels_allowed=False,removed_node_measurements_allowed=False,
        initial_pose_is_not_joint_angle_ground_truth=True,
        claim='complete protocol accounting; fit and validation roles are distinct, motion accuracy requires separate evidence')
