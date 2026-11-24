"use strict";

function stopSessionWork(){
 S.chatRequestController?.abort();S.chatRequestController=null;S.busy=false;
 $('send-message').disabled=false;$('chat-tools').textContent='';
 if(typeof stopMeterLive==='function')stopMeterLive();
 S.autoEnabled=false;clearTimeout(S.autoTimer);clearTimeout(S.agentSceneTimer);
 cancelAgentRequest();S.observationController?.abort();stopEquipmentPreview();stopObservation();stopMedia();
 S.agentSceneSignature=null;S.agentSceneCandidate=null;S.agentWaiting=null;
}
function clearSelectedSession(id){
 if(S.session?.id!==id)return;
 stopSessionWork();S.selectionEpoch=(S.selectionEpoch||0)+1;S.switching=false;S.session=null;
 sessionStorage.removeItem('labagent_demo_session');sessionStorage.removeItem('labagent_auto_'+id);
 if(S.candidateUrl)URL.revokeObjectURL(S.candidateUrl);S.candidateUrl=null;
 toggle('active',false);toggle('empty',true);
}
function sessionListRow(s){
 const row=elem('div',undefined,'session-row');row.dataset.sessionId=s.id;
 const pick=elem('button',undefined,'session-item'+(S.session?.id===s.id?' active':''));
 const state={running:'进行中',paused:'已暂停',completed:'已完成'}[s.status]||s.status;
 pick.append(elem('span',s.title,'session-name'),elem('small',`${s.mode==='replay'?'软件演练':'真实实验'} · ${state}${s.status==='completed'?'':` · 第 ${s.step+1} 步`}`));
 pick.append(elem('small',new Date(s.created_at).toLocaleString('zh-CN',{hour12:false})));
 pick.disabled=Boolean(S.starting||S.switching);pick.onclick=()=>attempt(()=>selectSession(s.id));
 const remove=elem('button','删除','session-delete danger-text');remove.dataset.deleteSession=s.id;
 remove.setAttribute('aria-label','删除实验：'+s.title);remove.disabled=Boolean(S.deletingSession||S.starting||S.switching);
 remove.onclick=()=>attempt(()=>deleteSession(s.id));row.append(pick,remove);return row;
}
async function deleteSession(id){
 if(S.deletingSession||S.starting||S.switching)return;
 const info=await api(`/sessions/${id}`);
 if(!window.confirm(`确认删除“${info.title}”？\n该实验的测量、对话和过程记录将被删除，无法从历史中恢复。`))return;
 S.deletingSession=id;
 if(S.session?.id===id){S.switching=true;stopSessionWork();}
 try{
  const latest=await api(`/sessions/${id}`);
  await api(`/sessions/${id}`,{method:'DELETE',body:JSON.stringify({confirmed:true,expected_revision:latest.revision})});
  clearSelectedSession(id);sessionStorage.removeItem('labagent_auto_'+id);await refreshList();toast('实验已删除');
 }finally{
  S.deletingSession=null;S.switching=false;if(S.session)render();await refreshList();
 }
}
