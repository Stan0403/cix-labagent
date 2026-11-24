"use strict";
const $ = (id) => document.getElementById(id);
const S = { session: null, config: null, spec: null, stream: null, mediaURL: null,
  media: null, roi: null, drag: null, timer: null, observing: false, polling: false,
  recorder: null, audioStream: null, audioTimeout: null, toastTimer: null, savingPhoto: false };
const names = {manual:"人工录入 / 确认",vision:"视觉模型",ocr:"显示屏 OCR",serial:"串口上报",replay:"测试回放"};

function toast(message, error=false) {
  clearTimeout(S.toastTimer); $("toast").textContent = message;
  $("toast").className = "toast" + (error ? " error" : "");
  S.toastTimer = setTimeout(() => $("toast").classList.add("hidden"), 6000);
}
function authHeaders() {
  const token = sessionStorage.getItem("labagent_access_token");
  return token ? {Authorization: "Bearer " + token} : {};
}
async function api(path, options={}) {
  const headers = {...authHeaders(), ...options.headers};
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch("/api" + path, {...options, headers});
  if (!response.ok) {
    let detail = `请求失败：HTTP ${response.status}`;
    try { const result = await response.json(); detail = typeof result.detail === "string" ? result.detail : JSON.stringify(result.detail); } catch { /* bounded diagnostic */ }
    if (response.status === 401) $("settings-dialog").showModal();
    throw new Error(detail);
  }
  return response.json();
}
function post(path, body) { return api(path, {method:"POST", body:JSON.stringify(body)}); }
async function attempt(fn) { try { return await fn(); } catch (error) { toast(error.message, true); return null; } }
function selected() { if (!S.session) throw new Error("请先选择或创建实验"); return S.session.id; }
function sync(state) {
  if (!S.session || S.session.id !== state.id || state.revision < S.session.revision) return;
  S.session = state; render();
}
async function refreshList() {
  const sessions = await api("/sessions"); $("sessions").replaceChildren();
  for (const item of sessions) {
    const button = document.createElement("button"); button.className = "session-item" + (S.session?.id === item.id ? " active" : "");
    const title = document.createElement("span"); title.className = "session-name"; title.textContent = item.title;
    const meta = document.createElement("small"); meta.textContent = `${item.mode === "replay" ? "回放测试" : "真实会话"} · 第 ${item.step+1} 步`;
    button.append(title, meta); button.addEventListener("click", () => attempt(() => selectSession(item.id)));
    $("sessions").append(button);
  }
}
async function selectSession(id) {
  stopObservation(); S.session = await api(`/sessions/${id}`); sessionStorage.setItem("labagent_session", id);
  $("human-confirm").checked = false; $("perception-output").classList.add("hidden");
  $("meter-range-confirm").checked = false; $("meter-range").value = "";
  const found = S.session.facts.objects?.value || [];
  document.querySelectorAll(".equipment-checkbox").forEach(box => box.checked = found.includes(box.value));
  render(); await Promise.all([refreshMessages(), refreshAudit(), refreshList()]);
}
function render() {
  const s = S.session; if (!s) return;
  $("empty").classList.add("hidden"); $("active").classList.remove("hidden");
  $("session-title").textContent = s.title; $("session-description").textContent = `会话 ${s.id.slice(0,8)} · 数据本地保存 · 修订 ${s.revision}`;
  $("mode").textContent = s.mode === "replay" ? "回放测试 · 非真实测量" : "真实实验会话";
  $("mode").className = "pill " + (s.mode === "replay" ? "replay" : "good");
  $("step-summary").textContent = s.steps[s.step].title;
  $("run-status").textContent = {running:"进行中", paused:"已暂停", completed:"已完成"}[s.status];
  const latest = [...s.measurements].reverse().find(m => !m.superseded);
  $("latest-reading").textContent = latest ? `${latest.value} ${latest.unit}` : "暂无有效读数";
  $("latest-source").textContent = latest ? names[latest.source] : "—";
  $("steps").replaceChildren();
  s.steps.forEach((step, i) => {
    const li = document.createElement("li"); li.className = i < s.step || s.status === "completed" ? "done" : i === s.step ? "current" : "";
    const dot = document.createElement("span"); dot.className = "step-dot"; dot.textContent = i < s.step || s.status === "completed" ? "✓" : i+1;
    const label = document.createElement("span"); label.textContent = step.title; li.append(dot, label); $("steps").append(li);
  });
  $("step-count").textContent = `${s.step+1} / ${s.steps.length}`;
  $("current-step").textContent = s.steps[s.step].title; $("instruction").textContent = s.steps[s.step].instruction;
  $("blockers").replaceChildren(); for (const blocker of s.blockers) { const li = document.createElement("li"); li.textContent=blocker; $("blockers").append(li); }
  $("advance").disabled = s.status !== "running"; $("advance").textContent = s.step === 8 ? "验证并完成实验 →" : "验证并进入下一步 →";
  $("pause").disabled = s.status !== "running";
  $("measurement-history").replaceChildren();
  for (const measurement of [...s.measurements].reverse()) {
    const tr = document.createElement("tr");
    for (const value of [`${measurement.value} V`, names[measurement.source], measurement.superseded ? "已被替代" : measurement.saved ? "已保存" : "候选 / 未保存", new Date(measurement.at).toLocaleTimeString()]) {
      const td = document.createElement("td"); td.textContent=value; tr.append(td);
    }
    $("measurement-history").append(tr);
  }
  if (!s.measurements.length) { const tr=document.createElement("tr"), td=document.createElement("td"); td.colSpan=4; td.textContent="尚无记录，不会生成示例测量值"; tr.append(td); $("measurement-history").append(tr); }
}
async function doAction(kind, extra={}) {
  const id=selected(), revision=S.session.revision;
  try {
    const result = await post(`/sessions/${id}/actions`, {kind, expected_revision:revision, ...extra});
    if (S.session?.id === id) { sync(result.session); await refreshAudit(); }
    toast(result.message, !result.accepted); await refreshList(); return result;
  } catch (error) { if (S.session?.id===id) sync(await api(`/sessions/${id}`)); throw error; }
}
async function humanAction(kind, extra={}) {
  if (!$("human-confirm").checked) throw new Error("请先完成现场核对，并勾选人工确认");
  try { return await doAction(kind, {confirmed:true, ...extra}); }
  finally { $("human-confirm").checked=false; }
}
async function refreshMessages() {
  const id=selected(), items=await api(`/sessions/${id}/messages`); if (S.session?.id!==id) return;
  $("messages").replaceChildren();
  for (const item of items) {
    const div=document.createElement("div"); div.className="message " + item.role;
    const author=document.createElement("span"); author.className="author"; author.textContent=item.role==="user" ? "YOU" : "LABAGENT";
    div.append(author,document.createTextNode(item.content)); $("messages").append(div);
  }
  if (!items.length) { const note=document.createElement("div"); note.className="message assistant"; note.textContent="我会根据当前实验状态回答。未配置模型时使用规则与本地检索；物理检查请在现场完成后使用确认面板。"; $("messages").append(note); }
  $("messages").scrollTop=$("messages").scrollHeight;
}
async function refreshAudit() {
  const id=selected(), events=await api(`/sessions/${id}/events`); if (S.session?.id!==id) return;
  $("audit").replaceChildren();
  for (const event of events.slice(-40).reverse()) {
    const row=document.createElement("div"), result=event.payload.result;
    if (result && !result.accepted) row.className="denied";
    row.textContent=`${new Date(event.at).toLocaleTimeString()} · ${event.kind}${result?.message ? " · "+result.message : ""}`; $("audit").append(row);
  }
}
async function sendChat() {
  const id=selected(), message=$("chat-input").value.trim(); if (!message) return;
  $("send-message").disabled=true; $("chat-input").value=""; $("chat-tools").textContent="正在读取状态与处理…";
  try {
    const result=await post(`/sessions/${id}/chat`,{message});
    if (S.session?.id===id) { sync(result.session); $("agent-mode").textContent=result.engine==="api" ? "模型已接入" : result.engine==="unavailable" ? "模型不可用" : "本地规则 / 检索";
      $("chat-tools").replaceChildren(document.createTextNode(result.traces.map(t=>t.tool).join(" → ")));
      const actionLabels={advance:"批准进入下一步",pause:"批准暂停",remeasure:"批准重新测量",save_measurement:"核对后批准保存",report:"批准完成实验"};
      for(const proposal of result.proposals||[]) {
        const button=document.createElement("button");button.className="secondary";button.textContent=actionLabels[proposal.kind]||"确认建议";
        button.addEventListener("click",()=>attempt(async()=>{
          if(S.session?.id!==id||S.session.revision!==proposal.expected_revision)throw new Error("建议对应的状态已变化，请重新询问或使用操作面板");
          button.disabled=true;
          if(proposal.kind==="save_measurement")await humanAction(proposal.kind,{expected_revision:proposal.expected_revision});
          else await doAction(proposal.kind,{expected_revision:proposal.expected_revision});
        }));$("chat-tools").append(button);
      }
      await Promise.all([refreshMessages(),refreshAudit()]); }
  } catch(error) { $("chat-input").value=message; throw error; }
  finally { $("send-message").disabled=false; }
}
function stopMedia() {
  $("save-training-photo").disabled=true;
  stopObservation(); if(S.stream) S.stream.getTracks().forEach(t=>t.stop()); S.stream=null;
  $("camera").pause(); $("camera").srcObject=null; $("camera").removeAttribute("src");
  if(S.mediaURL) URL.revokeObjectURL(S.mediaURL); S.mediaURL=null; S.media=null; S.roi=null;
  $("camera").classList.add("hidden"); $("preview").classList.add("hidden"); $("roi-overlay").classList.add("hidden");
  $("media-placeholder").classList.remove("hidden"); $("roi-label").textContent="未选择";
}
function prepareOverlay(width,height) {
  const canvas=$("roi-overlay"); canvas.width=width; canvas.height=height; canvas.classList.remove("hidden");
  $("media-placeholder").classList.add("hidden"); S.roi=null; $("roi-label").textContent="未选择";
}
async function startCamera() {
  stopMedia(); S.stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1920},height:{ideal:1080}},audio:false});
  const video=$("camera"); video.classList.remove("hidden"); video.srcObject=S.stream; await video.play(); S.media=video;
  prepareOverlay(video.videoWidth,video.videoHeight);
  $("save-training-photo").disabled=S.savingPhoto;
  void refreshTrainingPhotoCount();
}
async function loadMedia(file) {
  if (!file) return; stopMedia(); S.mediaURL=URL.createObjectURL(file);
  if(file.type.startsWith("video/")) {
    if(S.session?.mode!=="replay") { stopMedia(); throw new Error("录像回放请先创建/选择回放测试会话，不能混入真实实验"); }
    const video=$("camera"); video.src=S.mediaURL; video.classList.remove("hidden"); video.controls=true;
    await video.play(); S.media=video; prepareOverlay(video.videoWidth,video.videoHeight);
  } else {
    const img=$("preview"); img.src=S.mediaURL; await img.decode(); img.classList.remove("hidden"); S.media=img;
    prepareOverlay(img.naturalWidth,img.naturalHeight);
  }
}
function pointerPosition(event) { const rect=$("roi-overlay").getBoundingClientRect(); return {x:Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),y:Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))}; }
function drawROI() {
  const c=$("roi-overlay"), ctx=c.getContext("2d"); ctx.clearRect(0,0,c.width,c.height); if(!S.roi) return;
  const [x,y,w,h]=S.roi; ctx.strokeStyle="#39c5aa"; ctx.lineWidth=Math.max(2,c.width/300); ctx.fillStyle="#39c5aa22";
  ctx.fillRect(x*c.width,y*c.height,w*c.width,h*c.height); ctx.strokeRect(x*c.width,y*c.height,w*c.width,h*c.height);
  $("roi-label").textContent=S.roi.map(v=>v.toFixed(3)).join(", ");
}
async function capture(quality=.9) {
  if(!S.media) throw new Error("请先打开摄像头或载入图片");
  const canvas=document.createElement("canvas"); canvas.width=S.media.videoWidth||S.media.naturalWidth; canvas.height=S.media.videoHeight||S.media.naturalHeight;
  canvas.getContext("2d").drawImage(S.media,0,0); return new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error("无法截取画面")),"image/jpeg",quality));
}
async function refreshTrainingPhotoCount() {
  try {
    const {count}=await api("/training-photos");
    if(!S.savingPhoto && S.stream) $("training-photo-status").textContent=`已存 ${count} 张训练照片`;
  } catch { /* Capture reports any server error explicitly. */ }
}
async function saveTrainingPhoto() {
  if(S.savingPhoto) return;
  const video=$("camera"), button=$("save-training-photo");
  if(!S.stream?.active || S.media!==video || video.readyState<2 || !video.videoWidth)
    throw new Error("请先打开摄像头，等待实时画面");
  S.savingPhoto=true; button.disabled=true; button.textContent="保存中…";
  try {
    const form=new FormData(); form.append("file",await capture(.95),"camera.jpg");
    const result=await api("/training-photos",{method:"POST",body:form});
    $("training-photo-status").textContent=`已存 ${result.count} 张 · ${result.filename}`;
    toast(`照片已保存，当前共 ${result.count} 张`);
  } finally {
    S.savingPhoto=false; button.textContent="拍照并保存"; button.disabled=!S.stream?.active;
  }
}
async function observe(task) {
  const id=selected(); if(S.observing) return;
  const cloud=(task==="objects"?S.config.providers.vision_provider:S.config.providers.ocr_provider)==="vlm";
  if(cloud&&!$("cloud-consent").checked) throw new Error("请先确认允许将画面发送至已配置的视觉服务");
  if(task==="ocr"&&!S.roi) throw new Error("请先拖动选择显示屏区域 ROI");
  S.observing=true;
  try {
    const form=new FormData(); form.append("file",await capture(),"frame.jpg"); form.append("task",task); if(S.roi) form.append("roi",S.roi.join(","));
    if(task==="ocr" && $("meter-range").value) {
      if(!$("meter-range-confirm").checked) throw new Error("请先现场核对并确认所选直流电压档位");
      form.append("meter_range",$("meter-range").value); form.append("meter_range_confirmed","true");
    }
    const result=await api(`/sessions/${id}/image`,{method:"POST",body:form});
    if(S.session?.id!==id) return;
    sync(result.session); $("perception-output").textContent=JSON.stringify(result.perception,null,2); $("perception-output").classList.remove("hidden");
    await refreshAudit(); if(!S.timer||!result.accepted) toast(result.message,!result.accepted);
  } finally { S.observing=false; if(task==="ocr") $("meter-range-confirm").checked=false; }
}
function stopObservation() { clearInterval(S.timer); S.timer=null; $("observe-loop").textContent="连续观察"; }
async function toggleObservation() {
  if(S.timer) {stopObservation();return;}
  selected(); if(!S.media) throw new Error("请先载入实验画面");
  $("observe-loop").textContent="停止连续观察";
  const tick=async()=>{ try { await observe(S.session.step===6 ? "ocr":"objects"); } catch(error) {stopObservation();toast(error.message,true);} };
  S.timer=setInterval(tick,5000); await tick();
}
async function toggleRecording() {
  if(S.recorder?.state==="recording") { S.recorder.stop(); return; }
  if(!S.config.providers.asr_configured) throw new Error("语音服务未配置，请填写 .env 中的 ASR 项");
  S.audioStream=await navigator.mediaDevices.getUserMedia({audio:true});
  const chunks=[], recorder=new MediaRecorder(S.audioStream); S.recorder=recorder;
  recorder.ondataavailable=e=>{if(e.data.size) chunks.push(e.data);};
  recorder.onstop=()=>attempt(async()=>{
    clearTimeout(S.audioTimeout); S.audioStream.getTracks().forEach(t=>t.stop()); $("record-audio").textContent="录音转文字";
    const mime=recorder.mimeType||"audio/webm", form=new FormData(); form.append("file",new Blob(chunks,{type:mime}),"recording.webm");
    const result=await api("/audio/transcribe",{method:"POST",body:form}); $("chat-input").value=result.text; toast("已转文字，请核对后发送");
  });
  recorder.start(); $("record-audio").textContent="停止录音"; S.audioTimeout=setTimeout(()=>{if(recorder.state==="recording")recorder.stop();},60000);
}
async function download(format) {
  const response=await fetch(`/api/sessions/${selected()}/export?format=${format}`,{headers:authHeaders()}); if(!response.ok) throw new Error("导出失败，请检查访问令牌");
  const url=URL.createObjectURL(await response.blob()), link=document.createElement("a"); link.href=url; link.download=`LabAgent-${S.session.id}.${format}`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function boot() {
  S.config=await api("/health"); S.spec=await api("/experiment");
  $("connection").textContent="服务在线"; $("connection").className="pill good";
  $("provider-status").textContent=JSON.stringify(S.config.providers,null,2);
  $("agent-mode").textContent=S.config.providers.chat_provider==="api"?"模型模式":"本地规则 / 检索";
  $("equipment-checks").replaceChildren();
  for(const [key,value] of Object.entries(S.spec.object_labels)) {const label=document.createElement("label"), box=document.createElement("input");label.className="check";box.type="checkbox";box.value=key;box.className="equipment-checkbox";label.append(box,document.createTextNode(value));$("equipment-checks").append(label);}
  const fixtures=await api("/fixtures"); $("fixture").replaceChildren(); for(const fixture of fixtures) {const option=document.createElement("option");option.value=fixture.id;option.textContent=fixture.name;$("fixture").append(option);}
  await refreshList(); const previous=sessionStorage.getItem("labagent_session");
  if(previous) {try {await selectSession(previous);} catch{sessionStorage.removeItem("labagent_session");}}
}

$("new-session").addEventListener("click",()=>attempt(async()=>{const state=await post("/sessions",{title:"低压模拟传感器电压实验",mode:"live"});await selectSession(state.id);}));
$("meter-range").addEventListener("change",()=>{$("meter-range-confirm").checked=false;});
$("run-fixture").addEventListener("click",()=>attempt(async()=>{const result=await post(`/fixtures/${$("fixture").value}`,{});await selectSession(result.session.id);toast("回放结束："+result.results.filter(x=>x.accepted).length+" 项事件被接受；查看日志核对预期");}));
$("replay-file").addEventListener("change",event=>attempt(async()=>{const file=event.target.files[0];if(!file)return;const result=await post("/replays",JSON.parse(await file.text()));await selectSession(result.session.id);}));
document.querySelectorAll(".human-action").forEach(button=>button.addEventListener("click",()=>attempt(()=>humanAction(button.dataset.kind,button.dataset.kind==="confirm_equipment"?{objects:[...document.querySelectorAll(".equipment-checkbox:checked")].map(box=>box.value)}:{}))));
$("advance").addEventListener("click",()=>attempt(()=>doAction("advance")));
$("pause").addEventListener("click",()=>attempt(()=>doAction("pause")));
$("rewire").addEventListener("click",()=>attempt(async()=>{if(confirm("接线变化会暂停实验并使原有连接确认失效。请先人工断电，再重新检查。"))await doAction("invalidate_wiring");}));
$("add-measurement").addEventListener("click",()=>attempt(async()=>{if(!$("voltage").value.trim()||!Number.isFinite(Number($("voltage").value)))throw new Error("请输入实际读取的电压值");await humanAction("manual_measurement",{value:Number($("voltage").value)});}));
$("save-measurement").addEventListener("click",()=>attempt(()=>humanAction("save_measurement")));
$("remeasure").addEventListener("click",()=>attempt(()=>doAction("remeasure")));
$("chat-form").addEventListener("submit",event=>{event.preventDefault();attempt(sendChat);});
$("chat-input").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();attempt(sendChat);}});
document.querySelectorAll("[data-question]").forEach(button=>button.addEventListener("click",()=>{$("chat-input").value=button.dataset.question;attempt(sendChat);}));
$("start-camera").addEventListener("click",()=>attempt(startCamera)); $("stop-camera").addEventListener("click",stopMedia);
$("save-training-photo").addEventListener("click",()=>attempt(saveTrainingPhoto));
$("media-file").addEventListener("change",event=>attempt(()=>loadMedia(event.target.files[0])));
$("detect").addEventListener("click",()=>attempt(()=>observe("objects")));$("ocr").addEventListener("click",()=>attempt(()=>observe("ocr")));$("observe-loop").addEventListener("click",()=>attempt(toggleObservation));
$("roi-overlay").addEventListener("pointerdown",event=>{S.drag=pointerPosition(event);event.target.setPointerCapture(event.pointerId);});
$("roi-overlay").addEventListener("pointermove",event=>{if(!S.drag)return;const p=pointerPosition(event);S.roi=[Math.min(S.drag.x,p.x),Math.min(S.drag.y,p.y),Math.abs(S.drag.x-p.x),Math.abs(S.drag.y-p.y)];drawROI();});
$("roi-overlay").addEventListener("pointerup",()=>{S.drag=null;});
$("record-audio").addEventListener("click",()=>attempt(toggleRecording));
document.querySelectorAll("[data-export]").forEach(button=>button.addEventListener("click",()=>attempt(()=>download(button.dataset.export))));
$("settings-button").addEventListener("click",()=>{$("settings-dialog").showModal();});$("close-settings").addEventListener("click",()=>$("settings-dialog").close());
$("save-token").addEventListener("click",()=>attempt(async()=>{sessionStorage.setItem("labagent_access_token",$("access-token").value.trim());await boot();$("settings-dialog").close();}));
$("knowledge-file").addEventListener("change",event=>attempt(async()=>{const file=event.target.files[0];if(!file)return;const form=new FormData();form.append("file",file);const result=await api("/knowledge/upload",{method:"POST",body:form});toast(`已导入知识库：${result.source}`);}));
window.addEventListener("beforeunload",()=>{stopMedia();if(S.audioStream)S.audioStream.getTracks().forEach(t=>t.stop());});
setInterval(async()=>{if(!S.session||S.polling||document.hidden)return;S.polling=true;const id=S.session.id;try{const state=await api(`/sessions/${id}`);if(S.session?.id===id)sync(state);}catch{/* explicit operations show actionable errors */}finally{S.polling=false;}},3000);
attempt(boot);
