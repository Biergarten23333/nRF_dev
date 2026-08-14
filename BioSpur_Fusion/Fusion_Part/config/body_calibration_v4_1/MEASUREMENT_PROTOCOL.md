# V4.1 人体与鞋具测量规程

状态：`INPUT_ACQUISITION_ONLY`

适用采集：`v47_ten_node_body_calibration_20260814_093601`

单位：除体重外，所有长度一律为 **mm**。

本规程只准备 V4.1 输入，不运行标定，不读取 calibration ledger、raw payload、`walk` 或 `final_still`。表格中的三次原始读数必须全部保留；操作员不要平均、四舍五入成一个“最好值”，也不要凭感觉填写 uncertainty。后续工具根据三次读数、仪器分辨率和已公布模型误差确定不确定度。

## 1. 需要的工具和基本规则

- 软尺：量沿体表或较长的纵向距离；建议最小刻度 1 mm。
- 大型滑动卡尺或人体测量尺：量左右宽度、前后深度；记录其最小刻度。
- 可擦皮肤笔：先标出骨性标志，再测量。
- 直尺和硬质直角板：量地面到踝点的垂直高度。
- 鞋底厚度卡尺：量后跟与前掌 stack；不要用“约 7 cm”代替。
- 可选的 3D digitizer：只有 Meskers 肩关节中心推导需要。普通卷尺无法提供所需的五个肩胛骨标志三维坐标。

每一个标量距离按以下顺序测三次：定位标志 → 测量 → 松开量具并重新定位 → 测量 → 再次完全重新定位 → 测量。不要连续夹着同一位置读三遍。把三个读数分别写入 `repeat_1_mm`、`repeat_2_mm`、`repeat_3_mm`。无法可靠找到标志时留空并说明原因，禁止填 `0`。

人体自然站立、目视前方。纵向肢段测量时保持被测关节伸直但不要用力锁死。左右必须分开测，不能把一侧抄到另一侧。

## 2. 方向与标志速查图

正面示意（不是按比例绘制）：

```text
                   C7 在背面颈根部
        acromion L o-------------o acromion R
                   \           /
                    \         /
             ASIS L  o-------o  ASIS R
                       pelvis

        lateral elbow o       o lateral elbow
                      |       |
      radial/ulnar   [o]     [o]  radial/ulnar
      styloid midpoint

 greater trochanter  o       o  greater trochanter
                     |       |
 lateral knee        o       o  lateral knee
                     |       |
 malleolar midpoint [o]     [o] malleolar midpoint
```

骨盆侧面示意：

```text
                  anterior (+) --->
              ASIS o-------------o PSIS
                   <--- pelvis depth --->

后侧中点：mid-PSIS = 左右 PSIS 标记的几何中点。
pelvis depth = 左右 ASIS 中点到左右 PSIS 中点的水平前后距离，
不是沿皮肤弧线的软尺长度。
```

## 3. 标志的普通语言定义

| 名称 | 如何找到 | 本规程采用的点 |
|---|---|---|
| Acromion（肩峰） | 从锁骨向肩外侧摸，到肩顶部最外侧的硬骨边缘 | 肩峰最外侧、最容易重复定位的骨点；左右用同一规则 |
| Lateral epicondyle（肱骨外上髁） | 屈伸肘时，在肘外侧摸到不随前臂转动的硬骨凸点 | 凸点中心 |
| Wrist styloid midpoint（腕茎突中点） | 同时标记拇指侧桡骨茎突和小指侧尺骨茎突 | 两标记间直线的中点，不是腕表中心 |
| Greater trochanter（股骨大转子） | 手按髋外侧，缓慢内外旋大腿；能感觉到随股骨转动的大骨突 | 大骨突最外侧中心 |
| Lateral knee landmark（膝外侧标志） | 找股骨外上髁，而不是髌骨外缘或腓骨头 | 股骨外上髁中心；左右均用同一标志 |
| Malleolar midpoint（踝穴中点） | 标记内踝和外踝最突出的中心 | 两标记间直线的几何中点 |
| ASIS | 从髂嵴向前下方摸到左右骨盆前方明显骨突 | 左右髂前上棘最前突、可重复定位的点 |
| PSIS | 腰背两侧常见“腰窝”附近，触诊髂后上棘 | 左右 PSIS 骨点；`mid-PSIS` 是二者中点 |
| C7 | 低头时颈根部明显骨突；轻微转头辅助区别 C6 | 第七颈椎棘突中心 |

如果标志被软组织遮挡而无法可靠触诊，在 notes 中写 `LANDMARK_NOT_RELIABLY_PALPABLE`；不要根据照片或人口平均值补数字。

## 4. 直接表面测量（A 类）

填写 `v47_subject_measurement_form.csv`。以下项目每项三次：

1. 肩峰 → 肱骨外上髁，左/右。手臂自然下垂，量两标记间直线距离。
2. 肱骨外上髁 → 桡/尺骨茎突中点，左/右。肘伸直、掌心朝身体。
3. 股骨大转子 → 股骨外上髁，左/右。膝伸直、双脚自然平行。
4. 股骨外上髁 → 内外踝中点，左/右。
5. Biacromial breadth：左右肩峰标记之间的直线宽度，不沿背部曲线。
6. ASIS breadth：左右 ASIS 标记之间的直线宽度，不沿腹部曲线。
7. C7 → mid-PSIS：站直，在后侧中心线上量两点直线距离；若只能沿皮肤量，必须把方法写入 notes。
8. Pelvis anterior-posterior depth：ASIS 中点到 PSIS 中点的水平前后深度，优先使用人体测量尺；不能把一侧 ASIS→PSIS 的表面弧长代替。
9. 可选：赤脚身高和体重，仅用于单位/数量级 sanity check，绝不进入关节中心回归或标定 residual。

### 为什么表格还包含肩胛 3D 标志

肩关节内部中心采用 **Meskers et al. (1998)** 的命名回归。该方法要求 AC、AA、TS、AI、PC 五个肩胛标志在同一三维坐标系中的坐标，不能由 biacromial breadth 或一维软尺距离唯一恢复。表格为每侧、每个坐标分量预留三次独立 digitization；没有合格 digitizer 和受训定位者时保持为空，肩关节中心推导会明确 `BLOCKED_MISSING_3D_SCAPULAR_LANDMARKS`，不会退回人口平均值。

肩胛标志：

- `AC`：肩锁关节最背外侧点；
- `AA`：肩峰角，即肩胛冈与肩峰外侧缘相接的后外侧骨点；
- `TS`：肩胛冈内侧根部；
- `AI`：肩胛骨下角；
- `PC`：喙突尖端。

每一次 pass 必须重新触诊五点；同一个 pass 内左右共十个点必须共享同一个 digitizer frame，而且左右的 pass 1/2/3 必须一一对应。CSV 的 `repeat_1_mm`/`repeat_2_mm`/`repeat_3_mm` 对应 pass 1/2/3，不能把三个轴当成三次重复。Meskers 原始方法、五标志要求和坐标误差见 [Meskers et al., 1998](https://pubmed.ncbi.nlm.nih.gov/9596544/) 及 [ISB shoulder protocol](https://media.isbweb.org/images/documents/standards/frans_c.t._van_der_helm_shoulder_protocol.pdf)。

## 5. 鞋具与足部（D 类，rendering only）

填写 `v47_shoe_measurement_form.csv`。记录本次 capture 所穿鞋的品牌、型号、尺码、颜色/识别特征和照片文件名。

- Foot length L/R：赤脚站在纸上承重，从后跟最后点到最长脚趾的水平距离。
- Floor → malleolar midpoint L/R：穿上 capture 鞋并正常系紧，站在硬质水平地面；用直角板量地面到已标记踝中点的垂直高度。
- Rear heel stack：鞋取下，量后跟承重中心处外底接地面到鞋内脚床承托面的垂直厚度。
- Forefoot stack：量第一、第二跖骨头承重区域的同类垂直厚度。
- Heel-minus-forefoot elevation 由工具对每次对应读数作 `rear - forefoot`，操作员不手填结果。

这些项目只控制足/鞋渲染。缺少它们可以阻断 `FOOT_RENDERING`，但不能阻断未使用这些数值的 torso/limb centerline 输入准备。

## 6. 推导约定（操作员不填写内部中心）

### Hip joint centre

使用 Harrington et al. (2007) pelvis-only 回归，原点为 mid-ASIS。身体轴约定：AP 向前为正，ML 从中线向内为正（所以左右髋的外侧分量符号相反），SI 向上为正。令 `PW = ASIS breadth`，`PD = pelvis AP depth`，单位 mm：

```text
AP = -0.24 PD - 9.9
ML magnitude = 0.33 PW + 7.3
SI = -0.30 PW - 10.9
```

也就是髋中心相对 mid-ASIS 位于后方、外侧、下方。参考：[Harrington et al., 2007](https://pubmed.ncbi.nlm.nih.gov/16584737/)；公式和符号的复核表见[近期开放方法比较的 Table 1](https://pmc.ncbi.nlm.nih.gov/articles/PMC12614639/)。模型自身预测误差与重复测量误差分开保存。

### Glenohumeral/shoulder joint centre

使用 Meskers et al. (1998) 五肩胛标志线性回归，版本锁定为论文 Table 2。先由 AA、TS、AI 建立 ISB scapula frame，再将 AC、PC 及各距离代入三个坐标回归。输出 convention、公式、原始坐标引用和论文验证 RMSE（x 2.32 mm、y 2.68 mm、z 3.04 mm）作为模型不确定度的一部分。缺 3D 标志时 fail closed；不把肩峰点、固定“7 cm offset”或 biacromial breadth 当作内部肩中心。

## 7. Capture placement 证据等级

填写 `v47_capture_placement_questionnaire.md` 时遵循：

- `MEASURED_CAPTURE_DAY`：必须有 2026-08-14 当天量测记录或带尺度、可核验的同期影像。
- `PHOTO_DERIVED`：有同期照片/视频，可从已知 enclosure 尺寸或同画面尺度建立有界先验。
- `CALIBRATION_ESTIMATED`：无同期影像，但有明确的绑带方式、enclosure 尺寸及操作员回忆；只生成有界 nuisance prior。
- `MISSING`：连方向、绑带位置或合理边界也无法建立。

普通回忆不能升级成 `MEASURED_CAPTURE_DAY`。现代补拍只能说明硬件共用几何或重演方式，不能证明历史 capture-day placement。

## 8. 完成前检查

- 所有必填长度都有三个独立 mm 读数，或明确留空原因。
- CSV 保留原始读数；没有手填 mean、SD 或 uncertainty。
- 左右没有复制。
- 鞋身份与照片引用能够对应到 capture 鞋。
- 十个节点各自完成 placement questionnaire；共用 enclosure 几何只记录一次。
- 任何不确定记忆都以文字保存，不转换成虚假精确数字。
