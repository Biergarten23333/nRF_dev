# BioSpur C2 basis 修复 + genuine progressive calibration 中文审核表

状态：**仅供用户审核 / 未激活 / 不授权执行**

本表用于让用户逐项检查下一次 12–18 小时任务的边界。它与
`MASTER_CONTRACT.md`、`COMPLIANCE_MATRIX.json` 和
`STARTUP_PARAMETERS.json` 共同进入启动封印。若文字、机器参数或提示词之间
存在任何冲突，必须先修订并重新哈希，不得自行选择较宽松版本。

## 0. 这次交付究竟是什么

- [ ] 同一个任务同时完成：C2 basis/model 替换、真实 chronological
  progressive calibration、fresh full-batch 等价性、held-out 检验和实际画面 QA。
- [ ] 不能只交付 basis bookkeeping，也不能在旧 basis 上包装 progressive 日志。
- [ ] 当前已做的一小时 QMT 诊断只证明官方机制和输入转换可以运行，不是科学
  PASS，不是可复用的 C2 heading trajectory，也不是合格人体画面。
- [ ] 不再另开一个独立的一小时 bootstrap；正式任务先做受控 A/B/C 因果测试，
  然后进入 basis + progressive 主线。
- [ ] 不能预先承诺一定 PASS；必须承诺的是：实现错误、超时、普通候选失败不会
  被伪装成数据/设备失败，也不会成为整个任务的草率终点。

## 1. 激活、合同和不可变封印

- [ ] 只有用户审核后另发明确“开始”命令，才可创建英文 WORK 和中文 monitor。
- [ ] 本目录目前不授权改源码、打开新 C2 payload、跑 synthetic/solver/render，
  也不授权创建正式任务。
- [ ] 正式 WORK 首个 C2 payload open/hash 前，必须生成新的不可变
  `RUN_START_CONTRACT.json`。
- [ ] 启动封印绑定：本审核包全部文件哈希、用户启动消息哈希、WORK/monitor
  task id、起止时间、canonical realpath、资源门、权威文件哈希、输入语义、
  validator PASS、新 metadata-only preselection、动作/attempt allowlist 和
  byte-access plan。
- [ ] 封印之后只允许 append-only amendment；历史 seal 和合同不得覆盖。
- [ ] 静态 validator 只证明合同内部一致，不能替代运行时 seal、磁盘、preselection、
  payload firewall 和真实证据门。

## 2. 唯一工作区、写入权和资源

- [ ] 唯一路径：`/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part`。
- [ ] 不建 branch、worktree、nested checkout、代码副本或 raw-data 副本。
- [ ] 英文 WORK 是唯一写入者；中文 monitor 对该目录严格只读。
- [ ] 保留所有历史 raw、FAIL、seal、trace、viewer 和未提交用户改动。
- [ ] 开始时 `/mnt/nrf_ssd` 至少 100 GB，root 至少 40 GB；本任务预计增长
  不得超过 5 GB。
- [ ] 最多 8–10 个 CPU worker，保持桌面可用；单次无人检查的计算调用不得超过
  30 分钟。
- [ ] 所有新产物只写入一个新的 timestamped `logs/` run directory。

## 3. 数据范围和文件访问防火墙

- [ ] 仅 Capture 2；十节点必须全部存在且各出现一次，任何旧 alias/左右冲突
  fail closed。
- [ ] 禁止 C1、C3、Hxx、Capture3、Golf、Boxing 及跨 capture sharing。
- [ ] 允许字段仅 raw accelerometer、raw gyroscope、timestamp、boot epoch、
  sequence、status。
- [ ] 禁止 magnetometer、UWB spatial payload、vendor/locked/global/prior
  quaternion、Euler pose、QMT_OFF、shared IK、old profile warm start、freeze、
  candidate lock、manual/action-label pose truth、per-action stitching 和 viewer
  feedback rescue。
- [ ] 默认不得枚举、打开、hash 或为构造排除范围而读取以下 holdout 的元数据：
  `holdout/00_walk`、`holdout/H01_boxing`、`holdout/H02_golf`。
- [ ] 默认 held-out 证据是在允许的 C2 action episode 内，fit 前按结果无关规则
  预注册的 withheld blocks。
- [ ] 若以后要访问外部 holdout，必须在 fit freeze 之后获得一次新的精确授权；
  外部 holdout 结果不能触发重新拟合。

## 4. 十节点权威映射

| 节点 | 身体段 |
|---|---|
| BSFEC35 | forearm_left |
| BSFB165 | forearm_right |
| BSFAA61 | upper_arm_left |
| BSF1120 | upper_arm_right |
| BSF31CC | torso |
| BSFC2CC | pelvis |
| BSF44AD | thigh_left |
| BSF3C79 | thigh_right |
| BSF6C53 | shank_left |
| BSF8BC4 | shank_right |

- [ ] 映射必须绑定 sealed identity、wear-direction amendment、frame-semantics
  amendment 和当前 anthropometry 的哈希。
- [ ] 不允许以文件名、旧代码 alias 或画面看起来更顺为理由换边或换节点。

## 5. 定性佩戴方向和启动分支

十节点的 sensor `-Z` 依次为：左、右、左后且后向占优、右后且后向占优、
前、前、前、前、左、右；全部 sensor `-Y` 约朝地面。

- [ ] 这些是人体佩戴的定性方向锥和 gross hemisphere guard，不是精确向量、
  完整装配旋转、机械夹具或硬左右镜像。
- [ ] `-Y/-Z` 可以构造右手 nominal frame，但不能因此消灭轴向 twist、安装误差
  或其他物理合法分支。
- [ ] 启动使用宽、非紧支撑的方向概率分布并保留多分支；分布宽度不能看真人
  residual 再选，sensitivity 必须含接近无信息的半球支撑情形；不得用任意数值
  硬锥或 correction cap 把定性方向偷换成精确安装。
- [ ] 只有明显错误半球可以 hard reject；其余 mount extrinsic 必须由完整 C2
  动态证据估计。

## 6. C2 → QMT 输入契约

- [ ] 绑定 `b306-imu-relay-v47`，FWID
  `f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed`，
  image SHA
  `90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98`。
- [ ] JY61P 地址 `0x50`，从 `0x34` 读取，三轴均为 signed little-endian；
  B306 不做 axis permutation/sign flip。
- [ ] JY61P 输出/更新率为 200 Hz，B306 由 200 Hz hardware timer 触发采样，
  98 Hz 是配置带宽而不是拉取率；时间轴使用真实 B306 timestamp，不凭空补样本。
- [ ] 加速度换算：`raw / 2048 * 9.80665` m/s²；保留重力。
- [ ] 角速度换算：`deg2rad(raw / 16.384)` rad/s。
- [ ] JY61P accel measurement range 可由 2g 自适应至 16g，但输出寄存器编码固定
  为 ±16g；不得把“自适应量程”误当作逐样本缩放变化。
- [ ] 不消费 vendor quaternion/Euler。QMT quaternion 存储为 `wxyz`；
  QMT/SciPy/viewer 之间必须做 source-level active/passive 数值 round-trip。
- [ ] 静止重力模长、gyro bias/noise、saturation、duplicate/gap 是健康诊断和协方差
  证据，不能仅因普通噪声或局部 gap 就要求重录。
- [ ] 不能假定完美芯片：bias/slow drift、scale factor、cross-axis/non-orthogonality、
  quantization/static correlation、timestamp jitter、gap/duplicate/clipping 必须进入
  模型或预注册 sensitivity；普通偏差降权/扩不确定性，不是 hard stop。
- [ ] 当前默认不要求新的六面静置、±90° bench test 或重录；只有独立证据证明
  输入语义仍未闭环，才可向用户报告精确缺口并申请新的硬件动作。

## 7. C2 动作、attempt 和时间顺序

必须按 sealed metadata 的时间顺序保留全部完整
`rest → transition → action → transition → rest` episode：

`00_initial_still`、`02_t_pose`、`03_pelvis_hula_circle`、
`04_shoulder_left`、`05_shoulder_right`、`06_elbow_left`、
`07_elbow_right`、`08_hip_left`、`09_hip_right`、
`10_knee_left_seated`、`11_knee_right_seated`、`12_heel_raise_left`、
`13_heel_raise_right`、`14_trunk_flex_extend`、
`15_trunk_axial_rotation`、`16_squat`、`17_final_still`、
`18_heel_to_butt_left`、`19_heel_to_butt_right`。

- [ ] metadata-only preselection 验证 review package 中预注册的 attempt 号；
  不因结果不好改 attempt 或删 action。
- [ ] episode 是标签/因子窗口，不是重启 orientation、QMT 或 calibration 的边界。
- [ ] 左膝必须包含 seated left raise + squat + left heel-to-butt；右膝必须包含
  seated right raise + squat + right heel-to-butt。静息不能独立确定膝轴。
- [ ] sample 选择在看结果前按噪声、激励、连续有效性、协方差规则预注册并完整
  记录；禁止从 600 行挑 3 行迎合候选轴，也禁止 exact-zero 平行判据。

## 8. 连续 orientation、gap-aware QMT 和时变 heading

- [ ] 每个节点整次 capture 只有一个持续的 6-axis VQF/orientation state；
  episode reset 数必须为 0，不能产生 per-action yaw gauge。
- [ ] 缺失行采用 no-update + uncertainty growth；禁止长 gap 插值或把 gap 压缩掉。
- [ ] gap 只在某个 factor/viewer 确实需要该时刻且缺乏覆盖时，才影响该证据；
  “有 gap”不等于“整次 pelvis 没有可信姿态”。
- [ ] 先从成熟 pairwise 功能机制获得 joint axis/center 与 segment-frame branch，
  再做 heading correction；禁止为了抢跑给 QMT 塞入虚构的精确解剖轴。
- [ ] 官方 QMT `headingCorrection` 仅在预注册、稠密、连续有效 span 上运行；
  等间隔 `t` 只能绑定已验证的连续 200 Hz hardware-trigger cadence，不得造样本。
- [ ] 必须保留完整的 `deltaFilt(t)`、corrected child quaternion、rating/state、选样和
  debug；它们是带时间戳的 relative-heading observation。
- [ ] 官方算法不等于示例人体参数：advanced example 的 exact alignment、manual
  flip/sign/heading、ROM、`startRating`/`stillnessRating`、stillness/selection
  threshold、window/filter/solver settings 必须逐项登记，不得照抄成 C2 真值；
  rating 1 不能让初始静息凭空获得已知 heading。
- [ ] span 之间由九个 persistent edge state 承担 no-update covariance；九边结构是
  rooted tree，优先按 QMT 官方示例的 parent delta + child `deltaFilt` 传播，不得默认
  再造一个全局非线性 heading solver，也不得拼接独立 span profile。
- [ ] 禁止禁用 heading、丢弃 `quat2Corr`、把 `deltaFilt(t)` 平均成常数 seed、
  或只把 QMT 当优化器初值。
- [ ] factors、final refinement、progressive state、held-out evaluation 和 viewer
  必须消费同一个最终 time-varying corrected trajectory。

## 9. basis/model 的所有权边界

- [ ] measured geometry：只拥有真实外部测量及其 landmark/surface uncertainty。
- [ ] functional sensor-to-segment calibration：拥有每节点完整 SO(3) mount 和
  传感器坐标系中的完整 3D joint-center/connection vector。
- [ ] QMT parent-child heading：只拥有无磁的时变相对 heading observation。
- [ ] rooted tree：只拥有 9 个 relative-heading edge states + 1 个 pelvis yaw gauge，
  默认按官方 parent→child delta 传播，不添加无必要的全局 heading 变量。
- [ ] branch manager：维护多个物理合法分支并执行不可交易的 physical gate。
- [ ] progressive information owner：只维护一个按时间累积的 posterior/state。
- [ ] viewer：只读最终轨迹和固定几何；不能反向修姿态或参数。
- [ ] 不允许旧 over-free joint solve 的补丁、17D/axial-only restart 或在大文件里
  继续混合上述所有权。
- [ ] 依赖顺序固定：continuous orientation → pairwise functional axes/centers 与
  mount branches → QMT time-varying heading → 九边 tree propagation → genuine
  progressive → fresh batch。不得循环依赖或用虚构先验跨级。

## 10. 人体几何和成熟机制

现有**原始体表标志距离**必须逐条进入合同，不能只写一个 anthropometry 路径：

| 量 | 左 | 右 | 注意 |
|---|---:|---:|---|
| 前臂表面长度 | 245 mm；第二观察者 260–265 mm | 245 mm；第二观察者 260–265 mm | 原文件漏记左右；用户 amendment 已澄清同一区间适用于两侧。不得取方便中点、覆盖 245 mm 或硬化为内部骨长相等 |
| 上臂表面长度 | 310、325 mm | 310、325 mm | 肩峰到肱骨外上髁，不是肩关节中心到肘中心 |
| 大腿表面长度 | 480 mm | 480 mm | 大转子到股骨外上髁，不是髋中心到膝中心 |
| 小腿表面长度 | 430 mm | 430 mm | 股骨外上髁到踝中点，不是膝中心到踝中心 |

其他原始值：肩峰宽 400/425 mm、胸 IMU 到肩峰线 140/150 mm、骨盆 IMU
到胸 IMU 280 mm、髂嵴宽 335/315 mm、大转子宽 335 mm、骨盆前后深
200 mm、胸 IMU 到头顶 470 mm。

- [ ] 上述数值和 landmark definition 由
  `GEOMETRY_AND_PARAMETER_CONTRACT.json` 精确绑定；任何使用值必须能回到原始
  observation，不能只留下平均数。
- [ ] `USER_ANTHROPOMETRY_AMENDMENT_001.json` 只消除 260–265 mm 的左右来源
  歧义：左、右都保留该区间，同时保留各自 245 mm 原观察；不篡改原人体测量文件。
- [ ] 权威文件当前没有 instrument resolution，也没有 numerical measurement
  uncertainty。`sigma=0`、硬 equality 或随手猜一个 sigma 都不允许；首次真人拟合
  前必须在不看 residual 的情况下冻结 measurement + landmark-mapping uncertainty。
- [ ] 绝对 link length 不是可让 IMU residual 自由调整的参数。先由原始测量和明确
  mapping 冻结 scale likelihood；如果内部 mapping 尚未合格，只能使用明确标注的
  `LANDMARK_PROXY` skeleton 做一致 A/B/C 诊断，不能冒充 anatomical truth。
- [ ] 当前没有直接测得内部 shoulder center、hip center 或 anatomical torso
  length。髂嵴宽不是 ASIS 宽，因此现有输入不足以直接运行 Harrington 并声称
  hip center 已知；肩胛 AC/AA/TS/AI/PC 三维点也缺失，不能假装 Meskers 已完成。
- [ ] 旧配置的 0.06 m 髋垂直偏移、0.22 m 内部髋距、0.18/0.22/0.26 m 髋距
  sensitivity 当真值、八个 axial sensor offset 和旧 display geometry 全部禁止复用。
- [ ] 真人拟合前生成并封印 `ACTIVE_PARAMETER_REGISTRY.json`。每个 fixed/derived/
  fitted/state/gauge/viewer 参数必须写 owner、单位、来源、原始值/公式、uncertainty、
  bounds/manifold、消费者、A/B/C/D 类别和 sensitivity；source/config/default 扫描
  必须得到 0 个未注册参数。

- [ ] 优先按官方/论文机制实现并记录归因：QMT magnetometer-free
  parent-child heading、Seel joint-axis/center constraint、Olsson hinge-axis 及
  适用的 functional connection/center 思想；不清楚时查 primary source。
- [ ] 这是成熟机制的工程集成，不是重新发明动作捕捉理论。先做 per-edge
  applicability map 和 reference-equivalence；只写最薄 C2 timing/gap adapter。
  bespoke solver 必须先用有界试验证明一个成熟机制确实缺失的具体能力，并保留
  reference comparator。
- [ ] 传感器位置必须允许完整三维离轴：大腿前面、小腿侧面、胸腹表面都必须在
  模型可表达空间内；禁止把 IMU 强制放在骨段中心轴线上。
- [ ] V0 不让 raw IMU 重新发现绝对骨长；长度由测量/合理的不确定性拥有。
- [ ] surface breadth 不能直接当内部 hip/shoulder joint-center spacing。
- [ ] 不发明 torso length、6 cm vertical offset、理想中心线、24 cm internal hip
  spacing、硬左右对称或隐藏成人默认值。
- [ ] 双侧测量保留各自 provenance 和 uncertainty，不能先平均后伪装为真值。
- [ ] 不假定完美刚体人体：soft-tissue、缓慢绑带滑移、非理想/缓变 hinge axis、
  joint-center migration、不完美动作和不对称进入 covariance/robust loss/sensitivity；
  不得因此创建 per-action mount/profile。

## 11. 九边图、分支和不可交易的物理合法性

唯一 directed edges：

1. pelvis → torso
2. torso → upper_arm_left
3. upper_arm_left → forearm_left
4. torso → upper_arm_right
5. upper_arm_right → forearm_right
6. pelvis → thigh_left
7. thigh_left → shank_left
8. pelvis → thigh_right
9. thigh_right → shank_right

另有且只有一个 pelvis global yaw gauge。

- [ ] 初始静息只提供 gravity tilt、bias/noise/stillness 和明显不可能分支排除；
  不能声称从静息唯一得到 yaw、joint axis/center、length、axial twist 或 sagittal
  branch。
- [ ] full-circle/multi-start 生成多个物理合法分支，累积证据只能淘汰错误分支，
  不能冻结第一个低残差候选。
- [ ] physical feasibility 必须在 residual ranking 之前。
- [ ] 一膝前一膝后、crossing、mirror flip、collapse、invalid ROM、improper
  rotation、断开的 shared joint、错误 gravity/topology 都淘汰该候选，不能被
  更低 residual 抵消。
- [ ] ROM 是带个体差异和不确定性的概率 manifold：轻微、合理边界偏离只降权/
  扩不确定性；只有超出 sensitivity 后仍明显不可能才淘汰候选。膝前后分裂、
  crossing、collapse、improper rotation 和断连等拓扑错误仍是硬候选门。
- [ ] 若最低 residual 候选无效但存在合法次优候选，必须保留合法候选。

## 12. genuine progressive calibration

- [ ] 整次 C2 只有一个 persistent state；所有完整 episode 按时间顺序进入，
  不按 action 重标定、不拼 profile、不删除不方便的动作。
- [ ] prefix 是同一 posterior 的只读诊断快照，不是独立 calibration。
- [ ] progress 依据 gauge-reduced information/rank、uncertainty、branch
  concentration、physical validity 和每个 episode 摄入前已记录的 prequential
  prediction；不能按 sample/action 数或 elapsed time 造百分比。
- [ ] 最终 sealed held-out 在 fit 和所有选择冻结前不能打开；冻结后只评估，不得
  触发 refit。它不能伪装成 progressive 过程中的实时进度输入。
- [ ] 新证据发生冲突时 progress 可以下降；下降本身不是失败。
- [ ] 最终 progressive state 必须与一次 fresh cumulative all-episode batch 在
  预注册数值容差内等价，并通过 held-out。

## 13. 噪声、相关性、阈值和信息权重

- [ ] weighting 来自 covariance/information，并处理静息样本相关性；不能把
  大量相关静止行当成大量独立证据。
- [ ] threshold 在看真人拟合结果前由 sensor noise、概率、物理 manifold 或
  独立 randomized synthetic 固定，并做预注册 sensitivity。
- [ ] 不要求 IMU 或人体完美：ordinary noise、soft-tissue artifact、轻微轴偏差、
  scale/cross-axis error、bias drift、timestamp jitter、mount micro-motion、轴/中心
  缓变、quantization 和局部低激励属于不确定性，不是 exact-equality hard stop。
- [ ] 禁止结果相关的 threshold relaxation、desired-row selection、action removal
  或反复 blind restart。

## 14. 独立 synthetic qualification

- [ ] synthetic truth generator 不能调用 estimator 的 geometry/residual 来生成
  真值；代码文件分开但模型相同不算独立。
- [ ] 正例包含 broad-cone SO(3) mounts、任意 3D sensor offsets、人体尺寸/不对称、
  joint-axis/center 变化、不完美动作、soft-tissue/strap slippage、bias/scale/cross-axis
  error、noise/quantization、timestamp jitter、clipping edge、static correlation、duplicates、gaps、time-varying yaw drift、
  span gaps、episode order permutation、degenerate prefixes、full-circle/multibranch。
- [ ] 负 mutation 必须拒绝：front/back knee split、crossing、mirror、collapse、
  disconnected joint、per-action stitching、cross-capture sharing、leaked truth、
  exactized wear prior、fake progress、false still completion、mean-heading seed、
  invalid-low-residual candidate 挤掉合法次优候选、viewer rescue 和 holdout leak。
- [ ] synthetic 没通过前不能拟合真人 C2；失败后必须保存证据、定位模型/实现原因
  并做有界因果 pivot，不能调阈值蒙混。

## 15. 受控 A/B/C 因果比较

- [ ] A：历史 failed trajectory 在 display retarget/rebase 之前的原始版本，仅供
  诊断，不能 warm start 或影响拟合。
- [ ] B：同一 C2 的 continuous VQF trajectory。
- [ ] C：真正消费 QMT 时变输出并通过九边 persistent state 得到的 corrected
  trajectory。
- [ ] A/B/C 使用完全相同的 timestamp、固定 geometry、pelvis gauge、frame 和
  camera；禁止 measured-proportion retarget、torso rebase、shoulder-yaw rescue、
  per-action correction、IK、repair 或 smoothing rescue。
- [ ] 只有在同一 renderer 中才能把画面改善归因给 heading/model，而不是显示器
  偷改人体。

## 16. viewer 和独立像素 QA

- [ ] viewer 是 direct raw-path fixed-geometry FK，算法与 viewer 使用同一最终
  corrected trajectories；无 IK/no repair/no rebase/no retarget。
- [ ] 同时输出 sensor/segment diagnostic viewer 和 scientific anatomical FK。
- [ ] 肩/髋分支宽度不能为 0；真实 joint-center spacing 尚未可识别时，必须明确
  标注 proxy + uncertainty，A/B/C 一致使用，不能当 PASS 证据。
- [ ] 必看实际像素：initial standing、代表 upper action、左下肢、右下肢、squat、
  final standing；每个场景 front/side/top。
- [ ] 英文 WORK 自己看，中文 monitor 也独立看；布尔 `viewer_pass=true`、坐标表或
  worker 自述都不能代替实际图片。
- [ ] 发现一膝前一膝后、crossing、折叠、断连、mirror、严重 torso compression
  立即淘汰该候选并继续诊断，不能交付 false PASS。

## 17. 非致命问题不能终止整个任务

所有 predicate 在执行前分为四类：

- **A — 当前 run 的架构/来源 blocker**：例如错 capture/mapping、禁用字段、
  seal 前 payload、holdout firewall、per-episode reset、authority mismatch、磁盘门、
  严重 parse corruption、non-finite state。只停止当前 run，保留证据，修因并重新
  seal；不等于 terminal scientific FAIL。
- **B — 候选/分支物理淘汰**：例如膝前后分裂、crossing、mirror、collapse、
  invalid ROM、断连。只淘汰该候选，继续其他合法分支。
- **C — 随机证据/不确定性**：例如低激励、QMT rating、噪声、soft tissue、
  landmark uncertainty、局部 held-out conflict。调整 covariance/information 或
  报不确定性；不停止、不删 episode。
- **D — 数值/样本诊断**：例如孤立 near-axis 行、duplicate、局部 warning、一次
  multistart 失败、暂时画面失败。记录、分析、继续。

- [ ] ordinary solver/test/visual FAIL 是诊断事件，必须保存 trace、检查画面/primary
  source、做一个有界 hypothesis test 并进行 in-scope causal pivot。
- [ ] 只有用户撤销授权、不可恢复的外部依赖，或有证据证明模型类在本范围内不可
  识别，才可结束；普通 timeout、bug、zero finite candidates 或 invalid viewer
  都不能冒充科学 FAIL。

## 18. 18 小时上限和阶段检查点

| 阶段 | 最大时间 | 目标 |
|---|---:|---|
| P0 | 0.5 h | seal、input、firewall、validator |
| P1 | 2.0 h | continuous frontend + stochastic/synthetic harness + mature-method applicability |
| P2 | 4.0 h | functional SO(3) + 3D geometry |
| P3 | 4.0 h | QMT heading + nine-edge rooted tree + branches |
| P4 | 1.5 h | identical-renderer A/B/C |
| P5 | 4.0 h | real C2 genuine progressive |
| P6 | 2.0 h | batch/held-out/final visual QA |

- [ ] 阶段时间是上限和检查点，不是到了时间就宣称 PASS/FAIL。
- [ ] 每阶段有 iteration/wall limit、convergence trace、参数/分支/不确定性快照。
- [ ] 超过阶段上限必须保存证据、解释 causal boundary、缩小有界试验并继续总体
  目标；禁止 8–9 小时盲跑。
- [ ] 总任务到 18 小时必须交付当时真实证据和准确边界；不能偷偷延长，也不能
  为赶时间造 PASS。

## 19. 最终证据包和判决

- [ ] hashes：合同、authority、source/config、payload byte plan、输出 artifact。
- [ ] file-access firewall 日志：打开了什么、何时、范围；证明未访问禁区。
- [ ] 输入契约证据：单位、axis/sign、time、gravity、range、quaternion round-trip。
- [ ] source diff、ownership 说明、primary-source attribution。
- [ ] synthetic 正例/负 mutation、随机种子、结果和 failure trace。
- [ ] QMT `deltaFilt(t)`、corrected quaternion、rating、选样、gap/uncertainty。
- [ ] progressive 曲线：rank/information/uncertainty/branch/physical/prequential，允许
  下降；post-freeze held-out 结果单独报告，不能伪装成拟合中曲线。
- [ ] 所有 used/fitted parameter、单位、prior/uncertainty、可识别性和冲突。
- [ ] fresh batch equivalence、held-out 结果、A/B/C 和最终三视图路径。
- [ ] WORK 与 monitor 的独立像素结论、磁盘增长、named unresolved conflicts。

最终只能是：

- **PASS**：全部 architecture、synthetic、physical、progressive、batch、held-out、
  file firewall 和像素证据通过。
- **INCONCLUSIVE**：实现正确但仍存在明确、命名、量化的不可识别性/证据冲突，
  不夸大为 PASS，也不推卸为数据坏。
- **FAIL**：仅在证据证明规定模型类对现有范围系统性失败且已完成因果分析时使用。
  bug、timeout、普通 solver failure 或坏候选画面不能作为科学 FAIL。

## 20. 一票否决的重复错误

- [ ] 旧 over-free/axial/17D solve 补丁或盲重启。
- [ ] per-action VQF/QMT/calibration reset、profile stitching 或跨 capture sharing。
- [ ] 丢弃 `quat2Corr`/`deltaFilt(t)`，或压成平均 heading seed。
- [ ] 把定性佩戴方向 exactize，或把人/IMU 当完美机器。
- [ ] 从少量“刚好平行”的行反推轴、结果相关选样或硬 exact equality。
- [ ] surface measurement 伪装 internal joint center、虚构 torso/hip/shoulder 几何。
- [ ] residual-first 让非法低残差候选挤掉合法次优候选。
- [ ] viewer retarget/rebase/IK/repair、美化后回写算法。
- [ ] 为通过而放宽 threshold、删除 action、删 prior FAIL/raw 或扫描禁用 holdout。
- [ ] 不看实际像素就声称成功，或把普通失败/实现错误写成 terminal FAIL。

## 用户审核动作

- [ ] 我确认上述目标、输入范围、十节点映射和佩戴方向无误。
- [ ] 我确认默认禁止外部 holdout，held-out 先从允许 C2 episode 内预注册。
- [ ] 我确认物理非法只淘汰候选，普通噪声/低激励/局部失败不停止整体任务。
- [ ] 我确认正式启动时才创建两个新 task，并将本审核包全部哈希写入 immutable
  run-start seal。
- [ ] 若我提出修改，先改本审核包并重新跑 validator；在我最终明确“开始”前，
  不执行主任务。
