"use strict";
const $ = id => document.getElementById(id);
const video = $("video");
let stream = null, saving = false, batchBusy = false, batch = null, cameraStarting = false;
let cameraEpoch = 0;
const status = (text, error = false) => { $("status").textContent = text; $("status").classList.toggle("error", error); };
function updateControls() {
  $("capture").disabled = saving || batchBusy || !batch || !stream?.active;
  $("new-batch").disabled = saving || batchBusy;
  $("stop").disabled = !stream;
  $("start").disabled = Boolean(stream) || cameraStarting;
  $("camera").disabled = Boolean(stream) || saving || cameraStarting;
}
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : `保存失败 (${response.status})`);
  return result;
}
function showBatch(result) {
  batch = result;
  $("count").textContent = `本批 ${result.count} 张`;
  $("batch-id").textContent = `批次 ${result.batch_id}\n${result.directory}`;
  $("manifest").href = `/api/batches/${result.batch_id}/manifest.jsonl`;
  $("manifest").hidden = false;
  sessionStorage.setItem("lcd_capture_batch", result.batch_id);
  updateControls();
}
async function newBatch() {
  if (saving || batchBusy) return;
  batchBusy = true; updateControls();
  try {
    const form = new FormData(); form.append("name", $("batch-name").value);
    showBatch(await api("/api/batches", {method: "POST", body: form}));
    $("kind").value = "unlabeled"; $("label").value = ""; setKind();
    status("新批次已准备好。可以按空格拍照，后续再统一标注。");
  } catch (error) { status(error.message, true); }
  finally { batchBusy = false; updateControls(); }
}
function setKind() {
  $("label").disabled = !["reading", "overrange"].includes($("kind").value);
}
async function refreshCameras(selected = "") {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  try {
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === "videoinput");
    if (!devices.length) return;
    const preferred = selected || $("camera").value;
    $("camera").replaceChildren(...devices.map((device, index) => {
      const option = document.createElement("option"); option.value = device.deviceId;
      option.textContent = device.label || `摄像头 ${index + 1}`; option.selected = device.deviceId === preferred; return option;
    }));
  } catch (_) { /* Device permission may be needed before enumeration. */ }
}
function closeCamera() {
  cameraEpoch++;
  cameraStarting = false;
  if (stream) stream.getTracks().forEach(track => track.stop());
  stream = null; video.srcObject = null;
  document.querySelector(".preview").classList.remove("live");
  $("placeholder").hidden = false; $("resolution").textContent = "摄像头未打开";
  updateControls();
}
async function openCamera() {
  if (cameraStarting || stream) return;
  const epoch = ++cameraEpoch;
  cameraStarting = true;
  $("start").disabled = true;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("请在 O6 浏览器打开 http://127.0.0.1:8766，再允许摄像头权限");
    const deviceId = $("camera").value;
    const candidate = await navigator.mediaDevices.getUserMedia({audio: false,
      video: {width: {ideal: 1920}, height: {ideal: 1080}, ...(deviceId ? {deviceId: {exact: deviceId}} : {})}});
    if (epoch !== cameraEpoch) { candidate.getTracks().forEach(track => track.stop()); return; }
    stream = candidate; video.srcObject = stream; await video.play();
    if (epoch !== cameraEpoch) return;
    document.querySelector(".preview").classList.add("live"); $("placeholder").hidden = true;
    $("resolution").textContent = `${video.videoWidth} × ${video.videoHeight} · 原分辨率采集`;
    const track = stream.getVideoTracks()[0];
    track.addEventListener("ended", () => { if (stream === candidate) { closeCamera(); status("摄像头已断开，请重新打开。", true); } });
    await refreshCameras(track.getSettings().deviceId);
    status("摄像头已就绪。按空格拍照，保存后会显示照片编号。");
  } catch (error) {
    if (epoch === cameraEpoch) { closeCamera(); status(`打不开摄像头：${error.message}。可先关闭实验页正在使用的摄像头。`, true); }
  } finally { if (epoch === cameraEpoch) cameraStarting = false; updateControls(); }
}
function snapshot() {
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth; canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("无法截取当前画面")), "image/jpeg", .98));
}
async function capture() {
  if (saving || batchBusy || !batch || !stream?.active || video.readyState < 2 || !video.videoWidth) return;
  const kind = $("kind").value, label = $("label").value.trim();
  if (kind === "reading" && !/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/.test(label)) {
    status("请先按屏幕原样填写读数，或选择「待标注 / 看不清」。", true); $("label").focus(); return;
  }
  if (kind === "overrange" && !/^[a-zA-Z0-9.+-]{1,16}$/.test(label)) {
    status("请填写超量程时显示的字符，例如 OL 或 1。", true); $("label").focus(); return;
  }
  const currentBatch = batch.batch_id, note = $("note").value;
  const camera = stream.getVideoTracks()[0]?.label || "";
  saving = true; updateControls(); $("capture").textContent = "正在保存…";
  try {
    const blob = await snapshot();
    const captureId = crypto.randomUUID().replaceAll("-", "");
    const form = new FormData();
    for (const [name, value] of Object.entries({capture_id: captureId, kind, label, note, camera})) form.append(name, value);
    form.append("file", blob, "camera.jpg");
    const result = await api(`/api/batches/${currentBatch}/photos`, {method: "POST", body: form});
    batch.count = result.count; $("count").textContent = `本批 ${result.count} 张`;
    const names = {unlabeled: "待标注", blank: "空屏", unreadable: "看不清"};
    status(`已保存第 ${result.count} 张 · ${names[kind] || `读数 ${label}`}\n${result.filename}`);
    const flash = $("flash"); flash.classList.remove("active"); void flash.offsetWidth; flash.classList.add("active");
  } catch (error) { status(`保存未确认：${error.message}。请检查本批数量后重拍。`, true); }
  finally {
    saving = false;
    $("capture").replaceChildren(document.createTextNode("拍照保存 "), Object.assign(document.createElement("kbd"), {textContent: "Space"}));
    updateControls();
  }
}
$("start").onclick = openCamera; $("stop").onclick = closeCamera; $("capture").onclick = capture;
$("new-batch").onclick = newBatch;
$("kind").onchange = () => { setKind(); $("kind").blur(); };
document.addEventListener("keydown", event => {
  const target = event.target;
  const editing = target?.matches?.("input, textarea, select, [contenteditable=true]");
  if (event.key === "Escape") { closeCamera(); return; }
  if (editing) {
    if (event.key === "Enter" && target.tagName === "INPUT") { event.preventDefault(); target.blur(); status("设置已更新，按空格拍照。"); }
    return;
  }
  if (event.code === "Space" && !event.ctrlKey && !event.altKey && !event.metaKey) { event.preventDefault(); if (!event.repeat) void capture(); }
});
window.addEventListener("pagehide", closeCamera);
async function initialize() {
  void refreshCameras();
  const previous = sessionStorage.getItem("lcd_capture_batch");
  if (previous) {
    try { showBatch(await api(`/api/batches/${previous}`)); return; }
    catch (_) { sessionStorage.removeItem("lcd_capture_batch"); }
  }
  await newBatch();
}
void initialize();
