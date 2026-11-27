"use strict";

// One model decision followed by one authorized execution. Video preview runs independently.
function agentWakeKey(){
 const s=S.session;
 return s?`${s.id}/${s.revision}/${S.agentSceneSignature||''}`:'';
}
function updateAgentScene(result){
 const signature=JSON.stringify(result.present||{});
 if(S.agentSceneCandidate===signature)return;
 S.agentSceneCandidate=signature;clearTimeout(S.agentSceneTimer);
 S.agentSceneTimer=setTimeout(()=>{
  if(S.agentSceneCandidate!==signature)return;
  S.agentSceneSignature=signature;S.agentWaiting=null;scheduleAuto();
 },900);
}
function agentCanRun(){
 if(S.session?.interaction?.primary_action?.kind==='start_reading')return false;
 if(S.session?.interaction?.phase==='notice')return false;
 if(typeof meterLiveOwnsStep==='function'&&meterLiveOwnsStep())return false;
 return S.session?.automation==='agent'&&S.session.status==='running'&&S.autoEnabled
  &&!S.starting&&!S.switching&&!S.actionBusy&&!S.disconnected
  &&!(S.session.interaction?.phase==='waiting_user'&&S.session.interaction?.primary_action?.kind==='confirm_step')
  &&(S.session.mode==='live'||S.autoReplay)&&!document.hidden&&!S.observing&&!S.busy
  &&!(gate()==='equipment'&&S.equipmentDirty);
}
function cancelAgentRequest(){S.agentRequestController?.abort();}
async function applyAgentProposal(proposal){
 if(S.session.revision!==proposal.expected_revision)throw new Error('状态已变化，请重新询问');
 if(proposal.kind==='update_sampling'){
  cancelAgentRequest();
  const result=await post(`/sessions/${selected()}/agent/replan`,{samples:proposal.samples,
   confirmed:true,expected_revision:proposal.expected_revision});
  S.agentWaiting=null;sync(result.session);toast(result.message);await refreshAudit();return;
 }
 return proposal.kind==='save_measurement'?humanAction(proposal.kind):action(proposal.kind);
}
function scheduleAgentRun(){
 clearTimeout(S.autoTimer);
 if(!agentCanRun()||S.agentRunBusy||waitingForEquipment()||S.agentWaiting===agentWakeKey())return;
 S.autoTimer=setTimeout(()=>attempt(runAgent),Math.max(0,S.nextObserveAt-Date.now()));
}
function waitingForEquipment(){
 const s=S.session,p=S.equipmentPreview;
 if(!s?.interaction?.waiting_for_equipment)return false;
 return !(S.liveEquipment && p?.preview && p.inventory_ready && p.session_id===s.id
  && p.revision===s.revision && Date.now()-S.boxTime<5000);
}
function renderAgentPanel(){
 const s=S.session;if(!s)return;
 const agent=s.automation==='agent';
 $('controller-label').textContent=agent?'AGENT · 目标与行动':'WORKFLOW · 自动检查';
 if(agent&&S.agentRunBusy)$('agent-decision').textContent=S.agentStage==='executing'
  ?'正在执行：'+S.agentActionText:'Agent正在读取当前目标、现场证据和工具反馈，选择下一项行动…';
 $('agent-mode').textContent=agent?'实验 Agent':'实验助手';
 if(typeof renderTaskStatus==='function')renderTaskStatus();
}
async function runAgent(){
 if(!agentCanRun()||S.agentRunBusy||waitingForEquipment())return;
 const id=S.session.id,revision=S.session.revision,wakeAtStart=agentWakeKey();
 const control=new AbortController();S.agentRequestController=control;S.agentRunBusy=true;S.agentStage='planning';S.agentStartedAt=Date.now();renderAgentPanel();
 let executing=false;
 try{
  const response=await api(`/sessions/${id}/agent/decide`,{method:'POST',signal:control.signal,
   body:JSON.stringify({expected_revision:revision,request_id:crypto.randomUUID().replaceAll('-','')})});
  if(control.signal.aborted||S.session?.id!==id||S.session.revision!==revision||!agentCanRun())return;
  const d=response.decision;sync(response.session);
  S.agentTool=d.tool;
  S.agentActionText=d.reason;
  if(d.tool==='read_display'&&S.session.mode==='live')return;
  $('agent-decision').textContent=(d.arguments.message||d.reason);
  if(d.status==='completed'&&d.tool==='ask_user'){S.agentWaiting=wakeAtStart;return;}
  const form=new FormData();form.append('decision_id',d.id);
  if(d.requires_frame){
   if(!S.media)await startCamera(true);
   if(control.signal.aborted||!agentCanRun()||S.session.id!==id||S.session.revision!==revision)return;
   const frame=await capture();form.append('file',frame,'agent-frame.jpg');
   if(frame.captureId)form.append('capture_id',frame.captureId);
  }
  if(control.signal.aborted||!agentCanRun()||S.session.id!==id||S.session.revision!==revision)return;
  executing=true;S.agentStage='executing';S.observing=d.requires_frame;S.autoRunning=true;renderAgentPanel();
  $('observation-status').textContent=`Agent执行：${d.reason}`;
  const result=await api(`/sessions/${id}/agent/execute`,{method:'POST',body:form,signal:control.signal});
  if(control.signal.aborted||S.session?.id!==id)return;
  if(result.present)acceptEquipmentPreview(result);
  if(result.roi){S.roi=result.roi;drawROI();}
  sync(result.session);
  $('observation-status').textContent=result.message||'工具返回结果，Agent将继续核验目标。';
  S.agentWaiting=d.tool==='ask_user'?wakeAtStart:null;
  S.autoErrors=0;S.lastRetryReason=null;S.nextObserveAt=Date.now()+(d.requires_frame?1200:400);
  await Promise.all([refreshAudit(),refreshList()]);
 }catch(error){
  if(error.name==='AbortError')return;
  if(error.status===409){
   if(S.session?.id===id)sync(await api(`/sessions/${id}`));
   S.nextObserveAt=Date.now()+1000;
  }else if(error.status===429){S.nextObserveAt=Date.now()+2500;
  }else{
   S.autoErrors++;S.nextObserveAt=Date.now()+8000;
   S.lastRetryReason=error.message;
   $('observation-status').textContent='本次Agent行动未完成：'+error.message;
   if(['NotAllowedError','NotFoundError','NotReadableError'].includes(error.name)||S.autoErrors>=3){
    S.autoEnabled=false;S.runError='自动检查已暂停：'+error.message;toast('自动检查暂停，请按顶部提示处理。',true);
   }
  }
 }finally{
  if(S.agentRequestController===control)S.agentRequestController=null;
  if(executing){S.observing=false;S.autoRunning=false;}
  S.agentRunBusy=false;S.agentStage=null;S.agentStartedAt=null;
  if(S.session?.id===id)render();else scheduleAuto();
 }
}

function waitForReading(ms,signal){
 return new Promise((resolve,reject)=>{
  if(signal.aborted){reject(new DOMException('采集已取消','AbortError'));return;}
  const abort=()=>{clearTimeout(timer);reject(new DOMException('采集已取消','AbortError'));};
  const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},ms);
  signal.addEventListener('abort',abort,{once:true});
 });
}
async function startReadingBurst(){
 const s=S.session,d=s.agent_decision;
 if(S.actionBusy||!d?.decision_id)return;
 const id=s.id,revision=s.revision,control=new AbortController();
 S.agentRequestController=control;S.actionBusy=true;
 S.readingCountdown=5;
 renderTaskStatus();
 let deadline;
 const check=()=>{
  if(control.signal.aborted||S.session?.id!==id||S.session.revision!==revision
    ||S.session.status!=='running'||document.hidden)
   throw new DOMException('采集已取消','AbortError');
 };
 try{
  if(!S.media)await startCamera(true);
  for(let seconds=5;seconds>0;seconds--){
   check();S.readingCountdown=seconds;renderTaskStatus();
   await waitForReading(1000,control.signal);
  }
  check();S.readingCountdown=0;renderTaskStatus();
  deadline=setTimeout(()=>control.abort(),30000);
  const locateUntil=Date.now()+10000;
  let attempts=0,missing=0;
  while(true){
   check();
   const frame=await capture(),form=new FormData();
   form.append('decision_id',d.decision_id);
   form.append('file',frame,'reading.jpg');
   form.append('capture_id',frame.captureId||crypto.randomUUID().replaceAll('-',''));
   $('observation-status').textContent='正在定位显示屏并读取单帧数字，请保持接触…';
   const result=await api(`/sessions/${id}/agent/read-frame`,{method:'POST',body:form,signal:control.signal});
   if(S.session?.id!==id)return;
   if(result.located!==false){
    sync(result.session);$('observation-status').textContent=result.message;toast(result.message);break;
   }
   attempts++;missing=result.meter_present===false?missing+1:0;
   if(Date.now()>=locateUntil&&attempts>=2){
    const message=missing>=2?'持续未检测到万用表，请将仪表完整放入镜头。'
     :result.location_error?'定位服务暂时未返回结果，请稍后再试。':'暂未定位到显示屏，请摆正仪表并靠近镜头。';
    $('observation-status').textContent=message+' 本轮已结束，可以移开表笔。';
    toast($('observation-status').textContent,true);break;
   }
   $('observation-status').textContent='正在继续定位，请保持万用表入镜，稍候…';
   await waitForReading(800,control.signal);
  }
 }catch(error){
  toast('本轮采集已结束，可以移开表笔。'+(error.name==='AbortError'?'采集已取消或超时，请重新开始。':error.message),true);
 }finally{
  clearTimeout(deadline);S.readingCountdown=null;S.actionBusy=false;
  if(S.agentRequestController===control)S.agentRequestController=null;
  if(S.session?.id===id){sync(await api(`/sessions/${id}`));render();}
 }
}
