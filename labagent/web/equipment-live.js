"use strict";

// Classic deferred script, loaded before demo.js. Functions use its shared state.
function probeParts(key,name){
 const text=(key+" "+name).toLowerCase();
 if(!/表笔|probes?|test[ _-]?leads?/.test(text))return [];
 const red=/红|red/.test(text),black=/黑|black/.test(text);
 return red===black?[['red_probe','红表笔'],['black_probe','黑表笔']]:[];
}
function renderEquipmentChecklist(s){
 const key=s.id+"/"+(s.plan?.id||"legacy"),labels=s.plan?.object_labels||{};
 if(S.equipmentKey!==key){
  S.equipmentKey=key;$("equipment-checks").replaceChildren();
  for(const[id,name]of Object.entries(labels)){
   const parts=probeParts(id,name),checked=(s.facts.objects?.value||[]).includes(id);
   const aggregate=elem("input");aggregate.type="checkbox";aggregate.value=id;
   aggregate.className="equipment-checkbox";aggregate.checked=checked;
   if(parts.length){
    const hidden=elem("span",undefined,"hidden");hidden.append(aggregate);$("equipment-checks").append(hidden);
    aggregate.dataset.parts="true";
    for(const[modelClass,title]of parts){
     const label=elem("label",undefined,"check"),box=elem("input"),note=elem("small","尚未观察","obs-tag");
     box.type="checkbox";box.className="equipment-part";box.dataset.parent=id;box.dataset.modelClass=modelClass;box.checked=checked;
     note.dataset.object=id;note.dataset.modelClass=modelClass;
     label.append(box,document.createTextNode(title),note);$("equipment-checks").append(label);
    }
   }else{
    const label=elem("label",undefined,"check"),note=elem("small","尚未观察","obs-tag");note.dataset.object=id;
    label.append(aggregate,document.createTextNode(name),note);$("equipment-checks").append(label);
   }
  }
 }
 const observed=S.equipmentPreview||{present:s.facts.observed_objects?.value||{},boxes:s.facts.object_boxes?.value||[]};
 updateEquipmentSuggestions(observed);
}
function updateEquipmentSuggestions(result){
 const present=result.present||{},boxes=result.boxes||[];
 const statusList=$('equipment-status-list');
 if(statusList){statusList.replaceChildren();
  for(const[key,name]of Object.entries(S.session?.plan?.object_labels||{})){
   const parts=probeParts(key,name),items=parts.length?parts:[[null,name]];
   for(const[modelClass,title]of items){
    const value=modelClass&&boxes.length?boxes.some(box=>box.label===key&&box.model_class===modelClass):present[key];
    const row=elem('div',undefined,'equipment-status-item');row.dataset.visible=String(value===true);
    row.append(elem('span',title),elem('strong',value===true?'✓ 可见':value===false?'未见':'待看清'));statusList.append(row);
   }
  }
 }
 document.querySelectorAll('.equipment-checkbox').forEach(box=>{
  if(!S.equipmentEdited.has(box.value)&&box.dataset.parts!=='true')box.checked=present[box.value]===true;
 });
 document.querySelectorAll('.equipment-part').forEach(box=>{
  if(!S.equipmentEdited.has(box.dataset.parent))box.checked=boxes.some(d=>d.label===box.dataset.parent&&d.model_class===box.dataset.modelClass);
 });
 syncProbeAggregates();
 document.querySelectorAll('[data-object]').forEach(note=>{
  const key=note.dataset.object,part=note.dataset.modelClass;
  const value=part?(boxes.some(d=>d.label===key&&d.model_class===part)?true:present[key]===undefined?null:false):present[key];
  note.textContent=value===true?'观察建议：可见':value===false?'观察建议：未见':'观察建议：待核对';
 });
}
function syncProbeAggregates(){
 document.querySelectorAll('.equipment-checkbox[data-parts]').forEach(aggregate=>{
  const parts=[...document.querySelectorAll('.equipment-part')].filter(b=>b.dataset.parent===aggregate.value);
  aggregate.checked=parts.length===2&&parts.every(b=>b.checked);
 });
}
function acceptEquipmentPreview(result,captured=Date.now()){
 S.equipmentPreview=result;S.boxes=result.boxes||[];S.boxTime=captured;
 if(typeof updateAgentScene==='function')updateAgentScene(result);
 updateEquipmentSuggestions(result);drawObjects();
}
function equipmentBoxName(item){
 return {red_probe:'红表笔',black_probe:'黑表笔',multimeter:'万用表',battery:'电池'}[item.model_class]
  ||S.session?.plan?.object_labels[item.label]||item.label;
}
function liveEquipmentReady(){
 return Boolean(S.liveEquipment&&S.stream&&S.media===$("camera")&&$("camera").readyState>=2
  &&S.session?.mode==='live'&&S.session?.status==='running'&&gate()==='equipment'&&!document.hidden);
}
function stopEquipmentPreview(){
 clearTimeout(S.liveTimer);S.liveTimer=null;S.liveController?.abort();
}
function scheduleEquipmentPreview(delay=0){
 clearTimeout(S.liveTimer);S.liveTimer=null;
 if(!liveEquipmentReady()){S.liveController?.abort();return;}
 if(S.liveRunning)return;
 S.liveTimer=setTimeout(runEquipmentPreview,delay);
}
async function runEquipmentPreview(){
 S.liveTimer=null;
 if(!liveEquipmentReady()||S.liveRunning)return;
 if(S.observing||S.autoRunning){scheduleEquipmentPreview(350);return;}
 const id=S.session.id,stepId=S.session.steps[S.session.step].id,stream=S.stream;
 const controller=new AbortController();S.liveRunning=true;S.liveController=controller;
 let delay=250;const start=performance.now(),timeout=setTimeout(()=>controller.abort(),8000);
 try{
  const blob=await capture(),form=new FormData();form.append('file',blob,'preview.jpg');
  if(controller.signal.aborted||!liveEquipmentReady()||S.session.id!==id||S.stream!==stream)return;
  const result=await api(`/sessions/${id}/equipment-preview`,{method:'POST',body:form,signal:controller.signal});
  if(controller.signal.aborted||!liveEquipmentReady()||S.session.id!==id||S.stream!==stream
     ||S.session.steps[S.session.step].id!==stepId||result.step_id!==stepId)return;
  acceptEquipmentPreview(result);
  const ms=Math.round(performance.now()-start);
  $('live-equipment-status').textContent=`连续检测中 · 本次 ${ms} ms · 核对通过后自动继续`;
 }catch(error){
  delay=1500;
  if(error.name!=='AbortError')$('live-equipment-status').textContent='连续检测暂不可用，正在重试：'+error.message;
 }finally{
  clearTimeout(timeout);S.liveRunning=false;
  if(S.liveController===controller)S.liveController=null;
  scheduleEquipmentPreview(delay);
 }
}
function renderEquipmentLive(){
 const equipment=gate()==='equipment';toggle('live-equipment-controls',equipment);
 $('toggle-equipment-live').textContent=S.liveEquipment?'暂停连续检测':'恢复连续检测';
 if(!liveEquipmentReady())$('live-equipment-status').textContent=!S.liveEquipment?'连续检测已暂停':!S.stream?'打开摄像头后自动连续检测':'连续检测等待器材检查步骤或页面恢复';
 scheduleEquipmentPreview();
}
