"use strict";
const byId=id=>document.getElementById(id);
const video=byId("video"), start=byId("start"), stop=byId("stop"), save=byId("save");
let stream=null, saving=false;
const status=message=>{ byId("status").textContent=message; };

async function request(options={}) {
  const token=sessionStorage.getItem("labagent_access_token");
  const headers=token?{Authorization:"Bearer "+token}:{};
  const response=await fetch("/api/training-photos",{...options,headers});
  const data=await response.json();
  if(response.status===401) byId("auth").classList.remove("hidden");
  if(!response.ok) throw new Error(typeof data.detail==="string"?data.detail:`请求失败 (${response.status})`);
  return data;
}
const count=n=>{ byId("total").textContent=`已存 ${n} 张`; };
async function refresh() {
  try { count((await request()).count); byId("auth").classList.add("hidden"); }
  catch(e) { byId("total").textContent="数量暂不可用"; status(e.message); }
}
function closeCamera() {
  if(stream) stream.getTracks().forEach(track=>track.stop());
  stream=null; video.srcObject=null; video.classList.add("hidden"); byId("placeholder").classList.remove("hidden");
  start.disabled=false; save.disabled=true; stop.disabled=true;
}
start.onclick=async()=>{
  start.disabled=true;
  try {
    if(!navigator.mediaDevices?.getUserMedia) throw new Error("请在开发板浏览器打开本机 LabAgent 页面，再允许摄像头权限");
    stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1920},height:{ideal:1080}},audio:false});
    video.srcObject=stream; await video.play();
    video.classList.remove("hidden"); byId("placeholder").classList.add("hidden");
    save.disabled=saving; stop.disabled=false;
    status("摆好器材后，点「拍照并保存」。");
  } catch(e) { closeCamera(); status("打不开摄像头："+e.message); }
};
stop.onclick=closeCamera;
save.onclick=async()=>{
  if(saving||!stream?.active||video.readyState<2||!video.videoWidth) return;
  saving=true; save.disabled=true; save.textContent="保存中…";
  try {
    const canvas=document.createElement("canvas");
    canvas.width=video.videoWidth; canvas.height=video.videoHeight;
    canvas.getContext("2d").drawImage(video,0,0);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg",.95));
    if(!blob) throw new Error("无法截取当前画面");
    const form=new FormData(); form.append("file",blob,"camera.jpg");
    const result=await request({method:"POST",body:form});
    count(result.count); status(`已保存 ${result.count} 张 · 本次：${result.filename}`);
  } catch(e) { status("未保存："+e.message); }
  finally { saving=false; save.disabled=!stream?.active; save.textContent="拍照并保存"; }
};
byId("auth").onsubmit=event=>{
  event.preventDefault();
  sessionStorage.setItem("labagent_access_token",byId("token").value.trim());
  byId("token").value=""; status(""); void refresh();
};
window.addEventListener("pagehide",closeCamera);
void refresh();
