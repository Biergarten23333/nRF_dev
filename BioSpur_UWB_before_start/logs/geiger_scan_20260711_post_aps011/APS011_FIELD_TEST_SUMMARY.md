# APS011 现场测试小结 (Geiger MODE_SCAN, 2026-07-11)

**部署内容**：DWM1001C 上启用 APS011 距离偏置修正（`dwt_getrangebias()`, ch5/PRF64）。
- Geiger：APS011 + `GEIGER_ANTENNA_DELAY_OFFSET_MM = 100`（天线延迟 offset）。
- 3 个 wand tag（BS9336/BS955A/BSCCF4）：仅 APS011，无 +100mm。已 OTA。
- 分析工具：`analysis/post_aps011_comparison.py`，复用锁定的 `pg_lib`（阈值/逻辑与 `pg_pipeline.py` 完全一致，pre 自检可复现 `gate_verdict.json`）。

三段扫描（同一套 8-anchor 几何 `system_calibration_20260710_233443`）：
| 扫描 | 固件 | 现场 | LSCAN | 用途 |
|------|------|------|-------|------|
| `…161258_8anchor/scan.log` | 旧(无APS011) | 无人 | 517 | baseline |
| `scan_person.log` | 新(APS011) | **有人**在 BCFG 墙内侧 50–100cm 吃饭，绕人测 | 1025 | 有人 post |
| `scan.log` | 新(APS011) | **清场无人** | 758 | 干净 post |

---

## Test ① — 干净量 APS011（pre-baseline 无人 vs 清场 post 无人）

两段都无人、mostly-LOS，唯一变量是固件（APS011 开/关）。路线不完全一致，但空间/距离覆盖相近。

| 指标 | APS011 前 | APS011 后(清场) | delta |
|------|-----------|----------------|-------|
| per-anchor 均值 mm | 2958 | 3125 | +167 |
| gauge common-mode (截距 a) mm | −100.5 | **+353.7** | **+454** |
| gauge slope (b) % | +3.65 | **−7.01** | **−10.66** |
| LOO \|残差\| 中位数 mm | **158** | **251** | **+93 (变差 59%)** |
| proxy-gate verdict | UNDERPOWERED | NO-GO | — |

**结论：APS011+100mm 过校正（over-correction），且把测距改差了。**
- common-mode 从 −100 **冲过头到 +354**（本应回到 ~0）。
- slope 从 +3.65% **翻成 −7.01%**（修过头约 3×）。
- LOO 残差 **158→251mm**，即修正后各 anchor 距离**彼此更不自洽**——定位精度下降。

---

## Test ② — 隔离"人"的影响（有人 post vs 清场 post，均新固件）

两段都是新固件，唯一变量是现场有没有人。

| 指标 | 有人 post | 清场 post | delta（=人的影响）|
|------|-----------|-----------|------|
| per-anchor 均值 mm | 3201 | 3125 | −76 |
| gauge common-mode mm | +278 | +354 | +76 |
| gauge slope % | −5.46 | −7.01 | −1.56 |

补充：**近人墙 BCFG（\|e\|中位 231mm）并不比远人墙 ADEH（252mm）差**；最差是 E（362mm，远人低角落 = GDOP）。"被人挡"几何下误差反而略低。CIR 也没标记出遮挡。

**结论：人的影响很小（~76mm 截距、~1.5% 斜率），且检测不到明确遮挡特征。** 因此 Test ① 里那些巨大异常**不是人造成的**——人被干净排除。

---

## 拆解 + 根因

- **slope 的 −10.66% 变化全部来自 APS011**（+100mm 是常数，不改斜率）→ APS011 单独就把斜率修过头。
- **common-mode 的 +454 = +100(offset) + 354(APS011 截距贡献)** → **+100mm offset 与 APS011 在重复修同一个 DW1000 偏置（双重校正）**。
- **根因**（正是初始 prompt 已警告的那条，但幅度远超预期）：`dwt_getrangebias()` 内部用 **EVK1000 链路预算（TX −41.3 dBm/MHz、0 dBi 天线）**；**DWM1001C 天线 ~3 dBi**，实际 RSL 高 ~3 dB，导致 datasheet 修正量**系统性偏大**。预估"~2–3cm"，实测斜率过冲 3×。

---

## 我的意见 / 建议

1. **这版 naive APS011 不要信、不要留。** Geiger 和 3 个 wand tag 目前都是过校正状态（tag 虽无 +100mm，但吃到了 −10.66% 的斜率过校正），**测距比原始 raw 更差**。
2. **优先止损**：把 tag 重 OTA 回 pre-APS011、Geiger 去掉修正（`GEIGER_ANTENNA_DELAY_OFFSET_MM` 归 0 + 去掉 getrangebias）。
3. **若要保留 APS011 思路**：改成**缩放/温和版**——按 3 dBi 链路差把修正量整体调小，或退到 **slope-only** 微调；`+100mm` offset 必须**在 APS011 之后重新标定**（现在双重计数，应显著更小甚至 0）。
4. **黄金标准仍是静态已知真距离点（三脚架阶梯）**——只有受控真值才能把"斜率修正"和"天线 offset"干净分开定标。
5. proxy-gate 持续 NO-GO（best \|ρ\|≈0.12, AUC≈0.53）——与距离标定无关，符合预期；即使场里有人，per-位置 CIR 仍预测不了测距误差。

**一句话**：APS011 直接照 datasheet 用在 DWM1001C 上会过校正、把距离改差；先回退，再考虑缩放版或静态定标。

---

## 数据/产物
`logs/geiger_scan_20260711_post_aps011/` 下：`scan.log`(清场)、`scan_person.log`(有人)、`scan_reset_partial.log`(误 reset 的那段)、`cmp_aps011_clean.json`(Test①)、`cmp_person_effect.json`(Test②)。分析脚本 `analysis/post_aps011_comparison.py`。
