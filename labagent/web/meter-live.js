"use strict";

function meterStepEnabled(){return Boolean(S.config?.providers?.meter_live_enabled&&gate()==='meter_setup'&&S.session?.agent_decision?.visual_rule==='meter_setup');}
function meterLiveOwnsStep(){return Boolean(meterStepEnabled()&&S.autoEnabled&&S.session?.status==='running'&&(S.session.mode==='live'||S.autoReplay));}
function meterLiveReady(){return meterLiveOwnsStep()&&!S.starting&&!S.switching&&!S.actionBusy&&!S.disconnected&&!document.hidden;}
function meterAttemptKey(){return S.session?'labagent_meter_attempt_'+S.session.id+'_'+S.session.steps[S.session.step].id:null;}
function meterWasTried(){
 const key=meterAttemptKey();
 return Boolean(key&&(sessionStorage.getItem(key)==='1'||S.session.facts?.['workflow_check:'+S.session.steps[S.session.step].id]));
}
function clearMeterDetails(){
 S.meterDetailViews=null;S.meterDetailKey=null;
 $('meter-detail-views')?.replaceChildren();toggle('meter-detail-panel',false);
}
function clearMeterSnapshot(){
 if(S.meterSnapshotUrl)URL.revokeObjectURL(S.meterSnapshotUrl);
 S.meterSnapshotUrl=null;$('meter-snapshot-image')?.removeAttribute('src');toggle('meter-snapshot-panel',false);
}
function showMeterSnapshot(blob){
 clearMeterSnapshot();S.meterSnapshotUrl=URL.createObjectURL(blob);
 $('meter-snapshot-image').src=S.meterSnapshotUrl;toggle('meter-snapshot-panel',true);
}
function renderMeterDetails(){
 const views=S.meterResult&&S.meterDetailViews;
 if(!views?.length){clearMeterDetails();return;}
 const key=S.meterResultAt;
 if(S.meterDetailKey===key)return;
 const valid=views.filter(v=>['dial','terminals'].includes(v.part)&&typeof v.image_url==='string'&&v.image_url.startsWith('data:image/jpeg;base64,')&&v.image_url.length<250000);
 const container=$('meter-detail-views');container.replaceChildren();
 for(const view of valid){
  const figure=elem('figure'),image=elem('img');
  image.src=view.image_url;image.alt=view.part==='dial'?'本次识别的旋钮与档位放大图':'本次识别的插孔与表笔放大图';
  figure.append(image,elem('figcaption',view.part==='dial'?'旋钮与档位':'插孔与表笔'));container.append(figure);
 }
 S.meterDetailKey=key;toggle('meter-detail-panel',valid.length===2);
}
function discardMeterProof(token,id){if(token&&id)post(`/sessions/${id}/meter-live/discard`,{preview_id:token}).catch(()=>{});}
function stopMeterLive(){
 if(!S.meterActive&&!S.meterTimer&&!S.meterController&&!S.meterMotion)return;
 clearTimeout(S.meterTimer);clearInterval(S.meterMotion);S.meterController?.abort();
 discardMeterProof(S.meterProof,S.meterSessionId);S.meterEpoch=(S.meterEpoch||0)+1;
 S.meterActive=false;S.meterTimer=null;S.meterMotion=null;S.meterController=null;S.meterProof=null;
 S.meterBusy=false;S.meterResult=null;S.meterResultAt=null;S.meterMessage=null;clearMeterDetails();clearMeterSnapshot();
 if(S.session)renderWorkflow();
}
function meterLiveMessage(){
 if(S.meterBusy)return (S.meterMessage||'正在核对最新画面')+` · 本次已用 ${Math.max(0,Math.floor((Date.now()-S.meterStartedAt)/1000))} 秒`;
 return S.meterMessage||(meterWasTried()?'本步已有检查记录。调整现场后，点击“重新检测仪表设置”检查一次。':'准备自动检查一次，请完整展示旋钮和表笔插孔。');
}
function scheduleMeterLive(delay=150){
 if(!meterLiveReady()||meterWasTried()||S.meterBusy||S.meterTimer)return;
 S.meterTimer=setTimeout(()=>attempt(runMeterLive),Math.max(delay,(S.meterNextAt||0)-Date.now()));
}
function renderMeterLive(){
 toggle('live-meter-controls',meterStepEnabled());
 renderMeterDetails();
 if(!meterLiveReady()){stopMeterLive();$('live-meter-status').textContent='检查已暂停；恢复后按页面提示继续。';return;}
 S.meterActive=true;S.meterSessionId=S.session.id;
 $('live-meter-status').textContent=meterLiveMessage();
 scheduleMeterLive();
}
async function runMeterLive(){
 clearTimeout(S.meterTimer);
 S.meterTimer=null;if(!meterLiveReady()||S.meterBusy)return;
 if(S.agentRunBusy){S.meterMessage='上一项操作尚未结束，请稍后点击重新检测。';renderTaskStatus();return;}
 sessionStorage.setItem(meterAttemptKey(),'1');
 const epoch=S.meterEpoch||0,id=S.session.id,control=new AbortController();
 S.meterBusy=true;S.meterController=control;S.meterStartedAt=Date.now();
 S.meterResult=null;clearMeterDetails();clearMeterSnapshot();
 S.meterMessage='正在拍摄本次照片';renderMeterLive();renderTaskStatus();
 const timeout=setTimeout(()=>control.abort(),30000);
 const current=()=>!control.signal.aborted&&epoch===(S.meterEpoch||0)&&meterLiveReady()&&S.session?.id===id;
 try{
  if(!S.media)await startCamera(true);
  if(!current())return;
  const snapshot=await capture(),form=new FormData();
  if(!current())return;
  showMeterSnapshot(snapshot);form.append('file',snapshot,'meter-snapshot.jpg');
  if(!current())return;
  S.meterMessage='正在识别本次拍摄的照片；视频后续变化不影响这张照片';renderTaskStatus();
  const result=await api(`/sessions/${id}/meter-live/preview`,{method:'POST',body:form,signal:control.signal});
  if(!current()){discardMeterProof(result.preview_id,id);return;}
  S.meterResult=result.check;S.meterResultAt=result.captured_at;S.meterDetailViews=result.detail_views;renderMeterDetails();
  const at=new Date(result.captured_at).toLocaleTimeString();
  const retryNotice=!result.check.passed&&!result.check.message.includes('重新检测')?' 调整后请点击“重新检测仪表设置”；不会自动重试。':'';
  S.meterMessage=`照片 ${at} · 本次 ${result.seconds.toFixed(1)} 秒 · ${result.check.message}${retryNotice}`;
  $('live-meter-status').textContent=S.meterMessage;S.meterErrors=0;renderWorkflow();renderTaskStatus();
  if(!result.check.passed||!result.preview_id)return;
  S.meterProof=result.preview_id;
  const committed=await api(`/sessions/${id}/meter-live/accept-snapshot`,{method:'POST',body:JSON.stringify({preview_id:result.preview_id}),signal:control.signal});
  S.meterProof=null;
  if(current())sync(committed.session);
  await Promise.all([refreshAudit(),refreshList()]);
 }catch(error){
  if(epoch!==(S.meterEpoch||0)||S.session?.id!==id)return;
  S.meterResult=null;clearMeterDetails();
  if(error.status===409){S.meterMessage='实验状态已变化或本次照片检查已过期，未采用结果。请核对后重新拍照。';sync(await api(`/sessions/${id}`));
  }else if(error.status===404){clearSelectedSession(id);
  }else if(error.status===429){S.meterMessage='模型当前繁忙，本次未完成检查。请稍后点击重新检测。';
  }else{
   S.meterMessage=(error.name==='AbortError'?'本次检查超时，旧结果已丢弃。':error.message)+' 本次已停止，请处理后点击重新检测。';
  }
 }finally{
  clearTimeout(timeout);
  if(epoch===(S.meterEpoch||0)){
   if(S.meterProof){discardMeterProof(S.meterProof,id);S.meterProof=null;}
   clearInterval(S.meterMotion);S.meterMotion=null;
   S.meterBusy=false;S.meterController=null;
   if(S.session)render();
  }
 }
}

