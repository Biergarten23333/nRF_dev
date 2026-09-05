#!/usr/bin/env node
"use strict";

// Deterministically export one local C2 HTML viewer through Chrome DevTools.
// Usage: node render_c2_viewer_frames.js URL OUTPUT_DIR FRAME_COUNT PORT

const childProcess = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");
const WebSocket = require("ws");

const [url, outputDir, frameCountText, portText] = process.argv.slice(2);
if (!url || !outputDir || !frameCountText || !portText) {
  throw new Error("expected URL OUTPUT_DIR FRAME_COUNT PORT");
}
const frameCount = Number(frameCountText);
const port = Number(portText);
if (!Number.isInteger(frameCount) || frameCount < 1 || !Number.isInteger(port)) {
  throw new Error("FRAME_COUNT and PORT must be positive integers");
}
fs.mkdirSync(outputDir, { recursive: true });
const profileDir = path.join(outputDir, ".chrome-profile");
fs.mkdirSync(profileDir, { recursive: true });

const chrome = childProcess.spawn("google-chrome", [
  "--headless=new",
  "--no-sandbox",
  "--disable-gpu",
  "--hide-scrollbars",
  "--force-dark-mode",
  "--window-size=1280,720",
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${profileDir}`,
  url,
], { stdio: ["ignore", "ignore", "inherit"] });

function getJson(endpoint) {
  return new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${port}${endpoint}`, response => {
      let body = "";
      response.on("data", chunk => { body += chunk; });
      response.on("end", () => {
        try { resolve(JSON.parse(body)); } catch (error) { reject(error); }
      });
    }).on("error", reject);
  });
}

async function waitForPage() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const pages = await getJson("/json/list");
      const page = pages.find(row => row.type === "page" && row.url.startsWith("file:"));
      if (page) return page;
    } catch (_error) {
      // Chrome is still starting.
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error("timed out waiting for Chrome page target");
}

async function main() {
  const page = await waitForPage();
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.once("open", resolve);
    socket.once("error", reject);
  });
  let nextId = 1;
  const pending = new Map();
  socket.on("message", bytes => {
    const message = JSON.parse(bytes.toString());
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  });
  function call(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }
  await call("Page.enable");
  await call("Runtime.enable");
  await new Promise(resolve => setTimeout(resolve, 750));
  for (let frame = 0; frame < frameCount; frame += 1) {
    const expression = [
      `frameIndex=${frame}`,
      "viewMode='oblique'",
      "setImuWorldCameraMode(false,0.35)",
      "draw()",
      "true",
    ].join(";");
    const evaluated = await call("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (evaluated.exceptionDetails) {
      throw new Error(JSON.stringify(evaluated.exceptionDetails));
    }
    const screenshot = await call("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: false,
    });
    fs.writeFileSync(
      path.join(outputDir, `frame_${String(frame).padStart(4, "0")}.png`),
      Buffer.from(screenshot.data, "base64"),
    );
  }
  socket.close();
  chrome.kill("SIGTERM");
}

main().catch(error => {
  chrome.kill("SIGTERM");
  console.error(error.stack || error);
  process.exitCode = 1;
});
