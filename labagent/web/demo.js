"use strict";
const $=id=>document.getElementById(id);
const S={session:null,plan:null,config:null,media:null,stream:null,url:null,roi:null,drag:null,timer:null,
  agentStage:null,agentActionText:null,agentSceneSignature:null,agentSceneCandidate:null,agentSceneTimer:null,agentRunBusy:false,agentRequestController:null,agentWaiting:null,liveEquipment:true,liveTimer:null,liveController:null,liveRunning:false,equipmentPreview:null,observing:false,polling:false,busy:false,equipmentKey:null,candidateKey:null,candidateUrl:null,recorder:null,audio:null,autoEnabled:true,autoReplay:false,autoRunning:false,autoTimer:null,nextObserveAt:0,autoErrors:0,equipmentDirty:false,equipmentEdited:new Set(),boxes:[],boxTime:0};
const sourceNames={manual:"人工核对",vision_workflow:"视觉识别 · 工作流稳定性核验",vision_confirmed:"视觉识别 · 人工核对",ocr:"OCR",serial:"设备采集",replay:"软件演练"};
const gateNames={read_sop:"阅读要求",equipment:"器材核对",meter_setup:"仪表设置",manual_check:"现场核对",measurement:"测量",save:"保存",report:"报告"};
const eventNames={"agent.model_decision":"Agent选择行动","agent.execution_result":"Agent工具反馈","agent.execute":"Agent执行","agent.plan_updated":"调整实验计划","agent.decision_error":"Agent决策未完成","agent.recheck":"Agent返回复查","session.paused":"实验已暂停","session.created":"建立实验目标","user.action":"现场确认与执行","agent.action":"Agent 推进与验证","agent.decision":"Agent 选择感知工具","perception.objects":"器材观察","workflow.visual_check":"工作流视觉检查","perception.reading":"读取显示屏","agent.tool_result":"助手工具反馈","agent.tool":"助手执行","plan.attached":"载入文档计划"};
let toastTimer;
function toast(text,error=false){clearTimeout(toastTimer);$("toast").textContent=text;$("toast").className="toast"+(error?" error":"");toastTimer=setTimeout(()=>$("toast").classList.add("hidden"),6500);}
function headers(){const token=sessionStorage.getItem("labagent_access_token");return token?{Authorization:"Bearer "+token}:{};}
async function api(path,options={}){const h={...headers(),...options.headers};if(options.body&&!(options.body instanceof FormData))h["Content-Type"]="application/json";const r=await fetch("/api"+path,{...options,headers:h});if(!r.ok){let message=`请求失败（${r.status}）`;try{const d=(await r.json()).detail;message=typeof d==="string"?d:Array.isArray(d)?d.map(x=>x.msg).join("；"):message;}catch{}if(r.status===401)$("settings-dialog").showModal();const error=new Error(message);error.status=r.status;throw error;}return r.json();}
const post=(path,body={})=>api(path,{method:"POST",body:JSON.stringify(body)});
async function attempt(fn){try{return await fn();}catch(e){toast(e.message,true);return null;}}
function elem(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function gate(){return S.session?.steps[S.session.step]?.gate;}
function selected(){if(!S.session)throw new Error("请先启用一个实验计划");return S.session.id;}
function toggle(id,show){$(id).classList.toggle("hidden",!show);}

async function loadPlans(preferred){const plans=await api("/plans"),select=$("plan-select");select.replaceChildren();for(const p of plans){const option=elem("option",p.title+(p.approved?" · 已审核":" · 待审核"));option.value=p.id;select.append(option);}select.value=preferred||S.plan?.id||"battery-voltage-15v";if(!select.value&&plans.length)select.value=plans[0].id;if(select.value)await selectPlan(select.value);}
async function selectPlan(id){S.plan=await api(`/plans/${id}`);renderPlan();renderPlanReadiness();}
function renderPlan(){const p=S.plan;if(!p)return;$("plan-summary").textContent=`${p.title}\n${p.steps.length} 个步骤 · ${p.required_objects.length} 类器材\n${p.objective}\n${p.approved?"已审核计划，可再次核对后开始":"待审核：请对照原文检查拆解结果"}\n${[...(p.questions||[]).map(x=>"待澄清："+x),...p.review_notes].join("\n")}`;$("plan-title").value=p.title;$("plan-questions").value=(p.questions||[]).join("\n");$("plan-equipment").value=p.required_objects.map(x=>p.object_labels[x]).join("，");const m=p.measurement;$("plan-unit").value=m?.unit||"";$("plan-min").value=m?.min_value??"";$("plan-max").value=m?.max_value??"";$("plan-samples").value=m?.samples||1;for(const id of ["plan-unit","plan-min","plan-max","plan-samples"])$(id).disabled=!m;
 $("plan-steps").replaceChildren();p.steps.forEach((s,i)=>{const li=elem("li"),title=elem("input"),instruction=elem("textarea"),type=elem("select");title.value=s.title;title.dataset.field="title";instruction.value=s.instruction;instruction.rows=2;instruction.dataset.field="instruction";for(const [key,label]of Object.entries(gateNames)){const o=elem("option",label);o.value=key;type.append(o);}type.value=s.gate;type.dataset.field="gate";li.dataset.index=i;li.append(title,instruction,type,elem("small",s.origin==="platform"?"平台补充的核对步骤":s.source_line?`要求文档第 ${s.source_line} 行`:s.origin==="model_suggestion"?"本地模型提出的建议，需对照原文":"实验计划"));$("plan-steps").append(li);});}
async function importPlan(){
 const file=$("document-file").files[0],goal=$("experiment-goal").value.trim(),text=$("requirements").value.trim();
 if(!file&&!goal&&!text)throw new Error("请输入目标，或上传实验说明");
 const form=new FormData();if(file)form.append("file",file);if(goal)form.append("goal",goal);
 form.append("requirements",text);form.append("method",$("compile-method").value);form.append("planner","agent");
 $("import-plan").disabled=true;$("start-plan").disabled=true;
 $("import-status").textContent="正在理解目标和实验说明、生成待审核计划。请等待模型完成。";
 try{const p=await api("/plans/import",{method:"POST",body:form});await loadPlans(p.id);$("plan-details").open=true;
 $("import-status").textContent=p.questions?.length?"计划有待澄清事项，请补充要求后重新生成。":`已生成 ${p.steps.length} 个目标，请核对后开始。`;
 toast("计划已生成；尚未执行任何现场操作");
 }catch(e){$("import-status").textContent=e.message;throw e;}
 finally{$("import-plan").disabled=false;renderPlanReadiness();}
}
async function savePlan(){const p=structuredClone(S.plan);p.title=$("plan-title").value.trim();p.questions=$("plan-questions").value.split("\n").map(x=>x.trim()).filter(Boolean);const names=$("plan-equipment").value.split(/[,，、]/).map(x=>x.trim()).filter(Boolean);const old=Object.entries(p.object_labels);p.object_labels={};p.required_objects=[];names.forEach((name,i)=>{const existing=old.find(([,v])=>v===name),key=existing?existing[0]:`equipment_${i+1}`;p.object_labels[key]=name;p.required_objects.push(key);});document.querySelectorAll("#plan-steps li").forEach(li=>{const s=p.steps[Number(li.dataset.index)];for(const f of ["title","instruction","gate"])s[f]=li.querySelector(`[data-field=${f}]`).value;});if(p.measurement){p.measurement.unit=$("plan-unit").value.trim();p.measurement.min_value=Number($("plan-min").value);p.measurement.max_value=Number($("plan-max").value);p.measurement.samples=Number($("plan-samples").value);}await api(`/plans/${p.id}`,{method:"PUT",body:JSON.stringify(p)});await loadPlans(p.id);toast("计划已保存，开始前请重新审核");}
async function startPlan(){
 if(S.starting||S.switching||S.deletingSession)return;
 if(!S.plan)throw new Error('计划正在加载，请稍候再开始。');
 S.starting=true;stopSessionWork();$("start-plan").disabled=true;
 try{
  await post(`/plans/${S.plan.id}/approve`,{confirmed:true});
  const s=await post("/sessions",{title:S.plan.title,mode:"live",plan_id:S.plan.id,automation:$("agent-enabled").checked?"agent":"workflow"});
  sessionStorage.setItem('labagent_auto_'+s.id,'true');
  await selectSession(s.id,{fresh:true});toggle("plan-setup",false);
  if(gate()==='read_sop')await action("confirm_step",{confirmed:true,advance_after:true,note:"操作人员核对计划与现场条件，点击按此计划开始"});
  toast("实验已开始，将自动检查并推进；需要你操作时会在顶部提示。");
 }finally{S.starting=false;$("start-plan").disabled=false;renderTaskStatus();scheduleAuto();await refreshList();}
}
async function refreshList(){const list=await api("/sessions");$("sessions").replaceChildren();for(const s of list)$("sessions").append(sessionListRow(s));}
async function selectSession(id,{fresh=false}={}){
 if(S.switching||S.deletingSession||(S.starting&&!fresh))return;
 const previous=S.session?.id,epoch=(S.selectionEpoch||0)+1;S.selectionEpoch=epoch;S.switching=true;stopSessionWork();
 try{
 const loaded=fresh?await api(`/sessions/${id}`):await post(`/sessions/${id}/open`,{previous_session_id:previous||null});
 if(S.selectionEpoch!==epoch)return;S.session=loaded;S.equipmentPreview=null;
 S.equipmentKey=null;S.candidateKey=null;S.equipmentDirty=false;S.equipmentEdited.clear();S.boxes=[];S.nextObserveAt=0;
 toggle('plan-setup',false);
 S.autoEnabled=fresh;S.autoReplay=false;S.runError=null;S.lastRetryReason=null;S.disconnected=false;S.meterEdited=false;
 $("equipment-editor").open=false;sessionStorage.setItem("labagent_demo_session",id);render();
 await Promise.all([refreshMessages(),refreshAudit(),refreshList()]);
 }finally{if(S.selectionEpoch===epoch){S.switching=false;render();await refreshList();}}
}
function sync(s){if(S.session?.id!==s.id||s.revision<S.session.revision)return;if(s.step!==S.session.step){S.nextObserveAt=0;S.autoErrors=0;S.lastRetryReason=null;S.stepChangedAt=Date.now();S.stepNotice=s.steps[s.step].title;}S.session=s;render();}
function render(){const s=S.session;if(!s)return;toggle("empty",false);toggle("active",true);const current=s.steps[s.step],g=current.gate;$("session-title").textContent=s.title;$("session-description").textContent=`独立计划 · ${s.plan?.input_mode==="goal"?"输入目标":s.plan?.source_filename||"兼容流程"} · 证据本地保存`;$("mode").textContent=s.mode==="replay"?"软件演练 · 非真实测量":"真实实验";$("mode").className="pill "+(s.mode==="replay"?"replay":"good");$("safety-text").textContent=s.warning;$("step-summary").textContent=current.title;$("run-status").textContent={running:"进行中",paused:"已暂停，待复核",completed:"实验完成"}[s.status];const m=[...s.measurements].reverse().find(x=>!x.superseded);$("latest-reading").textContent=m?`${m.value} ${m.unit}`:"尚无已确认读数";$("latest-source").textContent=m?sourceNames[m.source]:"—";
 $("steps").replaceChildren();s.steps.forEach((item,i)=>{const done=i<s.step||s.status==="completed",li=elem("li",undefined,done?"done":i===s.step?"current":"");li.append(elem("span",done?"✓":String(i+1),"step-dot"),elem("span",item.title));$("steps").append(li);});$("step-count").textContent=`${s.step+1} / ${s.steps.length}`;$("current-step").textContent=current.title;$("instruction").textContent=current.instruction;$("blockers").replaceChildren(...s.blockers.map(x=>elem("li",x)));
 const decisions={read_sop:"先确认文档要求，再开始操作。",equipment:"核对所需器材；缺少器材时保持在本步，补齐后再次检查。",meter_setup:"需要现场核对档位和插孔，视觉模型不能代替此确认。",manual_check:"等待现场操作完成，再验证后续条件。",measurement:"调用读表工具获取候选，核对后才能形成正式测量。",save:"检查已有测量及来源，明确保存后再收尾。",report:"根据真实记录生成报告，保留异常和修正信息。"};$("agent-decision").textContent=s.status==="completed"?"目标已完成。可以导出报告，或保留历史后重新测量。":s.status==="paused"?s.pause_reason:s.blockers.length?decisions[g]||"等待当前条件满足。":"本步条件已满足，建议验证并进入下一步。";
 $("advance").textContent=g==="report"?"验证并完成实验 →":"验证并进入下一步 →";$("advance").disabled=s.status!=="running"||S.observing;$("pause").disabled=s.status!=="running";toggle("resume",s.status==="paused");toggle("equipment-panel",g==="equipment");toggle("meter-panel",g==="meter_setup");toggle("measurement-panel",g==="measurement");toggle("save-measurement",g==="save");toggle("confirm-current",["read_sop","manual_check","meter_setup"].includes(g));toggle("agent-observe",(["equipment","measurement"].includes(g)||Boolean(s.agent_decision?.visual_rule))&&s.status==="running"&&(g!=="measurement"||s.plan?.measurement?.adapter==="voltage"));$("detect").disabled=g!=="equipment"||S.observing||s.status!=="running";$("ocr").disabled=g!=="measurement"||s.plan?.measurement?.adapter!=="voltage"||S.observing||s.status!=="running";$("confirm-current").disabled=s.status!=="running";$("confirm-equipment").disabled=s.status!=="running";$("manual-measurement").disabled=s.status!=="running";$("save-measurement").disabled=s.status!=="running";$("remeasure").disabled=!s.plan?.measurement||!["save","report","measurement","manual_check"].includes(g);$("manual-label").textContent=`人工读取 ${s.plan?.measurement?.quantity||"数值"} / ${s.plan?.measurement?.unit||"V"}`;$("risk").textContent=s.risk;toggle("risk",Boolean(s.risk));
 renderEquipmentChecklist(s);if(s.facts.meter_range&&!S.meterEdited)$("meter-range").value=s.facts.meter_range.value;
 $("measurement-history").replaceChildren();for(const v of [...s.measurements].reverse()){const tr=elem("tr");for(const text of [`${v.value} ${v.unit}`,sourceNames[v.source],v.superseded?"已被替代":v.saved?"已保存":"已确认，待保存",new Date(v.at).toLocaleTimeString()])tr.append(elem("td",text));$("measurement-history").append(tr);}if(!s.measurements.length){const tr=elem("tr"),td=elem("td","等待真实输入");td.colSpan=4;tr.append(td);$("measurement-history").append(tr);}if(s.agent_decision)$("agent-decision").textContent=S.equipmentDirty&&g==="equipment"?"已保留你的器材修正。请核对后最终确认提交，我再继续。":s.agent_decision.message;
 if(g!=="equipment")S.boxes=[];drawObjects();
 $("observe-loop").textContent=S.autoEnabled&&(s.mode==="live"||S.autoReplay)?"暂停自动指导":"恢复自动指导";
 if(S.autoRunning)$("advance").disabled=true;
 renderWorkflow();renderCandidate();renderAgentPanel();renderTaskStatus();scheduleAuto();renderEquipmentLive();renderMeterLive();}
async function renderCandidate(){const c=[...(S.session?.candidates||[])].reverse().find(x=>x.status==="pending");toggle("candidate-panel",Boolean(c));document.querySelector(".observation").classList.toggle("has-candidate",Boolean(c));if(!c){S.candidateKey=null;return;}const key=c.id+"/"+c.status;if(S.candidateKey===key)return;S.candidateKey=key;$("candidate-value").value=c.value??"";$("candidate-reason").textContent=c.reason;$("candidate-raw").textContent=c.raw_text;$("candidate-status").textContent=c.value===null?"等待自动重拍":S.autoEnabled?"连续读数核验中":"等待人工核对";try{const r=await fetch(`/api/sessions/${S.session.id}/candidates/${c.id}/image`,{headers:headers()});if(!r.ok)throw new Error();const blob=await r.blob();if(S.candidateKey!==key)return;if(S.candidateUrl)URL.revokeObjectURL(S.candidateUrl);S.candidateUrl=URL.createObjectURL(blob);$("candidate-image").src=S.candidateUrl;}catch{$("candidate-image").removeAttribute("src");}}
async function action(kind,extra={}){const id=selected();try{const r=await post(`/sessions/${id}/actions`,{kind,expected_revision:S.session.revision,...extra});if(S.session?.id===id)sync(r.session);toast(r.message,!r.accepted);await Promise.all([refreshAudit(),refreshList()]);return r;}catch(e){if(S.session?.id===id)sync(await api(`/sessions/${id}`));throw e;}}
async function humanAction(kind,extra={}){
 if(S.actionBusy)return;
 cancelAgentRequest();S.actionBusy=true;S.runError=null;renderTaskStatus();
 try{const merged=['confirm_step','confirm_equipment','confirm_candidate','manual_measurement','save_measurement'].includes(kind);
 const result=await action(kind,{confirmed:true,advance_after:merged,...extra});
 if(result.accepted){S.autoEnabled=true;S.agentWaiting=null;S.nextObserveAt=0;
  if(kind==='confirm_equipment'){S.equipmentDirty=false;S.equipmentEdited.clear();$('equipment-editor').open=false;}
 }
 return result;}finally{S.actionBusy=false;render();}
}
async function refreshAudit(){const id=selected(),events=await api(`/sessions/${id}/events`);if(S.session?.id!==id)return;$("audit").replaceChildren();for(const e of events.slice(-45).reverse()){const result=e.payload.result;const text=result?.message||e.payload.reason||"已记录";$("audit").append(elem("div",`${new Date(e.at).toLocaleTimeString()} · ${eventNames[e.kind]||e.kind} · ${text}`,result?.accepted===false?"denied":""));}}
async function refreshMessages(){const id=selected(),items=await api(`/sessions/${id}/messages`);if(S.session?.id!==id)return;$("messages").replaceChildren();for(const m of items){const d=elem("div",undefined,"message "+m.role);d.append(elem("span",m.role==="user"?"你":"LABAGENT","author"),document.createTextNode(m.content));$("messages").append(d);}if(!items.length)$("messages").append(elem("div","我会结合本次实验文档、当前步骤和已记录的现场状态进行指导。可先问‘现在该做什么？’。","message assistant"));$("messages").scrollTop=$("messages").scrollHeight;}
async function sendChat(){
 const id=selected(),epoch=S.selectionEpoch,message=$('chat-input').value.trim();if(!message||S.busy)return;
 const controller=new AbortController();S.chatRequestController=controller;S.busy=true;$('send-message').disabled=true;
 const list=$('messages');
 for(const[role,content]of [['user',message],['assistant','正在读取实验记录并回复…']]){
  const bubble=elem('div',undefined,'message '+role);bubble.dataset.chatPending='true';
  bubble.append(elem('span',role==='user'?'你':'LABAGENT','author'),document.createTextNode(content));list.append(bubble);
 }
 list.scrollTop=list.scrollHeight;$('chat-tools').textContent='正在回复…';$('chat-input').value='';
 try{
  const r=await api(`/sessions/${id}/chat`,{method:'POST',body:JSON.stringify({message}),signal:controller.signal});
  if(controller.signal.aborted||S.session?.id!==id||S.selectionEpoch!==epoch)return;
  sync(r.session);$('agent-mode').textContent={api:'本地模型',workflow:'状态指导',rules:'规则与检索',unavailable:'模型暂不可用'}[r.engine]||r.engine;
  $('chat-tools').replaceChildren(document.createTextNode(r.traces.map(t=>t.tool).join(' → ')));
  for(const p of r.proposals||[]){const b=elem('button',p.kind==='update_sampling'?`将采样目标调整为 ${p.samples} 次`:'确认助手建议','secondary');b.onclick=()=>attempt(()=>applyAgentProposal(p));$('chat-tools').append(b);}
  await Promise.all([refreshMessages(),refreshAudit()]);
 }catch(error){
  if(error.name==='AbortError'||S.session?.id!==id||S.selectionEpoch!==epoch)return;
  list.querySelectorAll('[data-chat-pending]').forEach(node=>node.remove());$('chat-input').value=message;
  $('chat-tools').textContent='本次发送未完成，可重试。';throw error;
 }finally{
  if(S.chatRequestController===controller){S.chatRequestController=null;S.busy=false;$('send-message').disabled=false;scheduleAuto();}
 }
}

function stopObservation(){clearInterval(S.timer);S.timer=null;$("observe-loop").textContent="连续观察";}
function stopMedia(preserveAgent=false){
 if(!preserveAgent&&typeof stopMeterLive==='function')stopMeterLive();
 if(!preserveAgent)cancelAgentRequest();stopEquipmentPreview();S.equipmentPreview=null;stopObservation();clearTimeout(S.autoTimer);if(S.stream)S.stream.getTracks().forEach(t=>t.stop());S.stream=null;
 $("camera").pause();$("camera").srcObject=null;S.media=null;S.roi=null;S.boxes=[];
 if(S.url)URL.revokeObjectURL(S.url);S.url=null;
 for(const id of ["camera","preview","roi-overlay","object-overlay"])toggle(id,false);
 toggle("media-placeholder",true);$("roi-label").textContent="等待自动定位";
}
function overlay(w,h){
 for(const id of ["roi-overlay","object-overlay"]){$(id).width=w;$(id).height=h;}
 toggle("roi-overlay",true);toggle("media-placeholder",false);S.roi=null;S.boxes=[];$("roi-label").textContent="等待自动定位";
}
async function startCamera(preserveAgent=false){
 if(!navigator.mediaDevices?.getUserMedia)throw new Error("请在 O6 本机打开 http://127.0.0.1:8001，并允许相机权限");
 stopMedia(preserveAgent);const epoch=S.selectionEpoch;
 const stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:1920},height:{ideal:1080}},audio:false});
 if(epoch!==S.selectionEpoch){stream.getTracks().forEach(t=>t.stop());throw new DOMException("实验已切换","AbortError");}
 S.stream=stream;const v=$("camera");v.srcObject=stream;toggle("camera",true);let deadline;
 try{await Promise.race([v.play(),new Promise((_,reject)=>{deadline=setTimeout(()=>reject(new DOMException("摄像头未返回画面，请检查设备占用或重新打开","NotReadableError")),10000);})]);}
 catch(error){stream.getTracks().forEach(t=>t.stop());throw error;}
 finally{clearTimeout(deadline);}
 if(epoch!==S.selectionEpoch||S.stream!==stream){stream.getTracks().forEach(t=>t.stop());throw new DOMException("实验已切换","AbortError");}
 S.media=v;overlay(v.videoWidth,v.videoHeight);renderEquipmentLive();
}
async function loadImage(file){if(!file)return;stopMedia();S.url=URL.createObjectURL(file);const img=$("preview");img.src=S.url;await img.decode();toggle("preview",true);S.media=img;overlay(img.naturalWidth,img.naturalHeight);}
function point(e){const r=$("roi-overlay").getBoundingClientRect();return{x:Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))};}
function drawROI(){const c=$("roi-overlay"),ctx=c.getContext("2d");ctx.clearRect(0,0,c.width,c.height);if(!S.roi)return;const[x,y,w,h]=S.roi;ctx.strokeStyle="#22a087";ctx.fillStyle="#22a08722";ctx.lineWidth=Math.max(2,c.width/300);ctx.fillRect(x*c.width,y*c.height,w*c.width,h*c.height);ctx.strokeRect(x*c.width,y*c.height,w*c.width,h*c.height);$("roi-label").textContent="显示屏已自动定位";}
async function capture(){
 const media=S.media;if(!media)throw new Error("请先打开摄像头或载入实拍图");
 let freshVideo=false;
 if(media===$('camera')&&S.stream?.active&&typeof media.requestVideoFrameCallback==='function'){
  await new Promise((resolve,reject)=>{
   const handle=media.requestVideoFrameCallback(()=>{clearTimeout(timer);resolve();});
   const timer=setTimeout(()=>{media.cancelVideoFrameCallback(handle);reject(new Error('摄像头未返回新帧，请检查连接'));},3000);
  });
  if(S.media!==media||!S.stream?.active)throw new DOMException('相机或实验已切换','AbortError');
  freshVideo=true;
 }
 const c=document.createElement('canvas');c.width=media.videoWidth||media.naturalWidth;c.height=media.videoHeight||media.naturalHeight;
 c.getContext('2d').drawImage(media,0,0);
 const blob=await new Promise((ok,bad)=>c.toBlob(b=>b?ok(b):bad(new Error('无法截取画面')),'image/jpeg',.92));
 if(freshVideo)blob.captureId=crypto.randomUUID().replaceAll('-','');
 return blob;
}
async function observe(task,automatic=false){
 const id=selected();if(S.observing)return;
 const control=new AbortController(),epoch=S.selectionEpoch;S.observationController=control;
 const captured=Date.now(),form=new FormData(),frame=await capture();
 if(control.signal.aborted||S.session?.id!==id||S.selectionEpoch!==epoch)return;
 form.append("file",frame,"frame.jpg");
 if(frame.captureId)form.append('capture_id',frame.captureId);
 form.append("task",task);form.append("automatic",String(automatic));
 S.observing=true;S.observationTask=task;S.agentStartedAt=Date.now();render();$("observation-status").textContent=task==="reading"?"正在定位显示屏并核验读数…":task==="step"?"正在检查本步条件，请保持画面清晰…":"正在核对器材…";
 try{const r=await api(`/sessions/${id}/observe`,{method:"POST",body:form,signal:control.signal});if(control.signal.aborted||S.session?.id!==id)return;
 if(r.present)acceptEquipmentPreview(r,captured);
 if(r.roi){S.roi=r.roi;drawROI();}sync(r.session);drawObjects();
 $("observation-status").textContent=r.message;await refreshAudit();
 }finally{if(S.observationController===control){S.observationController=null;S.observing=false;S.observationTask=null;S.agentStartedAt=null;S.nextObserveAt=S.session?.agent_decision?.tool==="advance"?0:Date.now()+1200;render();}}
}
async function toggleObservation(){
 const active=S.autoEnabled&&(S.session?.mode==="live"||S.autoReplay);
 S.autoEnabled=!active;if(!S.autoEnabled)cancelAgentRequest();S.agentWaiting=null;if(S.autoEnabled&&S.session?.mode==="replay")S.autoReplay=true;
 if(S.autoEnabled){S.runError=null;S.lastRetryReason=null;S.nextObserveAt=0;}
 S.nextObserveAt=0;clearTimeout(S.autoTimer);render();
 toast(S.autoEnabled?"自动指导已恢复，将按当前状态选择工具":"自动指导已暂停，可手动观察或核对");
}
async function recordAudio(){if(S.recorder?.state==="recording"){S.recorder.stop();return;}if(!navigator.mediaDevices?.getUserMedia)throw new Error("请使用 O6 本机浏览器并允许麦克风权限");S.audio=await navigator.mediaDevices.getUserMedia({audio:true});const parts=[],rec=new MediaRecorder(S.audio);S.recorder=rec;rec.ondataavailable=e=>{if(e.data.size)parts.push(e.data);};let timeout;rec.onstop=()=>attempt(async()=>{clearTimeout(timeout);S.audio.getTracks().forEach(t=>t.stop());$("record-audio").textContent="转写中…";$("record-audio").disabled=true;try{const mime=rec.mimeType||"audio/webm",f=new FormData();f.append("file",new Blob(parts,{type:mime}),"question.webm");const r=await api("/audio/transcribe",{method:"POST",body:f});$("chat-input").value=r.text;toast("已转写，请核对后发送");}finally{$("record-audio").textContent="录音提问";$("record-audio").disabled=false;}});rec.start();$("record-audio").textContent="停止并转写";timeout=setTimeout(()=>{if(rec.state==="recording")rec.stop();},60000);}
async function download(format){const r=await fetch(`/api/sessions/${selected()}/export?format=${format}`,{headers:headers()});if(!r.ok)throw new Error("导出失败");const url=URL.createObjectURL(await r.blob()),a=elem("a");a.href=url;const title=(S.session.title||'实验').replace(/[\\/:*?"<>|]+/g,'-').slice(0,70);a.download=`实验报告-${title}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function services(){const data=await api("/service-status");$("service-status").replaceChildren();for(const[name,s]of Object.entries(data)){const row=elem("div",undefined,"service-row");row.append(elem("span",name),elem("span",s.ready?"已就绪":"未就绪","pill "+(s.ready?"good":"")));$("service-status").append(row);}}
async function boot(){S.config=await api("/health");$("connection").textContent="O6 服务在线";$("connection").className="pill good";await loadPlans();await refreshList();const old=sessionStorage.getItem("labagent_demo_session");if(old){try{await selectSession(old);}catch{sessionStorage.removeItem("labagent_demo_session");}}}

function drawObjects(){
 const c=$("object-overlay"),ctx=c.getContext("2d");ctx.clearRect(0,0,c.width,c.height);
 const stale=S.stream&&Date.now()-S.boxTime>(S.liveEquipment?2500:30000);
 const visible=S.media&&gate()==="equipment"&&S.boxes.length&&!stale;
 toggle("object-overlay",Boolean(visible));if(!visible)return;
 const palette=["#13a780","#e4a52b","#5589e7","#bd71df","#e97b62"];
 ctx.lineWidth=Math.max(3,c.width/300);ctx.font=`600 ${Math.max(16,c.width/55)}px sans-serif`;
 for(const item of S.boxes){
 const [x,y,w,h]=item.bbox,keys=S.session.plan.required_objects,index=keys.indexOf(item.label);
 const color=item.model_class==="red_probe"?"#d94b4b":item.model_class==="black_probe"?"#343a46":palette[Math.max(0,index)%palette.length],name=equipmentBoxName(item);
 ctx.strokeStyle=color;ctx.strokeRect(x*c.width,y*c.height,w*c.width,h*c.height);
 const pad=c.width/220,font=Math.max(16,c.width/55),width=ctx.measureText(name).width+pad*2;
 const tx=Math.max(0,Math.min(x*c.width,c.width-width)),ty=Math.max(font+pad*2,y*c.height);
 ctx.fillStyle=color;ctx.fillRect(tx,ty-font-pad*2,width,font+pad*2);ctx.fillStyle="#fff";
 ctx.fillText(name,tx+pad,ty-pad);
 }
}
function scheduleAuto(){
 if(S.session?.interaction?.phase==='notice'){
  clearTimeout(S.autoTimer);
  if(S.autoEnabled&&S.session.status==='running'&&!S.switching&&!document.hidden){
   const id=S.session.id,epoch=S.selectionEpoch;
   S.autoTimer=setTimeout(()=>attempt(async()=>{if(S.session?.id===id&&S.selectionEpoch===epoch)sync(await api(`/sessions/${id}`));}),Math.max(250,(S.session.interaction.remaining_seconds||1)*1000));
  }return;
 }
 if(meterLiveOwnsStep()){scheduleMeterLive();return;}
 if(S.session?.automation==="agent")return scheduleAgentRun();
 clearTimeout(S.autoTimer);
 const s=S.session,d=s?.agent_decision;
 if(!s||!d?.autorun||!S.autoEnabled||S.starting||S.switching||S.disconnected||(s.mode!=="live"&&!S.autoReplay)||s.status!=="running"||S.observing||S.autoRunning||document.hidden)return;
 if(gate()==="equipment"&&S.equipmentDirty)return;
 S.autoTimer=setTimeout(()=>attempt(runAuto),Math.max(0,S.nextObserveAt-Date.now()));
}
async function runAuto(){
 if(S.session?.automation==="agent")return runAgent();
 const s=S.session,d=s?.agent_decision;
 if(!s||!d?.autorun||!S.autoEnabled||S.starting||S.switching||S.disconnected||S.observing||S.autoRunning||s.status!=="running")return;
 const id=s.id,epoch=S.selectionEpoch;
 S.autoRunning=true;
 try{
 if(["inspect_equipment","read_display","inspect_step"].includes(d.tool)){
 if(!S.media)await startCamera();
 if(S.session?.id!==id||S.selectionEpoch!==epoch||S.switching||!S.autoEnabled)return;
 await observe(d.tool==="inspect_equipment"?"objects":d.tool==="inspect_step"?"step":"reading",true);
 }else if(["advance","report","save_measurement"].includes(d.tool)){
 await action(d.tool,{automatic:true});S.nextObserveAt=0;
 }
 S.autoErrors=0;
 }catch(e){
 if(e.name==='AbortError'||S.session?.id!==id||S.selectionEpoch!==epoch)return;
 if(["NotAllowedError","NotFoundError","NotReadableError"].includes(e.name)){
 S.autoEnabled=false;S.runError="相机不可用，请检查权限或设备占用。";toast("自动观察需要可用相机及权限，请允许相机后恢复自动指导",true);
 }else if(e.status===429||/视觉模型忙/.test(e.message)){S.nextObserveAt=Date.now()+8000;$("observation-status").textContent="本地视觉模型忙，我会稍后自动重试。";
 }else{
 S.autoErrors++;S.nextObserveAt=Date.now()+10000;
 $("observation-status").textContent=`本次自动观察未完成：${e.message}。我将再次尝试；请调整镜头或光照。`;
 if(S.autoErrors>=3){await action("pause",{automatic:true,note:"连续三次自动观察未完成，请复核镜头、模型状态，必要时请教师介入"});S.autoErrors=0;}
 }
 }finally{S.autoRunning=false;render();}
}
document.addEventListener("change",event=>{
 if(event.target.matches(".equipment-part")){syncProbeAggregates();S.equipmentDirty=true;S.equipmentEdited.add(event.target.dataset.parent);clearTimeout(S.autoTimer);render();}
 if(event.target.matches(".equipment-checkbox")){S.equipmentDirty=true;S.equipmentEdited.add(event.target.value);clearTimeout(S.autoTimer);render();}
});
document.addEventListener("visibilitychange",async()=>{renderTaskStatus();if(!document.hidden&&S.session){try{const id=S.session.id;sync(await api(`/sessions/${id}`));S.disconnected=false;}catch{S.disconnected=true;}}scheduleAuto();scheduleEquipmentPreview();renderTaskStatus();});
setInterval(drawObjects,1000);

function renderWorkflow(){
 const s=S.session,d=s.agent_decision,g=gate();
 const automatic=S.autoEnabled&&(s.mode==="live"||S.autoReplay);
 const visual=Boolean(d?.visual_rule),measurement=g==="measurement"&&s.plan?.measurement?.adapter==="voltage";
 toggle("workflow-check-panel",visual||measurement);
 $("workflow-checks").replaceChildren();
 if(visual){
  $("workflow-check-title").textContent=d.checks?.title||"本步自动视觉检查";
  const check=meterStepEnabled()?S.meterResult:d.checks;
  if(check){for(const[key,label]of Object.entries(check.labels)){
   const value=check.checks[key],row=elem("div",undefined,"workflow-check-row");
   row.append(elem("span",label),elem("strong",value===true?"✓ 通过":value===false?"未满足":"待看清",value===true?"check-pass":value===false?"check-fail":"check-unknown"));
   $("workflow-checks").append(row);
  }}else $("workflow-checks").append(elem("p","将自动拍摄并逐项核验，无需先点击人工确认。","muted small"));
 }else if(measurement){
  $("workflow-check-title").textContent="自动读数与稳定性核验";
  $("workflow-checks").append(elem("p",s.automation==='agent'&&s.mode==='live'
   ?"倒计时后定位显示屏，单帧识别到有效数字即记录；定位失败时会继续尝试约10秒。"
   :"间隔至少1秒采集两次，读数相差不超过0.02 V才自动记录。","muted small"));
 }
 const useManual=s.status==="paused"||g==="equipment"||!automatic||(!visual&&!measurement&&["read_sop","manual_check","meter_setup"].includes(g));
 toggle("confirm-current",["read_sop","manual_check","meter_setup"].includes(g)&&(!visual||!automatic));
 toggle("meter-panel",g==="meter_setup"&&(!visual||!automatic));
 toggle("manual-takeover",(visual||measurement)&&automatic&&s.status==="running");
 if(automatic&&s.status==="running"&&["inspect_step","inspect_equipment","read_display"].includes(d?.tool)){
  $("advance").textContent="检查通过后将自动继续";$("advance").disabled=true;
 }
 $("candidate-value").readOnly=automatic;
 $("confirm-candidate").disabled=automatic||s.status!=="running";
}
function manualTakeover(){cancelAgentRequest();S.autoEnabled=false;S.runError=null;clearTimeout(S.autoTimer);render();toast("已切换人工处理。核对后点击顶部按钮，提交并继续。");}

function renderPlanReadiness(){
 if(!S.plan)return;
 const reasons=[...(S.plan.questions||[])];
 if(S.plan.measurement?.unit==='待确认单位')reasons.push('请填写实际测量单位');
 $('start-plan').disabled=Boolean(S.starting||reasons.length);
 $('start-review-note').textContent=reasons.length?'开始前还需处理：'+reasons.join('；'):'请核对上方计划与现场条件。点击开始即确认采用这份计划；识别、核验和保存将自动进行。';
}
function renderTaskStatus(){
 const s=S.session;if(!s||!$('task-card'))return;
 const pending=[...(s.candidates||[])].reverse().find(c=>c.status==='pending');
 let busy=null,busyMessage=null;
 if(S.actionBusy){busy='submitting';busyMessage=s.interaction?.primary_action?.kind==='start_reading'
  ?'正在定位显示屏并读取单帧数字，请保持接触，完成后会提示移开表笔。':'正在记录本步确认并检查后续条件…';}
 else if(S.agentRunBusy){busy=S.agentStage==='planning'?'planning':S.agentTool==='read_display'?'reading':S.agentTool==='save_measurement'?'saving':'observing';
  if(S.agentStage==='executing'&&['advance','report'].includes(S.agentTool))busy='advancing';
  busyMessage=S.agentStage==='planning'?'Agent 正在根据当前步骤和现场结果选择行动…':S.agentTool==='read_display'?'正在读取显示屏并检查读数稳定性…':S.agentTool==='advance'?'本步已通过，正在进入下一步…':S.agentActionText;}
 else if(S.observing){busy=S.observationTask==='reading'?'reading':'observing';busyMessage=S.observationTask==='reading'?'正在读取显示屏并核验读数…':'正在核对现场画面，请保持画面清晰…';}
 else if(meterLiveOwnsStep()&&S.meterBusy){busy='observing';busyMessage=meterLiveMessage();}
 const automatic=S.autoEnabled&&(s.mode==='live'||S.autoReplay);
 let view=taskView(s,{autoEnabled:S.autoEnabled,manualEdits:S.equipmentDirty,candidate:Boolean(pending),
  hidden:document.hidden,disconnected:S.disconnected,starting:S.starting,error:S.runError,busy,busyMessage,
  replayBlocked:s.mode==='replay'&&!S.autoReplay,meterStep:meterLiveOwnsStep(),meterTried:meterStepEnabled()&&meterWasTried(),meterMessage:meterStepEnabled()?meterLiveMessage():null});
 if(S.actionBusy&&S.readingCountdown>0&&s.status==='running'&&!document.hidden&&!S.disconnected){
  view={...view,phase:'countdown',message:`${S.readingCountdown} 秒后自动读数。现在请红接正极、黑接负极，保持接触。`,
   primary_action:{kind:'cancel_reading',label:'取消本轮'}};
 }
 if(!busy&&S.autoEnabled&&S.lastRetryReason&&Date.now()<S.nextObserveAt){view={...view,phase:'retrying',message:S.lastRetryReason+'；将自动重试。',primary_action:null};}
 const names={ready:'准备中',countdown:'读数倒计时',preparing_reading:'准备读表',planning:'正在决策',observing:'正在识别',reading:'正在读表',saving:'正在保存',
  submitting:'正在提交',advancing:'自动继续',waiting_scene:'等待现场条件',waiting_user:'等待你操作',
  retrying:'稍后重试',paused:'已暂停',background:'后台暂停',error:'需要处理',completed:'已完成',notice:'收尾提示'};
 $('task-card').dataset.phase=view.phase;$('task-phase').textContent=names[view.phase]||'处理中';
 $('run-status').textContent=s.status==='completed'?'实验完成':names[view.phase]||'处理中';
 $('task-position').textContent=`第 ${s.step+1} / ${s.steps.length} 步`;
 $('task-message').textContent=view.message||s.steps[s.step].instruction;
 const button=$('primary-action');button.dataset.action=view.primary_action?.kind||'';
 button.textContent=view.primary_action?.label||'';button.disabled=Boolean((S.actionBusy&&view.primary_action?.kind!=='cancel_reading')||S.starting);
 toggle('primary-action',Boolean(view.primary_action));toggle('pause',s.status==='running');
 $('pause').textContent='暂停';
 const elapsed=busy&&S.agentStartedAt?`已用 ${Math.max(0,Math.floor((Date.now()-S.agentStartedAt)/1000))} 秒 · `:'';
 const updated=new Date(s.updated_at);const retries=S.autoErrors?` · 已重试 ${S.autoErrors} 次`:'';
 $('task-meta').textContent=elapsed+'最近更新 '+(Number.isNaN(updated.getTime())?'—':updated.toLocaleTimeString())+retries;
 $('step-notice').textContent='已进入：'+(S.stepNotice||'');toggle('step-notice',Boolean(S.stepChangedAt&&Date.now()-S.stepChangedAt<5000));
 toggle('meter-panel',gate()==='meter_setup'&&(!automatic||view.primary_action?.kind==='confirm_step'));
 toggle('measurement-panel',gate()==='measurement'&&!pending&&(!automatic||view.primary_action?.kind==='manual_measurement'));
 toggle('manual-takeover',s.status==='running'&&(S.autoEnabled||Boolean(S.runError)));
 document.querySelectorAll('.manual-only').forEach(node=>node.classList.toggle('hidden',automatic));
 document.querySelectorAll('#steps li').forEach((node,index)=>{
  node.setAttribute('aria-current',index===s.step&&s.status!=='completed'?'step':'false');
  if(!node.querySelector('.step-state'))node.append(elem('small',index<s.step||s.status==='completed'?'已完成':index===s.step?'当前':'待执行','step-state'));
 });
 sessionStorage.setItem('labagent_auto_'+s.id,String(S.autoEnabled));
}
async function primaryAction(){
 const kind=$('primary-action').dataset.action;
 if(kind==='cancel_reading'&&S.readingCountdown>0){cancelAgentRequest();return;}
 if(!kind||S.actionBusy||S.starting)return;
 if(kind==='start_reading'){await startReadingBurst();return;}
 if(kind==='inspect_meter'){S.runError=null;await runMeterLive();return;}
 if(kind==='show_results'){document.querySelector('.bottom-panel').scrollIntoView({behavior:'smooth'});return;}
 if(kind==='retry'){
  if(meterStepEnabled()){stopMeterLive();S.autoEnabled=true;S.runError=null;if(S.session.mode==='replay')S.autoReplay=true;await runMeterLive();return;}
  S.runError=null;S.lastRetryReason=null;S.agentWaiting=null;S.autoErrors=0;S.nextObserveAt=0;
  S.autoEnabled=true;if(S.session.mode==='replay')S.autoReplay=true;
  try{if(['equipment','measurement'].includes(gate())||S.session.agent_decision?.visual_rule){
   if(!S.media)await startCamera(true);
   await observe(gate()==='equipment'?'objects':gate()==='measurement'?'reading':'step',false);
  }toast('检查已恢复，将按当前步骤继续。');}catch(error){S.autoEnabled=false;S.runError='检查未完成：'+error.message;throw error;}
  finally{render();}return;
 }
 if(kind==='recheck'){cancelAgentRequest();await action('invalidate_wiring');return;}
 if(kind==='advance'){await action('advance');return;}
 if(kind==='confirm_step'){await humanAction(kind,{meter_range:$('meter-range').value});return;}
 if(kind==='confirm_equipment'){await humanAction(kind,{objects:[...document.querySelectorAll('.equipment-checkbox:checked')].map(x=>x.value)});return;}
 if(kind==='confirm_candidate'){
  const candidate=[...S.session.candidates].reverse().find(c=>c.status==='pending');
  if(!candidate||!$('candidate-value').value.trim()||!Number.isFinite(Number($('candidate-value').value)))throw new Error('请对照显示屏图片填写实际读数');
  await humanAction(kind,{candidate_id:candidate.id,value:Number($('candidate-value').value)});return;
 }
 if(kind==='manual_measurement'){
  if(!$('manual-value').value.trim()||!Number.isFinite(Number($('manual-value').value)))throw new Error('请输入现场实际读数');
  await humanAction(kind,{value:Number($('manual-value').value)});return;
 }
 await humanAction(kind);
}
setInterval(renderTaskStatus,1000);

function bind(id,fn){$(id).addEventListener("click",()=>attempt(fn));}
bind('primary-action',primaryAction);
$('meter-range').addEventListener('change',()=>{S.meterEdited=true;});
$('equipment-editor').addEventListener('toggle',()=>{if($('equipment-editor').open&&gate()==='equipment'){S.equipmentDirty=true;manualTakeover();}});
bind("manual-takeover",manualTakeover);bind("show-setup",()=>toggle("plan-setup",true));bind("new-session",()=>{toggle("plan-setup",true);$("plan-setup").scrollIntoView({behavior:"smooth"});});bind("hide-setup",()=>toggle("plan-setup",false));bind("import-plan",importPlan);bind("save-plan",savePlan);bind("start-plan",startPlan);$("plan-select").onchange=()=>attempt(()=>selectPlan($("plan-select").value));bind("edit-plan",()=>{$("plan-details").open=!$("plan-details").open;});bind("view-source",()=>{$("source-text").textContent=S.plan.source_text||"此模板的原文见随项目提供的实验要求文档。";$("source-dialog").showModal();});bind("close-source",()=>$("source-dialog").close());
bind("rehearse",async()=>{if(!S.plan.approved)throw new Error("请先审核计划；也可使用已审核的电池示例");const r=await post(`/plans/${S.plan.id}/rehearse`,{scenario:"happy"});await selectSession(r.session.id);toggle("plan-setup",false);toast("独立软件演练完成，所有数据标为回放");});
bind("advance",()=>action("advance"));bind("pause",()=>{cancelAgentRequest();return action("pause");});bind("resume",()=>humanAction("resume"));bind("recheck",async()=>{cancelAgentRequest();if(confirm("现场档位、接线或器材发生变化，需要暂停并重新核对。继续？"))await action("invalidate_wiring");});bind("confirm-current",()=>humanAction("confirm_step",{meter_range:$("meter-range").value}));bind("confirm-equipment",()=>humanAction("confirm_equipment",{objects:[...document.querySelectorAll(".equipment-checkbox:checked")].map(x=>x.value)}));bind("manual-measurement",()=>{if(!$("manual-value").value.trim()||!Number.isFinite(Number($("manual-value").value)))throw new Error("请输入实际测量值");return humanAction("manual_measurement",{value:Number($("manual-value").value)});});
bind("confirm-candidate",()=>{const c=[...S.session.candidates].reverse().find(x=>x.status==="pending");if(!c)throw new Error("没有待核对候选");if(!$("candidate-value").value.trim()||!Number.isFinite(Number($("candidate-value").value)))throw new Error("请核对图片后填写实际读数");return humanAction("confirm_candidate",{candidate_id:c.id,value:Number($("candidate-value").value)});});bind("reject-candidate",()=>{const c=[...S.session.candidates].reverse().find(x=>x.status==="pending");if(c)return humanAction("reject_candidate",{candidate_id:c.id});});bind("save-measurement",()=>humanAction("save_measurement"));bind("remeasure",()=>action("remeasure"));
bind("toggle-equipment-live",()=>{S.liveEquipment=!S.liveEquipment;renderEquipmentLive();});bind("start-camera",startCamera);bind("stop-camera",()=>{cancelAgentRequest();S.autoEnabled=false;stopMedia();render();});$("media-file").onchange=e=>attempt(()=>loadImage(e.target.files[0]));bind("detect",()=>observe("objects"));bind("ocr",()=>observe("reading"));bind("agent-observe",()=>observe(gate()==="equipment"?"objects":gate()==="measurement"?"reading":"step"));bind("observe-loop",toggleObservation);$("roi-overlay").onpointerdown=e=>{if(gate()!=="measurement")return;S.drag=point(e);e.target.setPointerCapture(e.pointerId);};$("roi-overlay").onpointermove=e=>{if(!S.drag)return;const p=point(e);S.roi=[Math.min(S.drag.x,p.x),Math.min(S.drag.y,p.y),Math.abs(S.drag.x-p.x),Math.abs(S.drag.y-p.y)];drawROI();};$("roi-overlay").onpointerup=()=>S.drag=null;
$("chat-form").onsubmit=e=>{e.preventDefault();attempt(sendChat);};$("chat-input").onkeydown=e=>{if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){e.preventDefault();attempt(sendChat);}};document.querySelectorAll("[data-question]").forEach(b=>b.onclick=()=>{$("chat-input").value=b.dataset.question;attempt(sendChat);});bind("record-audio",recordAudio);document.querySelectorAll("[data-export]").forEach(b=>b.onclick=()=>attempt(()=>download(b.dataset.export)));bind("settings-button",async()=>{$("settings-dialog").showModal();await services();});bind("close-settings",()=>$("settings-dialog").close());bind("save-token",async()=>{sessionStorage.setItem("labagent_access_token",$("access-token").value.trim());await boot();$("settings-dialog").close();});
window.addEventListener("beforeunload",()=>{stopMedia();if(S.audio)S.audio.getTracks().forEach(t=>t.stop());if(S.candidateUrl)URL.revokeObjectURL(S.candidateUrl);});setInterval(async()=>{if(!S.session||S.polling||document.hidden)return;S.polling=true;const id=S.session.id;try{const s=await api(`/sessions/${id}`);if(S.session?.id===id){S.disconnected=false;$("connection").textContent='O6 本地在线';sync(s);}}catch(e){if(S.session?.id===id){if(e.status===404){clearSelectedSession(id);attempt(refreshList);toast('该实验已被删除');}else{S.disconnected=true;$("connection").textContent='连接中断';}}}finally{S.polling=false;renderTaskStatus();}},3000);attempt(boot);
