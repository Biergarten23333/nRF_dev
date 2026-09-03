"use strict";

(async function () {
  const meta = window.BioSpurViewerMeta;
  const chunks = window.BioSpurPayloadChunks;
  const root = document.getElementById("biospur-viewer");
  const canvas = document.getElementById("viewport");
  const ctx = canvas.getContext("2d");
  const status = document.getElementById("load-status");
  const readout = document.getElementById("view-readout");
  const timeline = document.getElementById("timeline");
  const timeOutput = document.getElementById("time-output");
  const timestampInput = document.getElementById("timestamp-input");
  const playButton = document.getElementById("play-pause");
  const speedSelect = document.getElementById("speed-select");
  const cameraSelect = document.getElementById("camera-mode");
  const eventSelect = document.getElementById("event-select");
  const captureSelect = document.getElementById("capture-select");

  const toggleIds = ["joints", "segments", "sensors", "global-axes", "body-axes", "segment-frames", "ground", "ghost"];
  const toggles = Object.fromEntries(toggleIds.map(name => [name, document.getElementById(`toggle-${name}`)]));
  const state = {
    frame: 0,
    playing: false,
    speed: 1,
    mode: "FREE_ORBIT",
    yaw: -0.72,
    pitch: 0.32,
    zoom: 1,
    panX: 0,
    panY: 0,
    lastAnimationMs: null,
    frameCarry: 0,
    drag: null,
  };

  function bytesFromChunks(values) {
    const decoded = values.map(value => {
      const binary = atob(value);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      return bytes;
    });
    const total = decoded.reduce((sum, value) => sum + value.length, 0);
    const output = new Uint8Array(total);
    let offset = 0;
    decoded.forEach(value => { output.set(value, offset); offset += value.length; });
    return output;
  }

  async function gunzip(bytes) {
    if (!("DecompressionStream" in window)) throw new Error("This browser lacks the offline gzip DecompressionStream API");
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  function typedArrays(raw, schema) {
    const constructors = {"<f8": Float64Array, "<f4": Float32Array, "|u1": Uint8Array};
    const output = {};
    Object.entries(schema).forEach(([name, spec]) => {
      const Constructor = constructors[spec.dtype];
      if (!Constructor) throw new Error(`Unsupported viewer dtype ${spec.dtype}`);
      const length = spec.shape.reduce((a, b) => a * b, 1);
      output[name] = new Constructor(raw.buffer, raw.byteOffset + spec.offset, length);
    });
    return output;
  }

  function add(a, b) { return [a[0]+b[0], a[1]+b[1], a[2]+b[2]]; }
  function sub(a, b) { return [a[0]-b[0], a[1]-b[1], a[2]-b[2]]; }
  function mul(a, scalar) { return [a[0]*scalar, a[1]*scalar, a[2]*scalar]; }
  function dot(a, b) { return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]; }
  function cross(a, b) { return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]; }
  function norm(a) { return Math.hypot(a[0], a[1], a[2]); }
  function unit(a) { const value = norm(a) || 1; return mul(a, 1/value); }

  function quatRotate(q, v) {
    const qv = [q[1], q[2], q[3]];
    const uv = cross(qv, v);
    const uuv = cross(qv, uv);
    return add(v, mul(add(mul(uv, q[0]), uuv), 2));
  }

  let arrays;
  let width = 1;
  let height = 1;
  const segmentCount = meta.segment_names.length;
  const jointCount = meta.joint_names.length;

  function qAt(frame, segment) {
    const offset = (frame * segmentCount + segment) * 4;
    return [arrays.q_GB_wxyz[offset], arrays.q_GB_wxyz[offset+1], arrays.q_GB_wxyz[offset+2], arrays.q_GB_wxyz[offset+3]];
  }

  function pointAt(frame, joint) {
    const offset = (frame * jointCount + joint) * 3;
    return [arrays.joint_positions_m[offset], arrays.joint_positions_m[offset+1], arrays.joint_positions_m[offset+2]];
  }

  function segmentValid(frame, segment) { return arrays.valid[frame*segmentCount+segment] !== 0; }
  function jointValid(frame, joint) { return arrays.joint_available[frame*jointCount+joint] !== 0; }

  function cameraBasis(frame) {
    let direction;
    let upReference = [0, 0, 1];
    if (state.mode === "WORLD_FIXED_FRONT") direction = [1, 0, 0];
    else if (state.mode === "WORLD_FIXED_SIDE") direction = [0, -1, 0];
    else if (state.mode === "WORLD_FIXED_TOP") { direction = [0, 0, 1]; upReference = [1, 0, 0]; }
    else if (state.mode.startsWith("PELVIS_")) {
      const pelvis = meta.segment_names.indexOf("pelvis");
      if (segmentValid(frame, pelvis)) {
        const q = qAt(frame, pelvis);
        direction = quatRotate(q, state.mode === "PELVIS_FRONT_LOCKED" ? [1, 0, 0] : [0, -1, 0]);
        upReference = quatRotate(q, [0, 0, 1]);
      } else direction = state.mode === "PELVIS_FRONT_LOCKED" ? [1, 0, 0] : [0, -1, 0];
    } else {
      direction = [Math.cos(state.pitch)*Math.cos(state.yaw), Math.cos(state.pitch)*Math.sin(state.yaw), Math.sin(state.pitch)];
      if (Math.abs(dot(unit(direction), upReference)) > 0.98) upReference = [1, 0, 0];
    }
    direction = unit(direction);
    const forward = mul(direction, -1);
    const right = unit(cross(forward, upReference));
    const up = unit(cross(right, forward));
    return {right, up, forward};
  }

  function projector(frame) {
    const basis = cameraBasis(frame);
    const scale = Math.min(width, height) * 0.42 / 1.40 * state.zoom;
    const center = [0, 0, -0.15];
    return point => {
      const relative = sub(point, center);
      return [width/2 + dot(relative, basis.right)*scale + state.panX,
              height/2 - dot(relative, basis.up)*scale + state.panY,
              dot(relative, basis.forward)];
    };
  }

  function line(project, a, b, color, thickness=2, alpha=1) {
    const pa = project(a);
    const pb = project(b);
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.strokeStyle = color;
    ctx.lineWidth = thickness;
    ctx.beginPath();
    ctx.moveTo(pa[0], pa[1]);
    ctx.lineTo(pb[0], pb[1]);
    ctx.stroke();
    ctx.restore();
  }

  function text(project, point, value, color="#dbeafe", dx=5, dy=-5) {
    const p = project(point);
    ctx.fillStyle = color;
    ctx.font = "12px system-ui, sans-serif";
    ctx.fillText(value, p[0]+dx, p[1]+dy);
  }

  function axisFrame(project, origin, q, size=0.12, alpha=0.95) {
    const axes = [
      [quatRotate(q, [size, 0, 0]), "#ef4444", "X"],
      [quatRotate(q, [0, size, 0]), "#22c55e", "Y"],
      [quatRotate(q, [0, 0, size]), "#3b82f6", "Z"],
    ];
    axes.forEach(([direction, color, label]) => {
      const end = add(origin, direction);
      line(project, origin, end, color, 2, alpha);
      if (size >= 0.16) text(project, end, label, color, 2, -2);
    });
  }

  function drawGround(project) {
    const z = meta.ground_z_m;
    for (let value=-1; value<=1.0001; value+=0.25) {
      line(project, [-1, value, z], [1, value, z], "#23384a", 1, 0.65);
      line(project, [value, -1, z], [value, 1, z], "#23384a", 1, 0.65);
    }
  }

  function drawGhost(project) {
    const frame = meta.ghost_frame;
    meta.edges.forEach(edge => {
      if (!jointValid(frame, edge.a) || !jointValid(frame, edge.b)) return;
      line(project, pointAt(frame, edge.a), pointAt(frame, edge.b), "#64748b", 3, 0.32);
    });
  }

  function segmentMidpoint(frame, edge) {
    return mul(add(pointAt(frame, edge.a), pointAt(frame, edge.b)), 0.5);
  }

  function drawFrame() {
    if (!arrays) return;
    const frame = state.frame;
    const project = projector(frame);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#050b11";
    ctx.fillRect(0, 0, width, height);
    if (toggles.ground.checked) drawGround(project);
    if (toggles.ghost.checked) drawGhost(project);
    if (toggles["global-axes"].checked) axisFrame(project, [0, 0, 0], [1, 0, 0, 0], 0.20);

    const edges = meta.edges.map(edge => ({...edge, depth: (project(pointAt(frame, edge.a))[2] + project(pointAt(frame, edge.b))[2])/2}))
      .sort((a, b) => a.depth-b.depth);
    edges.forEach(edge => {
      if (!jointValid(frame, edge.a) || !jointValid(frame, edge.b)) return;
      line(project, pointAt(frame, edge.a), pointAt(frame, edge.b), meta.colors[edge.class], edge.class === "pelvis" ? 5 : 4, 1);
    });

    for (let joint=0; joint<jointCount; joint+=1) {
      if (!jointValid(frame, joint)) continue;
      const p = project(pointAt(frame, joint));
      ctx.fillStyle = "#e7eef6";
      ctx.beginPath();
      ctx.arc(p[0], p[1], 3, 0, Math.PI*2);
      ctx.fill();
      if (toggles.joints.checked) text(project, pointAt(frame, joint), meta.joint_names[joint]);
    }

    const pelvisIndex = meta.segment_names.indexOf("pelvis");
    if (toggles["body-axes"].checked && segmentValid(frame, pelvisIndex)) {
      axisFrame(project, pointAt(frame, meta.segment_origins[pelvisIndex]), qAt(frame, pelvisIndex), 0.18);
    }
    if (toggles["segment-frames"].checked) {
      for (let segment=0; segment<segmentCount; segment+=1) {
        if (!segmentValid(frame, segment)) continue;
        axisFrame(project, pointAt(frame, meta.segment_origins[segment]), qAt(frame, segment), 0.10, 0.78);
      }
    }

    if (toggles.segments.checked || toggles.sensors.checked) {
      for (let segment=0; segment<segmentCount; segment+=1) {
        if (!segmentValid(frame, segment)) continue;
        const labels = [];
        if (toggles.segments.checked) labels.push(meta.segment_names[segment]);
        if (toggles.sensors.checked) labels.push(meta.node_ids[segment]);
        const name = meta.segment_names[segment];
        const colorClass = name === "pelvis" ? "pelvis" : name.includes("_left") ? "left" : name.includes("_right") ? "right" : "core";
        text(project, pointAt(frame, meta.segment_origins[segment]), labels.join(" · "), meta.colors[colorClass], 7, 14+(segment%3)*13);
      }
    }

    const time = arrays.time_s[frame];
    const validSegments = Array.from({length: segmentCount}, (_, i) => segmentValid(frame, i)).filter(Boolean).length;
    const resetNodes = Array.from({length: segmentCount}, (_, i) => arrays.filter_reset[frame*segmentCount+i] ? meta.node_ids[i] : null).filter(Boolean);
    const commonYaw = arrays.common_body_yaw_deg[frame];
    const spread = arrays.inter_segment_heading_spread_deg[frame];
    readout.textContent = `frame ${frame.toLocaleString()} / ${(meta.frames-1).toLocaleString()} · ${time.toFixed(3)} s · valid ${validSegments}/10 · common yaw ${Number.isFinite(commonYaw) ? commonYaw.toFixed(1) : "n/a"}° · inter-segment spread ${Number.isFinite(spread) ? spread.toFixed(1) : "n/a"}°${resetNodes.length ? ` · RESET ${resetNodes.join(", ")}` : ""}`;
    timeOutput.textContent = `${time.toFixed(3)} s`;
    timeline.value = String(frame);
    timestampInput.value = time.toFixed(3);
  }

  function setFrame(frame) {
    state.frame = Math.max(0, Math.min(meta.frames-1, Math.round(frame)));
    drawFrame();
  }

  function frameForTime(time) {
    let lo = 0;
    let hi = meta.frames-1;
    while (lo < hi) {
      const mid = Math.floor((lo+hi)/2);
      if (arrays.time_s[mid] < time) lo = mid+1; else hi = mid;
    }
    if (lo > 0 && Math.abs(arrays.time_s[lo-1]-time) < Math.abs(arrays.time_s[lo]-time)) return lo-1;
    return lo;
  }

  function setPlaying(value) {
    state.playing = value;
    state.lastAnimationMs = null;
    state.frameCarry = 0;
    playButton.textContent = value ? "Pause" : "Play";
  }

  function animation(now) {
    if (state.playing) {
      if (state.lastAnimationMs !== null) {
        const frames = (now-state.lastAnimationMs)/1000 * meta.display_rate_hz * state.speed + state.frameCarry;
        const whole = Math.floor(frames);
        state.frameCarry = frames-whole;
        if (whole > 0) {
          if (state.frame+whole >= meta.frames-1) { setFrame(meta.frames-1); setPlaying(false); }
          else setFrame(state.frame+whole);
        }
      }
      state.lastAnimationMs = now;
    }
    requestAnimationFrame(animation);
  }

  function resetCamera() {
    state.zoom = 1;
    state.panX = 0;
    state.panY = 0;
    state.yaw = -0.72;
    state.pitch = 0.32;
    drawFrame();
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    width = Math.max(1, Math.round(rect.width*ratio));
    height = Math.max(1, Math.round(rect.height*ratio));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    drawFrame();
  }

  function bindControls() {
    captureSelect.value = meta.capture;
    captureSelect.addEventListener("change", () => { window.location.href = `CAPTURE${captureSelect.value}_INTERACTIVE_3D.html`; });
    timeline.max = String(meta.frames-1);
    timeline.addEventListener("input", () => { setPlaying(false); setFrame(Number(timeline.value)); });
    playButton.addEventListener("click", () => setPlaying(!state.playing));
    document.getElementById("step-back").addEventListener("click", () => { setPlaying(false); setFrame(state.frame-1); });
    document.getElementById("step-forward").addEventListener("click", () => { setPlaying(false); setFrame(state.frame+1); });
    speedSelect.addEventListener("change", () => { state.speed = Number(speedSelect.value); });
    cameraSelect.addEventListener("change", () => { state.mode = cameraSelect.value; resetCamera(); });
    document.getElementById("reset-camera").addEventListener("click", resetCamera);
    document.getElementById("jump-time").addEventListener("click", () => { setPlaying(false); setFrame(frameForTime(Number(timestampInput.value))); });
    timestampInput.addEventListener("keydown", event => { if (event.key === "Enter") document.getElementById("jump-time").click(); });
    meta.events.forEach(event => {
      const option = document.createElement("option");
      option.value = String(event.time_s);
      option.textContent = `${Number(event.time_s).toFixed(3)} s — ${event.label}`;
      eventSelect.appendChild(option);
    });
    document.getElementById("jump-event").addEventListener("click", () => { setPlaying(false); setFrame(frameForTime(Number(eventSelect.value))); });
    Object.values(toggles).forEach(toggle => toggle.addEventListener("change", drawFrame));

    canvas.addEventListener("contextmenu", event => event.preventDefault());
    canvas.addEventListener("pointerdown", event => {
      // Synthetic events used by the offline runtime self-test are not active
      // pointer streams and therefore cannot be captured by Chromium.
      if (event.isTrusted) canvas.setPointerCapture(event.pointerId);
      state.drag = {x: event.clientX, y: event.clientY, pan: event.shiftKey || event.button === 2};
      canvas.classList.add("dragging");
    });
    canvas.addEventListener("pointermove", event => {
      if (!state.drag) return;
      const dx = event.clientX-state.drag.x;
      const dy = event.clientY-state.drag.y;
      state.drag.x = event.clientX;
      state.drag.y = event.clientY;
      if (state.drag.pan || state.mode !== "FREE_ORBIT") { state.panX += dx*(window.devicePixelRatio || 1); state.panY += dy*(window.devicePixelRatio || 1); }
      else { state.yaw -= dx*0.008; state.pitch = Math.max(-1.48, Math.min(1.48, state.pitch+dy*0.008)); }
      drawFrame();
    });
    const endDrag = () => { state.drag = null; canvas.classList.remove("dragging"); };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    canvas.addEventListener("wheel", event => { event.preventDefault(); state.zoom = Math.max(0.2, Math.min(8, state.zoom*Math.exp(-event.deltaY*0.001))); drawFrame(); }, {passive: false});
    new ResizeObserver(resize).observe(canvas);
  }

  function assertion(checks, name, condition, detail="") {
    checks.push({name, pass: Boolean(condition), detail});
  }

  async function runSelfTest() {
    const checks = [];
    assertion(checks, "typed arrays decoded", arrays.time_s.length === meta.frames && arrays.q_GB_wxyz.length === meta.frames*segmentCount*4);
    assertion(checks, "canvas has dimensions", canvas.width > 100 && canvas.height > 100, `${canvas.width}x${canvas.height}`);
    assertion(checks, "three-capture selector", captureSelect.options.length === 3);

    setPlaying(false);
    setFrame(100);
    const beforeStep = state.frame;
    document.getElementById("step-forward").click();
    assertion(checks, "exact one-frame forward step", state.frame === beforeStep+1, `${beforeStep}->${state.frame}`);
    document.getElementById("step-back").click();
    assertion(checks, "exact one-frame backward step", state.frame === beforeStep, `${state.frame}`);
    timeline.value = "240";
    timeline.dispatchEvent(new Event("input", {bubbles: true}));
    assertion(checks, "complete timeline scrub", state.frame === 240);

    playButton.click();
    assertion(checks, "play", state.playing === true);
    playButton.click();
    assertion(checks, "pause", state.playing === false);
    for (const speed of ["0.25", "0.5", "1", "2", "4"]) {
      speedSelect.value = speed;
      speedSelect.dispatchEvent(new Event("change", {bubbles: true}));
      assertion(checks, `playback speed ${speed}x`, state.speed === Number(speed));
    }

    const cameraModes = ["WORLD_FIXED_FRONT", "WORLD_FIXED_SIDE", "WORLD_FIXED_TOP", "FREE_ORBIT", "PELVIS_FRONT_LOCKED", "PELVIS_SIDE_LOCKED"];
    for (const mode of cameraModes) {
      cameraSelect.value = mode;
      cameraSelect.dispatchEvent(new Event("change", {bubbles: true}));
      const basis = cameraBasis(state.frame);
      assertion(checks, `camera ${mode}`, state.mode === mode && [...basis.right, ...basis.up, ...basis.forward].every(Number.isFinite));
    }

    cameraSelect.value = "FREE_ORBIT";
    cameraSelect.dispatchEvent(new Event("change", {bubbles: true}));
    Object.assign(state, {yaw: 0.42, pitch: 0.21, zoom: 1.37, panX: 17, panY: -9});
    setFrame(480);
    const retained = state.yaw === 0.42 && state.pitch === 0.21 && state.zoom === 1.37 && state.panX === 17 && state.panY === -9;
    assertion(checks, "camera retained while scrubbing", retained);
    document.getElementById("reset-camera").click();
    assertion(checks, "camera reset", state.zoom === 1 && state.panX === 0 && state.panY === 0);

    const orbitBefore = [state.yaw, state.pitch];
    canvas.dispatchEvent(new PointerEvent("pointerdown", {pointerId: 71, button: 0, clientX: 100, clientY: 100, bubbles: true}));
    canvas.dispatchEvent(new PointerEvent("pointermove", {pointerId: 71, button: 0, clientX: 130, clientY: 118, bubbles: true}));
    canvas.dispatchEvent(new PointerEvent("pointerup", {pointerId: 71, button: 0, clientX: 130, clientY: 118, bubbles: true}));
    assertion(checks, "orbit by dragging", state.yaw !== orbitBefore[0] && state.pitch !== orbitBefore[1]);
    const panBefore = [state.panX, state.panY];
    canvas.dispatchEvent(new PointerEvent("pointerdown", {pointerId: 72, button: 0, shiftKey: true, clientX: 80, clientY: 80, bubbles: true}));
    canvas.dispatchEvent(new PointerEvent("pointermove", {pointerId: 72, button: 0, shiftKey: true, clientX: 102, clientY: 91, bubbles: true}));
    canvas.dispatchEvent(new PointerEvent("pointerup", {pointerId: 72, button: 0, shiftKey: true, clientX: 102, clientY: 91, bubbles: true}));
    assertion(checks, "pan by dragging", state.panX !== panBefore[0] && state.panY !== panBefore[1]);
    const zoomBefore = state.zoom;
    canvas.dispatchEvent(new WheelEvent("wheel", {deltaY: -120, cancelable: true, bubbles: true}));
    assertion(checks, "zoom by wheel", state.zoom > zoomBefore);
    document.getElementById("reset-camera").click();

    Object.entries(toggles).forEach(([name, toggle]) => {
      const original = toggle.checked;
      toggle.click();
      const changed = toggle.checked !== original;
      toggle.click();
      assertion(checks, `toggle ${name}`, changed && toggle.checked === original);
    });

    const exactTarget = meta.capture === "2" ? 10.10 : 10.0;
    timestampInput.value = exactTarget.toFixed(3);
    document.getElementById("jump-time").click();
    assertion(checks, "exact timestamp jump", Math.abs(arrays.time_s[state.frame]-exactTarget) <= 1/meta.display_rate_hz/2+1e-9, `${arrays.time_s[state.frame]}`);
    if (eventSelect.options.length) {
      eventSelect.selectedIndex = eventSelect.options.length-1;
      const eventTarget = Number(eventSelect.value);
      document.getElementById("jump-event").click();
      assertion(checks, "event jump", Math.abs(arrays.time_s[state.frame]-eventTarget) <= 1/meta.display_rate_hz/2+1e-9, `${eventTarget}->${arrays.time_s[state.frame]}`);
    }

    const reviewTargets = {"1": 700.0, "2": 1198.35, "3": 900.0};
    setFrame(frameForTime(Math.min(meta.duration_s, reviewTargets[meta.capture])));
    drawFrame();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const sample = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let variation = 0;
    for (let i=0; i<sample.length; i+=4096) variation += sample[i] + sample[i+1] + sample[i+2];
    assertion(checks, "canvas rendered nonempty scene", variation > 0, String(variation));

    const result = {capture: meta.capture, pass: checks.every(check => check.pass), checks,
                    final_frame: state.frame, final_time_s: arrays.time_s[state.frame],
                    browser_user_agent: navigator.userAgent};
    const evidence = document.createElement("pre");
    evidence.id = "biospur-selftest";
    evidence.hidden = true;
    evidence.textContent = JSON.stringify(result);
    root.appendChild(evidence);
    window.__BIO_VIEWER_SELFTEST = result;
    document.title = `${document.title} — SELFTEST_${result.pass ? "PASS" : "FAIL"}`;
  }

  try {
    if (!meta || !Array.isArray(chunks) || chunks.length === 0) throw new Error("Viewer metadata or payload is missing");
    const compressed = bytesFromChunks(chunks);
    const raw = await gunzip(compressed);
    arrays = typedArrays(raw, meta.arrays);
    if (arrays.time_s.length !== meta.frames) throw new Error("Decoded frame count does not match metadata");
    bindControls();
    resize();
    status.textContent = `Ready · ${meta.frames.toLocaleString()} frames · ${meta.duration_s.toFixed(3)} s · offline typed arrays`;
    window.BioSpurViewerTest = {
      ready: true,
      metadata: meta,
      getState: () => ({frame: state.frame, mode: state.mode, zoom: state.zoom, panX: state.panX, panY: state.panY}),
      setFrame,
      frameForTime,
      resetCamera,
      arrays,
    };
    if (new URLSearchParams(window.location.search).get("selftest") === "1") await runSelfTest();
    requestAnimationFrame(animation);
  } catch (error) {
    console.error(error);
    status.textContent = `Viewer error: ${error.message}`;
    status.classList.add("error");
    window.__BIO_VIEWER_ERROR = String(error.stack || error);
  }
})();
