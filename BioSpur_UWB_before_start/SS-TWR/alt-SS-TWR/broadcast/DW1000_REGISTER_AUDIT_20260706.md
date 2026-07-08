# DW1000 寄存器使用审计 — Broadcast Alt-SS-TWR 自研代码 (2026-07-06)

**被审代码(自研):** `src/` — 主要 `ss_twr_init.c`(tag/发起端, 6343 行)、`ss_twr_resp.c`(anchor/应答端)、
`ss_twr_anchor_init.c`、`uwb_bringup.c`。
**驱动:** 标准 Decawave decadriver `drivers/dw1000/`(未改动的寄存器抽象层)。
**方法:** grep 全量 `dwt_*` 调用 + 直接寄存器访问,逐一回溯 driver 源码确认每个寄存器的配置来源。

---

## 0. 前提与全局结论

你的代码几乎全部通过 `dwt_*` API 访问寄存器,直接写寄存器仅限
`SYS_STATUS` / `RX_FINFO` / `SYS_CFG` / `SYS_CTRL`。判断"某寄存器是否配置"须区分三种来源:

* **(a)** 你显式调用的 API;
* **(b)** `dwt_configure()` 作为副作用自动写入(算"已配置");
* **(c)** 需要独立 API 但你从未调用(算"缺失")。

**一句话结论:** PHY/信道/AGC/DRX/PLL/LDE 链路非常干净(`dwt_configure` 全包),双缓冲/RXAUTR 的处理是
**有 A/B 实验依据的教科书级别**。唯一 CRITICAL 是 **发射 RF 链路(`TX_POWER` + `TC_PGDELAY`)完全未配置**,
并连带 **Smart TX Power 失控** —— 对 SS-TWR 测距偏差与频谱合规都有实打实影响。

---

## 1. 寄存器使用清单

### 1a. 直接寄存器读写(自研代码显式操作)

| 寄存器 (hex) | 子偏移 | R/W | 写入值 | 位置 |
|---|---|---|---|---|
| `SYS_STATUS` 0x0F | — | W | 状态清除(TXFRS/RXFCG/ALL_RX_ERR/ALL_RX_TO/AFFREJ) | ~25 处:`src/ss_twr_init.c:4765`, `:5604`; `src/ss_twr_resp.c:1319`; `src/ss_twr_anchor_init.c:141` |
| `SYS_STATUS` 0x0F | — | R | 轮询 TXFRS/RXFCG/err/to;HPDWARN(hi16) | `src/ss_twr_init.c:5346`, `:5977`; `src/ss_twr_resp.c:577`(HPDWARN), `:1027` |
| `RX_FINFO` 0x10 | — | R | RXFLEN 帧长 | `src/ss_twr_init.c:5365`, `:6012`; `src/ss_twr_resp.c:1086`; `src/ss_twr_anchor_init.c:284` |
| `SYS_CFG` 0x04 | — | R/M/W | 置/清 `RXAUTR`(bit29) | `src/ss_twr_init.c:4838-4845`(默认 OFF) |
| `SYS_CTRL` 0x0D | HRBT 0x03 | W | `HRBPT=1`(双缓冲翻转) | `src/ss_twr_init.c:4893`(仅双缓冲编译开关下) |

### 1b. 经 `dwt_*` API 触及的寄存器(API → 寄存器映射)

| API(调用处) | 实际写/读的寄存器 | 值 |
|---|---|---|
| `dwt_initialise(DWT_LOADUCODE)` `uwb_bringup.c:98` | DEV_ID(R,校验)、软复位、OTP→`LDOTUNE`(W)、OTP→`FS_XTALT`(W)、LDE ucode 加载、`AON_CFG1`=0、读回 `SYS_CFG`/`TX_FCTRL` | — |
| `dwt_configure(&cfg)` ×3 | `SYS_CFG`(PHR/RXM110K)、`LDE_REPC`、`LDE_CFG1/2`、`FS_PLLCFG`、`FS_PLLTUNE`、`RF_RXCTRLH`、`RF_TXCTRL`、`DRX_TUNE0b/1a/1b/2/4H`、`DRX_SFDTOC`、`AGC_TUNE1/2`、`USR_SFD`、`CHAN_CTRL`、`TX_FCTRL` | ch5, PRF64M, PLEN128, PAC8, code9/9, nsSFD=1, 6.8M, PHR_STD, sfdTO=**129** |
| `dwt_settxantennadelay` | `TX_ANTD` (0x18) | **16436** |
| `dwt_setrxantennadelay` | `LDE_RXANTD` (0x2E:1804) | **16436** |
| `dwt_setpanid` / `dwt_setaddress16` | `PANADR` | PAN + short addr |
| `dwt_enableframefilter` | `SYS_CFG` FFEN/FFAD | **0**(默认关) |
| `dwt_setleds` | `GPIO_MODE` + `PMSC_LEDC` | LED 使能 |
| `dwt_setrxtimeout` | `RX_FWTO` + `SYS_CFG.RXWTOE` | 动态/0 |
| `dwt_setpreambledetecttimeout` | `DRX_PRETOC` (0x27:0x24) | **0**(禁用) |
| `dwt_setrxaftertxdelay` | `ACK_RESP_T.W4R_TIM` | 140 uus |
| `dwt_setdelayedtrxtime` | `DX_TIME` (0x0A) | 计算值 |
| `dwt_setdblrxbuffmode(0)` `ss_twr_init.c:5686` | `SYS_CFG.DIS_DRXB` | **置1=单缓冲** |
| `dwt_starttx(… \| DWT_RESPONSE_EXPECTED)` | `SYS_CTRL` TXSTRT/TXDLYS/**WAIT4RESP** | 同一次写 |
| `dwt_readtxtimestamplo32` / `dwt_readrxtimestamp*` | `TX_TIME`(R) / `RX_TIME`(R) | — |
| `dwt_readsystimestamphi32` | `SYS_TIME`(R) | — |
| `dwt_readcarrierintegrator` ×5 | `DRX_CARRIER_INTEGRATOR`(R) — **CFO** | — |
| `dwt_readdiagnostics`(`dwt_rxdiag_t`)×9 | `RX_FQUAL`(STD_NOISE/FP_AMPL2/3/CIR_PWR)、`RX_TIME.FP_INDEX`、`LDE_PPINDX/PPAMPL/THRESH`、`RX_FINFO.RXPACC` | — |
| `dwt_readaccdata` ×2 | `ACC_MEM`(R),driver 内部强开 FACE+AMCE 时钟 | CIR |
| `dwt_readtempvbat(1)` ×2 | `RF_CONF` bias、`TC_SARC/SARL`(R) | **raw** |

---

## 2. 分类 A–N 缺失分析

图例:✅ 显式配置 · ⚠️ 依赖默认(可接受或需注意) · ❌ 完全未用 · 🔍 用了但值/用法需复核

| 类 | 项 | 状态 | 说明 |
|---|---|---|---|
| **A** 初始化 | DEV_ID 校验 | ✅ | `uwb_bringup.c:76` 对 `0xDECA0130` |
| | LDE ucode 加载 (LDELOAD) | ✅ | `dwt_initialise(DWT_LOADUCODE)` → `_dwt_loaducodefromrom` |
| | AGC_TUNE1/2 | ✅ | `dwt_configure`(`deca_device.c:764-765`) |
| | AGC_TUNE3 | ⚠️ | driver 不写,复位默认(手册认为默认 OK) |
| | DRX_TUNE0b/1a/1b/2/4H | ✅ | `dwt_configure`(729-753) |
| | RF_RXCTRLH / RF_TXCTRL | ✅ | `dwt_configure`(721/725) |
| | TC_PGDELAY | ❌ | **需 `dwt_configuretxrf`,从未调用 → 复位默认** |
| | FS_PLLCFG / FS_PLLTUNE | ✅ | `dwt_configure`(717-718) |
| | LDE_CFG2 | ✅ | `_dwt_configlde` → PRF64 用 `LDE_PARAM3_64` |
| | LDE_REPC | ✅ | `dwt_configure`(712),按 rxCode 查表 |
| **B** 信道/PHY | CHAN_CTRL / TX_FCTRL / SYS_CFG | ✅ | `dwt_configure`(783/787/709) |
| **C** 功率 | TX_POWER | ❌ | **需 `dwt_configuretxrf`,从未调用 → 复位默认** |
| | DIS_STXP(Smart Power) | 🔍 | 从未触碰 → **默认=0=Smart Power 开**,且无固定功率托底 |
| **D** 时间戳 | TX_ANTD | ✅ | 16436 |
| | LDE_RXANTD | ✅ | 16436(与 TX **分别**设置,但值相同) |
| | RX_TIME / TX_TIME 读取 | ✅ | `dwt_readrx/txtimestamp*` |
| **E** 时钟偏移 | RX_TTCKI / RX_TTCKO | ⚠️→✅等价 | 未用这两个,但用 `dwt_readcarrierintegrator`(DRX 载波积分器)——DW1000 推荐的 CFO 等价方式。🔍 是否用于测距补偿待确认 |
| **F** RX 质量 | RX_FQUAL / LDE_PPINDX/PPAMPL/THRESH | ✅ | `dwt_readdiagnostics` 广泛使用 |
| **G** CIR | ACC_MEM 读取 | ✅ | `dwt_readaccdata` |
| | PMSC FACE+AMCE 使能 | ✅(driver) | `dwt_readaccdata` 内 `_dwt_enableclocks(READ_ACC_ON/OFF)` 自动处理 |
| **H** 事件计数器 | EVC_CTRL / 12 个计数器 | ❌ | `dwt_configeventcounters`/`dwt_readeventcounters` **从未调用** |
| **I** 错误监控 | HPDWARN | ✅ | `ss_twr_resp.c:577`(延迟 TX)+ driver 内检查 |
| | RXOVRR | ✅ | 通过 `ALL_RX_ERR` 清除 + TQ 遥测监控(见 `ss_twr_init.c:4816` 注释) |
| | RFPLL_LL / CLKPLL_LL | ❌ | **PLL 失锁位从不读取/监控** |
| | TXBERR | ⚠️ | 未单独监控 |
| **J** 电源管理 | AON / SLEEP / DEEPSLEEP / SNIFF / ATXSLP/ARXSLP | ❌ | **完全未用**(`dwt_configuresleep`/`entersleep`/`setsniffmode` 全无) |
| **K** 温/压 | TC_SARC/SARL/SARW | 🔍 | `dwt_readtempvbat` 读了 **raw**,但 OTP 参考(tempP/vBatP)未加载 → 无法换算绝对 °C/V |
| | ONW_RADC | ❌ | 未用(唤醒时自动 SAR) |
| **L** GPIO/LED | GPIO_MODE / PMSC_LEDC | ✅ | `dwt_setleds(DWT_LEDS_ENABLE)` |
| **M** 晶振 | FS_XTALT / OTP XTAL trim | ✅(driver) | `dwt_initialise` 自动从 OTP 0x1E 加载出厂 trim,0 则用 MIDRANGE |
| **N** 高级 | 双缓冲 (DIS_DRXB+HRBPT) | ✅ | 有意关闭,有实验依据(见 §3.2) |
| | Frame Filtering | ✅ | 有意关闭(broadcast 混杂接收) |
| | WAIT4RESP | ✅ | `DWT_RESPONSE_EXPECTED`,driver 折进 TXSTRT 同写 |
| | Auto-ACK | ❌ | 未用(SS-TWR 不需要) |
| | 延迟 TX/RX | ✅ | `dwt_setdelayedtrxtime` + `DWT_START_TX_DELAYED` |
| | 扩展帧模式 | ⚠️ | PHR_STD(标准帧),有意 |

---

## 3. 14 个暗坑逐条核查

**3.1 [WARNING] DIS_STXP=0 → Smart TX Power 默认开启**
全代码无 `dwt_setsmarttxpower`,`dwt_configure` 不动此位 → `SYS_CFG.DIS_STXP`(bit18)=复位值 0 = **Smart Power 开**;
叠加 `TX_POWER` 未配置。→ 6.8Mbps 短帧被自动 boost,不同帧长功率不同 → 时间戳/测距的功率相关偏差,**未受控**。
**建议:** `dwt_configuretxrf()` 设 ch5 手册值 + `dwt_setsmarttxpower(0)` 固定功率。

**3.2 [OK ✅] DIS_DRXB=0 → 双缓冲**
`dwt_setdblrxbuffmode(0)` 显式置 `DIS_DRXB=1`(单缓冲)。`ss_twr_init.c:4848-4873` 有完整 A/B 记录
(2026-06-28:双缓冲反而 RXOVRR 26-85%、拉低健康 tag)。**有意识、有数据支撑,处理得极好。**

**3.3 [OK ✅] LDE ucode 初始化加载**
`dwt_initialise(DWT_LOADUCODE)`,SPI 先慢后快(`uwb_bringup.c:89-107`)。标准正确。

**3.4 [OK ⚠️] LDE 参数集选择**
`_dwt_configlde` 写 `LDE_CFG1=LDE_PARAM1`(NTM 默认)、PRF64 → `LDE_CFG2=LDE_PARAM3_64`。即 **Default 集**,
未选 Length64/Tight。对 PLEN128/PRF64 是标准正确的默认。

**3.5 [OK ✅] 读 CIR 前 FACE=1/AMCE=1**
`dwt_readaccdata` 内部 `_dwt_enableclocks(READ_ACC_ON/OFF)` 自动强开/复位累加器时钟。读法(`len+1` 含 dummy
字节 `ss_twr_init.c:864`)也正确。

**3.6 [OK ✅] DRX_SFDTOC 按前导长度调整**
`sfdTO=129`。PLEN128 + nsSFD(8符号)+ PAC8:128+8+1−8=**129**,精确匹配,非默认 4096。

**3.7 [OK ✅, 含 INFO] TX_ANTD 与 LDE_RXANTD 分别设置**
两者分别调用、各写各寄存器,无混淆。**但**都=**16436**(Decawave 官方示例默认值)→ 见下方 WARNING(天线延迟未器件级校准)。

**3.8 [OK ✅] WAIT4RESP 与 TXSTRT 同一次 SPI 写**
`dwt_starttx`(`deca_device.c:2779/2803`):`temp |= WAIT4RESP` 后与 `TXSTRT` 一次 8-bit 写出。
你用 `DWT_RESPONSE_EXPECTED`(`ss_twr_init.c:5960`)正确触发。

**3.9 [INFO ❌] 事件计数器使能 (EVC_EN)**
从未 `dwt_configeventcounters(1)` → 12 个计数器全程不可用。见 §4。

**3.10 [WARNING ❌] PLL 失锁监控**
`SYS_STATUS.CLKPLL_LL`(bit25)/`RFPLL_LL`(bit24) 从不读取。长时间运行/温漂下的 PLL 抖动无告警,
会静默污染时间戳。**建议:** 轮询掩码纳入这两位,计一个健康计数。

**3.11 [N/A — driver 覆盖] PKTSEQ=0xE7**
LDE 加载/累加器读时对 `PMSC_CTRL0` 序列位的操作,由 `_dwt_loaducodefromrom` / `_dwt_enableclocks` 在 driver
内完成,非应用职责。走 `DWT_LOADUCODE` 路径已覆盖。

**3.12 [N/A] LDE_REPC 在 110kbps 除以 4**
用 **6.8Mbps**,不触发。(该 driver 对 110k 是 `>>3`(÷8),`deca_device.c:697`;与你无关。)

**3.13 [OK ✅ — driver 覆盖] FS_XTALT 从 OTP 加载**
`dwt_initialise`(`deca_device.c:242-257`)读 OTP 0x1E,非 0 则 `dwt_setxtaltrim`,为 0 用 `FS_XTALT_MIDRANGE`。
DWM1001 模块出厂已写 → 自动生效。

**3.14 [OK ✅] RXAUTR 在当前缓冲模式下的行为**
`RXAUTR` 默认 OFF(单缓冲 + 手动重装)。`ss_twr_init.c:4818-4830` 记录 2026-06-27 falsify:单缓冲开 RXAUTR
会在第一帧后收不到 2..N 应答(6 tag 全塌到 rank-0)。错误/超时路径始终手动 `dwt_rxreset+rxenable`。逻辑自洽。

---

## 4. 功能利用率评估(DW1000 有、代码未用)

| 未用功能 | 对 Alt-SS-TWR 的价值 | 建议 |
|---|---|---|
| **载波频偏 CFO**(已读,未确认补偿) | **高** — SS-TWR 对钟差极敏感,`range_bias ≈ −CFO_ppm × T_reply × c`。已 `dwt_readcarrierintegrator`,需确认是否代入距离修正 | 🔍 若尚未:`d_corr = d − (clockoffset_ppm/1e6)·Dresp·c`。**当前架构下提升精度最直接的一步** |
| **RX_TTCKI/TTCKO** | 低(与上者等价) | 不必增加 |
| **事件计数器 EVC** | **中** — 长跑健康监控,量化丢帧根因(CRC 坏 vs 溢出 vs 前导超时) | 建议开启,补齐 TQ 遥测里 "clean non-detection" 的归因 |
| **PLL 失锁位** | 中 — 稳定性告警 | 纳入 status 轮询(见 3.10) |
| **温/压绝对值 + 温补** | **中高** — memory 明确提到温度补偿;当前只有 raw SAR 码 | `dwt_initialise` 加 `DWT_READ_OTP_TMP\|DWT_READ_OTP_BAT`,用 `dwt_convertdevicetemperature` 换算 |
| **DRX_PRETOC 前导检测超时** | 中(省电)——当前=0(禁用) | 电池 tag 场景可设为略大于前导长度,未检到前导即早退 |
| **SNIFF / SLEEP / DEEPSLEEP / AON** | **高(仅电池节点)** | 电池 tag 可获数量级功耗改善;常供电则可不做 |
| **AGC_STAT1**(增益) | 低-中 | 现有 FP/CIR 诊断已足够 |

---

## 5. 按严重度汇总的重点发现

```
[CRITICAL]
[寄存器]: TX_POWER (0x1E) + TC_PGDELAY (0x2A:0x0B)
[当前状态]: dwt_configuretxrf() 在整个 src/ 树中从未被调用;dwt_configure 不写这两个寄存器,
           => 两者停留在上电复位默认值,而非 DW1000 手册 ch5/PRF64 推荐值。
[预期/建议]: 上电配置后调用 dwt_configuretxrf(),PGdly 用 ch5 推荐(≈0xC0),
           power 用 ch5/64M 手册值(手动模式建议 0x0E082848 一类,并关 Smart Power)。
[影响]:  (1) 合规:PG delay 决定发射脉冲带宽/频谱形状,错值可能超出监管频谱掩模;
        (2) 精度:输出功率非标称,跨设备不一致,first-path 幅度差异带入测距偏差;
        (3) 一致性:每台板子 TX 行为取决于芯片复位态,批间不可复现。
```

```
[WARNING]
[寄存器]: SYS_CFG.DIS_STXP (bit18) — Smart TX Power
[当前状态]: 从未 dwt_setsmarttxpower();默认位=0 => Smart Power 开;叠加 TX_POWER 未配置。
[预期/建议]: 测距场景显式 dwt_setsmarttxpower(0) + 固定 dwt_configuretxrf();
           如确要用 Smart Power,则必须先写正确的 boost/base 功率表。
[影响]: 6.8M 短帧被自动 boost,帧长不同 => 发射功率跳变 => 时间戳/测距的功率相关偏差,不可控。
```

```
[WARNING]
[寄存器]: TX_ANTD (0x18) / LDE_RXANTD (0x2E:1804)
[当前状态]: 三角色一律 16436 / 16436(Decawave 官方示例默认值,非本硬件校准值)。
[预期/建议]: 做一次器件级(至少设计级)天线延迟校准,分别标定 TX/RX;或用已知基线反解总延迟。
[影响]: 天线延迟每 ±1 单位 ≈ ±15.65 ps ≈ ±4.7 mm;用示例默认值通常带来数 cm 级恒定距离偏移。
        因所有节点同值,表现为系统性偏置(可被单点标定吸收,但绝对精度受限)。
```

```
[WARNING]
[寄存器]: OTP 温/压参考 (tempP@23C / vBatP@3V3) + TC_SARL
[当前状态]: dwt_initialise(DWT_LOADUCODE) 未带 DWT_READ_OTP_TMP/BAT;
           dwt_readtempvbat() 只取 raw SAR 码(存 tag_temp_raw/vbat_raw)。
[预期/建议]: initialise 加 DWT_READ_OTP_TMP|DWT_READ_OTP_BAT,再用 dwt_convert* 换算绝对值。
[影响]: 无法得到绝对温度/电压,做不了温度补偿(与 roto/温补目标相关);仅能做相对趋势监控。
```

```
[INFO]
[寄存器]: SYS_STATUS.CLKPLL_LL(25) / RFPLL_LL(24) 未监控;EVC_CTRL(0x2F) 未使能
[当前状态]: PLL 失锁位从不读取;12 个事件计数器从不使能/读取。
[预期/建议]: 轮询掩码纳入 PLL_LL;dwt_configeventcounters(1) + 周期读 EVC。
[影响]: 缺少长跑健康信号;无法定量归因丢帧(CRC 坏/溢出/前导超时),诊断只能靠间接遥测。
```

```
[INFO — 建议增强,非缺陷]
[寄存器]: DRX_CARRIER_INTEGRATOR (0x27) — 已读取
[当前状态]: dwt_readcarrierintegrator() 已在 init/resp/anchor 三处读取。
[预期/建议]: 确认 CFO 是否代入 SS-TWR 距离补偿;若否,加上是当前精度最高性价比的改进。
[影响]: SS-TWR 距离偏差 ∝ 收发钟差 × 回复时延;补偿后可消除随温漂的慢变距离漂移。
```

---

## 6. 优先级动作清单

按对 **测距精度 + 合规** 的影响排序:

1. **(CRITICAL)** 加 `dwt_configuretxrf()` 配 ch5 的 `TX_POWER`+`TC_PGDELAY`,并 `dwt_setsmarttxpower(0)`。
   一次修复同时解决合规、功率一致性、Smart Power 失控三件事。
2. **(WARNING)** 做天线延迟校准,替换示例默认 16436。
3. **(增强)** 确认/加上 CFO 距离补偿(carrier integrator 已经在读)。
4. **(增强)** `initialise` 带 OTP 温压参考做温补;开 EVC + PLL_LL 健康监控。

**正面结论:** PHY/信道/DRX/AGC/PLL/LDE 全链路、双缓冲/RXAUTR、WAIT4RESP、SFDTOC、CIR/FACE-AMCE、
XTAL trim、LDE 加载这些最容易踩的坑全都对了,其中双缓冲/RXAUTR 的取舍还带完整 A/B falsification 记录——
代码质量明显高于典型自研 DW1000 工程。真正风险集中在 **发射 RF 配置这一处**。

---

## 附:与 APS011 range-bias 审计的交叉引用

本审计聚焦寄存器配置。ranging 距离修正侧的暗坑(`dwt_getrangebias` 定义但零调用、APS011 未启用)
详见 `handoff_scripts_20260704/DIAG_SIGMA_MAP_RESULTS_20260706.md` §5b / §5b-REVISED —— 结论:
APS011 slope 是真实的 sub-%~few-% **scale** 污染,与本处 TX 功率暗坑相互独立。
