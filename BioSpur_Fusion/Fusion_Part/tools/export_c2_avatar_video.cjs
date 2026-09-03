#!/usr/bin/env node
"use strict";

/** Export one embedded C2 viewer episode without modifying its frozen samples. */

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");
const { spawnSync } = require("child_process");
const { chromium } = require("playwright");

function value(flag, fallback = undefined) {
  const index = process.argv.indexOf(flag);
  return index === -1 ? fallback : process.argv[index + 1];
}

function required(flag) {
  const result = value(flag);
  if (!result) throw new Error(`missing required argument ${flag}`);
  return result;
}

function sha256(filename) {
  return crypto.createHash("sha256").update(fs.readFileSync(filename)).digest("hex");
}

function run(command, args) {
  const result = spawnSync(command, args, { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(`${command} failed (${result.status}):\n${result.stderr}`);
  }
  return result.stdout;
}

async function main() {
  const source = path.resolve(required("--source"));
  const outputDirectory = path.resolve(required("--output-dir"));
  const action = required("--action");
  const camera = value("--camera", "oblique");
  const zoom = Number(value("--zoom", "0.55"));
  const fps = Number(value("--fps", "10"));
  const width = Number(value("--width", "1280"));
  const height = Number(value("--height", "720"));
  const basename = value("--basename", action.toUpperCase());
  const colorScheme = value("--color-scheme", "light");
  const worldMotionCamera = value("--world-motion-camera", "follow");
  const worldOverviewZoom = Number(value("--world-overview-zoom", String(zoom)));
  const keepFrames = process.argv.includes("--keep-frames");

  if (!["light", "dark"].includes(colorScheme)) {
    throw new Error(`unsupported color scheme: ${colorScheme}`);
  }
  if (!["follow", "overview"].includes(worldMotionCamera)) {
    throw new Error(`unsupported world-motion camera: ${worldMotionCamera}`);
  }

  if (!fs.statSync(source).isFile()) throw new Error(`source is not a file: ${source}`);
  if (fs.existsSync(outputDirectory)) {
    throw new Error(`refusing to reuse output directory: ${outputDirectory}`);
  }
  fs.mkdirSync(outputDirectory, { recursive: true });
  const frameDirectory = path.join(outputDirectory, `${basename}_FRAMES_${fps}FPS`);
  fs.mkdirSync(frameDirectory);

  const browser = await chromium.launch({
    executablePath: "/usr/bin/google-chrome",
    headless: true,
    args: ["--allow-file-access-from-files", "--disable-background-timer-throttling"],
  });
  let episode;
  try {
    const page = await browser.newPage({
      viewport: { width, height },
      deviceScaleFactor: 1,
      colorScheme,
    });
    const episodeIndex = await page.goto(pathToFileURL(source).href, { waitUntil: "load" })
      .then(() => page.evaluate((wanted) => DATA.episodes.findIndex((row) => row.id === wanted), action));
    if (episodeIndex < 0) throw new Error(`action not found in viewer: ${action}`);

    const query = new URLSearchParams({
      build: `video-export-${action}`,
      episode: String(episodeIndex),
      frame: "0",
      view: camera,
      zoom: String(zoom),
    });
    await page.goto(`${pathToFileURL(source).href}?${query}`, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(({ worldMotionCamera, worldOverviewZoom }) => {
      if (typeof IMU_WORLD === "undefined") return;
      setImuWorldCameraMode(worldMotionCamera !== "overview", worldOverviewZoom);
    }, { worldMotionCamera, worldOverviewZoom });
    episode = await page.evaluate(() => ({
      id: active().id,
      instruction: active().instruction,
      frameCount: active().frames.length,
      sourceFrame: [...active().sourceFrame],
      time: [...active().time],
      jointNames: [...DATA.jointNames],
      targetFps: DATA.targetFps,
      trajectoryModified: DATA.viewGauge.trajectoryModified,
      viewerRepair: DATA.viewerRepair,
      ik: DATA.ik,
      retarget: DATA.retarget,
      worldMotion: active().worldMotion || null,
    }));
    if (episode.id !== action) throw new Error(`viewer selected ${episode.id}, expected ${action}`);
    if (Math.abs(episode.targetFps - fps) > 1e-9) {
      throw new Error(`viewer target FPS ${episode.targetFps} differs from export FPS ${fps}`);
    }

    for (let frame = 0; frame < episode.frameCount; frame += 1) {
      await page.evaluate((index) => {
        playing = false;
        frameIndex = index;
        scrub.value = String(index);
        accumulator = 0;
        draw();
      }, frame);
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => resolve())));
      await page.screenshot({
        path: path.join(frameDirectory, `frame_${String(frame).padStart(6, "0")}.png`),
        type: "png",
      });
    }
  } finally {
    await browser.close();
  }

  const video = path.join(outputDirectory, `${basename}_${camera.toUpperCase()}_ZOOM${String(zoom).replace(".", "")}_${fps}FPS.mp4`);
  run("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y",
    "-framerate", String(fps),
    "-i", path.join(frameDirectory, "frame_%06d.png"),
    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
    "-pix_fmt", "yuv420p", "-movflags", "+faststart", video,
  ]);

  const sampleFrames = Array.from({ length: 8 }, (_, index) =>
    Math.round(index * (episode.frameCount - 1) / 7));
  const selection = sampleFrames.map((frame) => `eq(n\\,${frame})`).join("+");
  const contactSheet = path.join(outputDirectory, `${basename}_${camera.toUpperCase()}_QA_8_FRAMES.jpg`);
  run("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y", "-i", video,
    "-vf", `select='${selection}',scale=640:360,tile=4x2`,
    "-frames:v", "1", "-q:v", "2", contactSheet,
  ]);

  const probe = JSON.parse(run("ffprobe", [
    "-v", "error", "-select_streams", "v:0",
    "-show_entries", "stream=codec_name,pix_fmt,width,height,avg_frame_rate,nb_frames:format=duration",
    "-of", "json", video,
  ]));
  const stream = probe.streams[0];
  const audit = {
    status: "DISPLAY_ONLY_CAMERA_EXPORT_OVER_FROZEN_CAPTURE2",
    source_action: action,
    source_instruction: episode.instruction,
    source_html: source,
    source_html_sha256: sha256(source),
    camera: {
      preset: camera,
      zoom,
      color_scheme: colorScheme,
      body_following: worldMotionCamera === "follow" && camera !== "three",
      world_motion_camera: worldMotionCamera,
      world_overview_zoom: worldOverviewZoom,
      fixed_during_export: true,
    },
    video: {
      path: video,
      sha256: sha256(video),
      codec: stream.codec_name,
      pixel_format: stream.pix_fmt,
      width: stream.width,
      height: stream.height,
      fps,
      frame_count: Number(stream.nb_frames),
      duration_s: Number(probe.format.duration),
    },
    visual_qa: {
      sampled_frames: sampleFrames,
      contact_sheet: contactSheet,
      contact_sheet_sha256: sha256(contactSheet),
      human_pixel_review_required: true,
    },
    frozen_capture2: {
      joint_samples_modified: Boolean(episode.worldMotion?.global_translation_applied),
      relative_joint_geometry_modified: Boolean(episode.worldMotion?.relative_joint_geometry_modified),
      orientation_or_calibration_refit: false,
      ik_rebase_retarget_or_repair: false,
      embedded_trajectory_modified_flag: episode.trajectoryModified,
      embedded_viewer_repair_flag: episode.viewerRepair,
      embedded_ik_flag: episode.ik,
      embedded_retarget_flag: episode.retarget,
    },
  };
  const auditPath = path.join(outputDirectory, `${basename}_MP4_AUDIT.json`);
  fs.writeFileSync(auditPath, `${JSON.stringify(audit, null, 2)}\n`, { flag: "wx", mode: 0o444 });

  if (!keepFrames) fs.rmSync(frameDirectory, { recursive: true });
  process.stdout.write(`${JSON.stringify({ video, contactSheet, audit: auditPath, frameCount: episode.frameCount }, null, 2)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
