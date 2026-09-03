"""Offline Root-R4 viewer and compact review video."""
from __future__ import annotations

import html
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FFMpegWriter
import numpy as np


def build_viewer(output: Path, payload: dict) -> Path:
    data = json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>BioSpur C1 Root-R4 review</title>
<style>
body{{margin:0;background:#07111f;color:#e6edf7;font:15px system-ui,sans-serif}}header{{padding:24px 30px;background:#0d1b2d;border-bottom:1px solid #29415f}}
h1{{margin:0 0 7px;font-size:25px}}.warning{{color:#ffcc66;font-weight:700}}main{{display:grid;grid-template-columns:1fr 1fr;gap:18px;padding:20px}}
section{{background:#0d1b2d;border:1px solid #29415f;border-radius:10px;padding:18px}}h2{{font-size:17px;margin:0 0 12px}}table{{width:100%;border-collapse:collapse}}
td,th{{text-align:left;padding:7px;border-bottom:1px solid #233a55}}.pass{{color:#70e1a1}}.fail{{color:#ff8d8d}}canvas{{width:100%;height:260px;background:#081522}}
footer{{padding:20px 30px;color:#aabbd0}}code{{color:#9dd7ff}}</style></head>
<body><header><h1>BioSpur C1 Root-R4 — raw range + T4 evidence</h1>
<div class="warning">EXPERIMENTAL ONLY · REAL C1 INTERNAL CONSISTENCY · NO EXTERNAL ACCURACY · FRAME NOT AUTHORIZED</div></header>
<main><section><h2>Evidence classes</h2><table id="classes"></table></section>
<section><h2>Common-frame candidates</h2><table id="frames"></table></section>
<section><h2>Frame stability</h2><canvas id="plot" width="720" height="300"></canvas></section>
<section><h2>Line health and boundaries</h2><table id="health"></table></section></main>
<footer>Raw and T4 layers use exact lineage. T4 and its constituent ranges never appear as independent factors. CIR was not used. M1 remains frozen. Counterfactual transforms are diagnostics only.</footer>
<script>const D={data};
function rows(id,values){{document.getElementById(id).innerHTML=values.map(v=>`<tr><th>${{v[0]}}</th><td>${{v[1]}}</td></tr>`).join('')}}
rows('classes', [['Synthetic truth',D.synthetic],['Real C1',D.real],['Authorized frame',D.authorized],
['Unauthorized counterfactual','inverse / 90° / 180° / reflection / free scale — diagnostic only'],
['T4-only','baseline/diagnostic'],['Raw-range-only','active raw likelihood diagnostic'],
['Lineage-safe hybrid','disjoint-epoch replacement; frame not authorized']]);
rows('frames',D.frames.map(x=>[x.layer,`${{x.yaw.toFixed(2)}}° · median ${{x.med.toFixed(3)}} m`]));
rows('health', [['Exact T4 lineage',D.lineage],['Credible/degraded/rejected',D.health],['CIR used','false'],['Product ready','false']]);
const c=document.getElementById('plot'),g=c.getContext('2d');g.fillStyle='#081522';g.fillRect(0,0,c.width,c.height);g.strokeStyle='#3f6085';g.beginPath();g.moveTo(55,250);g.lineTo(700,250);g.stroke();
const vals=D.blocks;const lo=-180,hi=180;vals.forEach((v,i)=>{{const x=85+i*120,y=250-(v-lo)/(hi-lo)*210;g.fillStyle='#ffcc66';g.fillRect(x-22,y,44,250-y);g.fillStyle='#dbe8f5';g.fillText(`B${{i}} ${{v.toFixed(1)}}°`,x-30,275)}});g.fillStyle='#aabbd0';g.fillText('Independent time-block yaw; authorization limit is 10° range',60,20);
</script></body></html>"""
    path = output / "C1_ROOT_R4_VIEWER_INDEX.html"; path.write_text(document, encoding="utf-8")
    return path


def build_review_video(output: Path, payload: dict) -> Path:
    path = output / "C1_ROOT_R4_REVIEW.mp4"
    fig = plt.figure(figsize=(12.8, 7.2), dpi=100, facecolor="#07111f")
    writer = FFMpegWriter(fps=15, bitrate=1600, metadata={"title": "BioSpur C1 Root-R4 review"})
    frames = payload["frames"]; blocks = payload["blocks"]
    with writer.saving(fig, str(path), 100):
        for frame_index in range(120):
            fig.clear(); fig.patch.set_facecolor("#07111f")
            ax = fig.add_axes([0.06, 0.10, 0.88, 0.80]); ax.set_facecolor("#0d1b2d"); ax.axis("off")
            phase = frame_index / 119.0
            ax.text(0.02, 0.94, "BioSpur C1 Root-R4", color="white", fontsize=24, weight="bold", transform=ax.transAxes)
            ax.text(0.02, 0.88, "EXPERIMENTAL · INTERNAL CONSISTENCY ONLY · NO PRODUCT USE", color="#ffcc66", fontsize=14, weight="bold", transform=ax.transAxes)
            if phase < 0.34:
                ax.text(0.03, 0.73, "Lineage closure", color="#9dd7ff", fontsize=20, transform=ax.transAxes)
                ax.text(0.03, 0.62, payload["lineage"], color="#70e1a1", fontsize=17, transform=ax.transAxes)
                ax.text(0.03, 0.51, "raw link → exact record/source row → T4 used-mask → T4 event", color="white", fontsize=15, transform=ax.transAxes)
            elif phase < 0.70:
                ax.text(0.03, 0.76, "Global frame evidence", color="#9dd7ff", fontsize=20, transform=ax.transAxes)
                for index, row in enumerate(frames):
                    ax.text(0.05, 0.66 - index * 0.10, f"{row['layer']}: {row['yaw']:.2f}°  median residual {row['med']:.3f} m", color="white", fontsize=15, transform=ax.transAxes)
                ax.text(0.05, 0.28, f"time-block yaw range: {np.ptp(blocks):.2f}° (limit 10°)", color="#ff8d8d", fontsize=17, weight="bold", transform=ax.transAxes)
            else:
                ax.text(0.03, 0.76, "Scientific verdict", color="#9dd7ff", fontsize=20, transform=ax.transAxes)
                lines = ["T4/raw lineage closed", "Root inertial equations pass synthetic truth", "Real C1 common frame not stable enough to authorize", "No real fused-root accuracy claim", "M1 byte-identical · CIR not used · no commit/push/merge"]
                for index, line in enumerate(lines):
                    ax.text(0.05, 0.65 - index * 0.10, "• " + line, color="white" if index != 2 else "#ff8d8d", fontsize=16, transform=ax.transAxes)
            writer.grab_frame()
    plt.close(fig); return path
