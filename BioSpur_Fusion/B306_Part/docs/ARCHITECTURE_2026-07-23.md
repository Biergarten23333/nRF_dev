# BioSpur Fusion 系统架构(2026-07-23 定稿)

本文固化最近一轮讨论的架构决议。它是 IMU/中继大批次 prompt 的
架构基准,也是后续所有固件工作的对照物。

---

## 1. 节点构成(Fusion PCB = 一个体戴节点)

```
┌─ Fusion PCB(体戴节点,编号 BSF####)──────────────────┐
│                                                        │
│  DWM1001C(nRF52832 + DW1000)    JY61P(6轴 IMU)      │
│   · 自研 Alt-SS-TWR UWB 测距       · I2C 从机 0x50     │
│   · TDMA slot 调度(absdeadline)   · 3V3,上拉 3V3     │
│   · BS 号(如 BS065F)              · 已焊,不可断电    │
│        │                                │               │
│        │ UART 460800(96B 帧,tag→B306)│ I2C 400kHz    │
│        │ + strobe(sweep 完成脉冲)     │(B306 主动拉) │
│        ▼                                ▼               │
│  ┌──────────── NINA-B306(nRF52840)────────────┐       │
│  │  · UART RX: GPIO35/P1.01(收 96B 帧)        │       │
│  │  · UART TX: GPIO36/P1.02(下行命令,本批启用)│       │
│  │  · strobe:  GPIO37/P1.03(GPIOTE+TIMER2 捕获)│       │
│  │  · I2C:     GPIO42/P0.26 SDA, GPIO44/P0.27 SCL│      │
│  │  · TIMER2 µs 时基 = 全节点统一时钟           │       │
│  │  · BLE peripheral → Fusion Master             │       │
│  └───────────────────────────────────────────────┘       │
└────────────────────────────────────────────────────────┘
```

关键事实:
- 引脚编号语言:板级文档用 NINA GPIO 号,固件 devicetree 用
  nRF52840 P 号,映射表在 boards/README(GPIO_42→P0.26 等)。
  strobe(P1.03)与 I2C(P0.26/27)不同 port,零冲突。
- tag 的 P0.26 strobe 输出是 nRF52832 侧引脚号,与 B306 的
  P0.26 只是编号巧合,文档中一律标明芯片归属。
- 杆臂:IMU MEMS 封装中心 ↔ UWB 天线几何中心 ≈ 400 mil
  (10.16mm),三维向量从 EasyEDA 设计与实测轴映射闭合后落主机融合配置;相位中心
  绝对位置被天线延迟标定吸收,方向摆动已由 Layer1(46mm)记账。

## 2. 控制拓扑:双路径并行(铁律)

```
【路径 M · Master 路(受保护,原样保留)】
 PC ──USB──► Master_Tag(B120) ──BLE/NUS──► DWM1001C tag
   · TDMA roster/rebalance/auto、CFG、TR;2 接收、OTA(SMP)
   · roster 编排模型:Master 知道各 BS 号,按表点名分发
   · ⛔ 保护条款:本批及以后所有版本完整保留,任何批次不得
     以"中继可用"为由削减;直到操作者明确下令才可移除

【路径 R · 中继路(本批新建)】
 PC ──USB CDC──► DK(52840DK) ──BLE──► B306 ──UART──► tag
   · PC↔DK = DK 原生 USB CDC(主通道:命令入 + 日志出;
     /dev/serial/by-id 解析纪律照旧);J-Link RTT 降为调试
     后备,J-Link 线保留用于烧录/调试
   · IMU 全部控制(在 B306 终结)
   · TAG 前缀命令经 UART 下行中继给 tag
   · 直配模型:命令自带全部参数,tag 立即执行,不查 roster、
     不等 BS 点名(UART 线上收件人唯一)
   · 应答按来源路由:UART 来的从 UART ack 帧回

【并行纪律】
   · 同一时刻一个活动控制者(单一 rig 所有权规矩沿用)
   · tag 日志/应答标注命令来源(BLE/UART)供归因
   · 两路 TDMA 交互(M 配好→R 覆盖→M 重配)行为必须可预期,
     rapid-reconfig 卡 0TX 的老 caveat 在此重点验证
```

Master_Anchor(B120)第三条控制线不变:anchor fleet 的
responder/OTA 管理,与本批无关。

## 3. 数据流

```
UWB raw ranging(每 sweep,10Hz,双通道并行——已实测共存):
  ① DWM1001C ──UART 96B 二进制帧──► B306    (40,581 帧零错背书)
  ② DWM1001C ──BLE/NUS TR;2 文本──► Master_Tag(过夜日志背书)
  语义:每轮即时、CFO 时钟校正、无任何平滑(range_mm,
  v2-clean1 起名实相符);平滑/融合全部归主机侧。
  共存前提:fusion tag CI=437.5ms(CAP 350 units)——不许调快。

IMU(200Hz,B306 主动拉,默认静默):
  JY61P ──I2C 26B 连读(0x34-0x40)──► B306
  · TWIM 中断/DMA 模式,绝不 polling
  · 采样时标 = B306 TIMER2(与 strobe 同源)——δt 塌缩为
    实测 I2C 拉取延迟常数 788µs(400kHz,0x34/26B)。C-R 双速
    回归分解为数据线时 585µs + 寻址/控制线时约 87µs +
    软件固定项约 116µs;后续逐字节相同的镜像以 788µs 为基线,
    同时满足 ±100µs 和 ±15% 才通过。
  · IMU START 前零拉取零发送(和 tag 等 TDMA 同纪律)

上行汇聚(B306 ──BLE notify──► DK ──USB CDC──► PC 日志):
  · kind=1 UWB(184B)/ kind=2 telemetry / kind=3 IMU(新)
  · kind=3:{version,kind,len} + seq + base_TIMER2_ts +
    N×(delta_us + acc + gyro) + temp;无应用层 CRC(house
    style,LL CRC24 兜底);N 默认 2,RATE/BATCH 运行时可调
  · schema 单一共享头 biospur_fusion_ble.h(B306/DK CMake
    共享,非双拷贝)
```

## 4. 命令面(路径 R,寻址 = BSF 板号)

```
DK 本地:      LIST(已连接板清单)
板级:        BSF#### PING / STATUS / REBOOT / COUNTERS [CLEAR]
IMU:          BSF#### IMU START|STOP|RATE=<hz>|BATCH=<n>|
                        STATUS|PROVISION|SELFTEST
TAG(中继):  BSF#### TAG PING|REBOOT|STATUS
              BSF#### TAG CFG id=<n> slot=<s> count=<c>
                        [period=10 active=9 epoch=5000]  ← 直配:
                        DK 生成 tag 原生 CFG TAG=... 行下发;
                        on-air 地址 = 0xB100+id,不单独设
              BSF#### TAG TDMA CLEAR(回 free-run)
              BSF#### TAG RAW <命令行>(逃生舱透传)
```

- B306 校验板号匹配自己(防串扰)。BSF 板号的物理载体
  (NVS/编译期/FICR 派生)按 codex 核查结果定。
- 直配语义(codex 审计定案):tag 从来只认识 `CFG TAG=...`——
  roster/rebalance 是 Master 本地调度命令,从未到过 tag。CFG 本身
  就是参数完备、立即排队的直配接口,tag 侧**无需新 TDMA 分支**;
  中继工作量 = 传输管道(UART RX 组帧,行长 ≥160B,与 96B BSL
  数据帧可区分 + parser 抽象为 parse(line, source, reply_sink),
  应答按源路由)。⚠️ CFG_OK LIVE=1 = 已排队非已发射;强确认另靠
  观测(TR/听诊)。板号寻址基础已存在:B306 的 FICR 派生
  BSF%04X(与 BLE 名同源);人工 override 推迟到有需求。
- REBOOT 双层意义:B306 REBOOT 同时是 TIMER2 回绕债
  ("每次采集前重启")的远程执行手段;TAG REBOOT 补上
  以前只能断电/JLink 的一条独立复位通道。

## 4a. 启动/停止顺序(强制,session 脚本按此实现)

原则:**UWB 先起且被证实,IMU 最后起、最先停(last-on /
first-off)**。两条流虽在架构上独立(不同外设:UARTE+strobe
vs TWIM;不同发送路径),但顺序 + 步间验证门把"互相卡死"
从"不应发生"变成"构造上不可能发生":

```
START(每步有 ack/证据,失败即停在该步,绝不带病继续):
 S1  BSF#### PING                 → 活性 + 版本
 S2  BSF#### STATUS               → IMU provisioning 核对(只读),
                                    UART/strobe 计数器基线记录
 S3  BSF#### TAG CFG ...          → CFG_OK LIVE=1(仅=已排队)
 S4  等待并证实 tag 实际发射       → strobe/UART 帧计数在涨、
                                    频率≈10Hz(LIVE=1 不算数,
                                    这一步才是 UWB "起来了")
 S5  BSF#### COUNTERS CLEAR       → 清零,给共存监控干净基线
 S6  BSF#### IMU START            → 最后一个起
 S7  10s 共存哨兵                 → UWB 帧率不降、strobe↔帧
                                    配对零 orphan、imu_seq 零缺口
                                    → 全绿才宣布 session RUNNING;
                                    S7 失败 → 自动 IMU STOP,
                                    UWB 保持,报告后停下

STOP(反序,IMU 最先停):
 T1  BSF#### IMU STOP             → I2C 拉取停、kind=3 停发
 T2  收尾统计读取(COUNTERS)
 T3  TAG TDMA CLEAR(可选,按场景)
```

设计根据:
- IMU START 前(S1-S5)零 I2C 事务、零 kind=3 发送(默认静默
  纪律)——UWB 建立阶段在完全无 IMU 活动的环境里完成,排除
  "IMU 先起干扰 UWB 建立"的可能
- S4 是硬门:LIVE=1 是排队回执不是发射证据;不见 strobe/帧
  计数增长就不放行 IMU——杜绝"UWB 没真起来、IMU 流先跑、
  误判是 IMU 卡了 UWB"的归因泥潭
- S7 哨兵 + 自动回退:共存异常 10 秒内暴露并自动摘除 IMU,
  UWB 数据流不陪葬;30 分钟共存 gate(F3)是它的长时版
- 卡死防护的架构底座(顺序之外的保证):B306 侧 TWIM 中断/
  DMA 模式禁 polling(I2C 事务期 CPU 空闲,strobe 捕获走
  GPIOTE+PPI 硬件通路不经 CPU)+ tag 侧本批 ranging 路径零
  改动——即使顺序被人为打乱,硬件层面也没有共享资源可卡;
  顺序是第二道保险和归因纪律,不是唯一防线

## 5. OTA 体系(无外部 reset,全软件)

```
镜像搬运:PC → Master_Tag → BLE/SMP → tag slot-1
                              (路径 M,现有,不动)
触发/指挥:路径 R 的 TAG REBOOT / 状态查询(新增的独立通道)
交换:tag sys_reboot → MCUboot swap(纯软件,无引脚)
```

- ⛔ 不经中继链传镜像:不建 UART-SMP 传输层。理由:速度不占优
  (460800 上限)、OTA 期间污染 B306 时序、且把两个独立故障域
  焊死成一个(现在 tag OTA 挂了 Master 路还能救)。
- 边界诚实:tag 应用彻底死机时,UART REBOOT 与 BLE 同样无效,
  兜底仍是断电/JLink/看门狗。
- B306 自身固件更新:mcumgr/SMP over BLE(经 DK),既有路径。

## 6. 时间与标定体系

- 统一时基:B306 TIMER2(µs)。UWB 观测时刻 = strobe 硬件捕获
  (GPIOTE+PPI,CPU 无关);IMU 时刻 = 拉取发起的 TIMER2 戳。
- δt(UWB↔IMU):同 MCU 同时钟,残余 = I2C 拉取常延迟。2026-07-25
  C-R 软件标定在 400kHz 得到 788µs;其中软件固定项的 pre-TWIM
  setup 与 post-TWIM completion/return 分割无法靠软件测出,所以
  时标修正仍有 [0,116]µs 的位置不确定区间。它约占 0–5ms
  异步刷新锯齿满幅的 2.32%,必须记录但不是主导项。双时标
  (到达+帧内)留在记录里供在线估计。
- 杆臂 r:EasyEDA/实测闭合的三维向量 → 主机融合配置(符号约定:IMU→天线,
  IMU 体系;集成后原地纯旋转自检验符号)。
- yaw(世界系坐标对齐):6 轴无磁,IMU 自身永远不知道朝向;
  唯一的世界方向参考是 UWB 位移。机制:载体真实移动一段
  (走 2-3 步,位移 ~1.5m ≫ UWB 散点 25-50mm,方向不确定度
  ≈2°)→ UWB 给出世界系位移方向,IMU 给出体坐标系加速方向,
  两方向之差 = yaw,一次钉出。静止时 UWB 散点各向同性无方向,
  滤波器对 yaw 的更新增益趋零——乱飘被数学上自动无视,不污染。
  操作含义:session 开头"站 1-2s(重力定 roll/pitch)→ 正常
  走几步(yaw 收敛)→ ready",写入使用流程;yaw 方差过门槛前
  姿态相关输出标记不可信。
  注意与 δt 区分:时间轴对准靠 B306 TIMER2 共同时钟(strobe
  捕获 + I2C 拉取戳同源),零运动需求;运动激励只为 yaw。
- ⚠️ 已知债压在时基底下:B306 TIMER2 32-bit µs 回绕
  (uptime 71.58min 必死,wrap 处理代码存在未验证)。
  当前拐杖 = 每次采集前 REBOOT(现可远程);真修 = P6 独立
  专项,产品化前必做。

## 6a. 多 Fusion 节点时间对齐(本代决议)

本代选择 **UWB TDMA 全局 superframe index + 各节点 TIMER2 线性拟合**。
节点可以任意时刻上电;上电偏移只进入拟合截距。主机连续拟合
`t_TIMER2 = a + b*N_global`,把 UWB 和 IMU 都离线映射到公共轴。不要
增加同步线,也不要新增"同步命令"。

2026-07-25 源码审计发现一个实现缺口:现有 96B 帧的 `sweep` 是 tag
本地 `uint32_t` 计数器,启动归零;CFG 的 `EPOCH` 是到共同未来 deadline
的相对毫秒延迟,不是 Master 分配的 epoch 编号。因此目标架构已定,
但 `N_global` 尚不存在。最小修复是在 CFG 增加同轮一致的
`SUPERFRAME_BASE`,保留 tag 本地计数供内部维护,只把公开 `sweep`
改为 `base + schedule_cycle`;复用原 `uint32_t sweep`,96B schema 不增大。
Master 重启若要求同一 capture 连续,还需持久化 next base 或明确切分
time-domain segment。

现有单机 5 分钟/3,001 sweep 拟合得到 `b=99.993584 ms`,
`-64.155 ppm`,残差 σ=97.189µs、|p95|=130.004µs、
|max|=288.681µs,满足当前 P4 `<10ms and constant` 的量级要求。
`σ/√N` 给出的 5min/30min 理想截距误差是 1.774/0.724µs,但残差
lag-1=-0.381,不满足 IID 假设;负相关使均值比 IID 更快收敛,并呈现
absolute-deadline 调度的“本次晚、下次早”纠偏特征。保守工作边界仍
采用实测最大 0.289ms。与
TIMER2-vs-DW 晶振 `-12.376±0.512ppm` 不一致,因为 superframe 斜率
还包含 tag 的 RTC/LF 调度时钟。归档长跑在 wrap 前只有 30.991min
连续权威;26 个滑动 5min 拟合仅移动 0.943ppm peak-to-peak,未显示
足以威胁 10ms/30min 门限的漂移,但不能冒充完整一小时温漂验证。

明确拒绝:

- host BLE sync:CI=437.5ms 下到达时刻先被 connection event 量化,
  再叠 USB/host 调度抖动,远差于当前 <0.3ms 的拟合残差;
- UTC sync:UTC 每 session 只记一次,仅作文件名/外部粗关联的 label,
  永远不称为同步;
- 用时钟直接对 Vicon/video:跨系统靠一次清晰的 start/stop 动作
  event marker。

下一代改用 DW3000 空口 beacon 的 radio timestamp 获取 sub-µs 节点
间对齐。本代 superframe fit 是过渡方案,不是永久架构。完整证据:
`B306_Part/logs/homecoming_20260725/multiunit_alignment_20260725/`。

## 7. 已知债清单(并列记录,不在本批)

1. B306 TIMER2 回绕真修 + P6 迁移验证(80min 专项)
2. 签名 key = NCS 公开样例 key(诊所前必换,全 fleet SWD 事件)
3. PC 生产 parser 缺位(host/pc 占位;当前 RTT 文本日志 + 离线
   脚本)
4. notify_drop=617 历史成因不可归因(计数器本批拆分后新数据
   可归因)
5. 两个 tag fork(freeze / fusion)合并债;APOS 移除 = 分歧点
   之一(本批兑现,已记账)
6. tag 公共 `sweep` 仍是本地计数;实现 Master 分配的
   `SUPERFRAME_BASE`,再做两节点同 index 残差直接相减

## 8. 终态对照(box 产品形态)

```
┌─ Box ──────────────────────────────────────────┐
│ 主机 CPU(编排层,今天的 session 脚本 = 其原型)│
│   ├─ 控制器①:anchor fleet 管理(今 Master_Anchor)│
│   ├─ 控制器②:tag 直连管理(今 Master_Tag,受保护)│
│   └─ 控制器③:Fusion 节点网关(今 DK)           │
└────────────────────────────────────────────────┘
  多控制器 = 架构事实,不是临时脚手架;
  统一发生在编排层,无线层不造新路;
  台架三件套 = box 的 1:1 原型。
  reverse 落地后:tag 数据离开 BLE,②的控制职责仍在(变轻)。
```

---

## 本批(IMU + 中继)范围一句话

B306:JY61P provisioning/boot-verify/I2C 拉取/kind=3/控制
characteristic/UART TX 中继/诊断修缮;DK:USB CDC 主通道
(命令台+日志出,RTT 调试后备)/kind=3
解析;tag:APOS 删除 + UART RX/应答路由管道(两个独立 commit,
v2-relay1,一次 OTA + 计时);验证:慢转/65s 静置/2g 边界/重复
样本/三流共存 30min/双路径互不干扰/session 脚本。
UWB ranging 路径、96B 帧、Master 路、物理层:零改动。
