"use strict";

// Pure view selection: persisted workflow state wins over transient browser work.
function taskView(session, local={}){
 const current=session.steps[session.step],g=current.gate;
 const view={...(session.interaction||{phase:'ready',message:current.instruction,primary_action:null})};
 if(session.status!=='running')return view;
 if(local.disconnected)return {...view,phase:'error',message:'与实验服务的连接中断，正在重新同步状态。',primary_action:null};
 if(local.error)return {...view,phase:'error',message:local.error,primary_action:{kind:'retry',label:'重新检查并继续'}};
 if(local.starting)return {...view,phase:'planning',message:'正在启动实验并记录本次计划。',primary_action:null};
 if(local.busy)return {...view,phase:local.busy,message:local.busyMessage||view.message,primary_action:null};
 if(local.hidden)return {...view,phase:'background',message:'页面在后台，后续自动调度暂停；返回页面后继续。',primary_action:null};
 if(view.phase==='notice')return {...view,primary_action:null};
 if(local.manualEdits)return {...view,phase:'waiting_user',message:'请按实物修正下方器材清单，提交后自动继续。',primary_action:{kind:'confirm_equipment',label:'提交器材清单并继续'}};
 if(!local.autoEnabled){
  if(view.phase==='waiting_user')return view;
  if(view.phase==='advancing')return {...view,phase:'waiting_user'};
  const kinds={equipment:['confirm_equipment','提交器材清单并继续'],meter_setup:['confirm_step','设置已核对，继续'],
   manual_check:['confirm_step','本步已完成，继续'],read_sop:['confirm_step','已阅读要求，继续'],
   save:['save_measurement','保存并继续'],report:['advance','完成实验']};
  let action=kinds[g];
  if(g==='measurement')action=local.candidate?['confirm_candidate','提交读数并继续']:['manual_measurement','提交读数并继续'];
  return {...view,phase:'waiting_user',message:'自动指导已暂停。'+current.instruction,
   primary_action:action?{kind:action[0],label:action[1]}:{kind:'retry',label:'恢复自动检查'}};
 }
 // Workflow replay needs an explicit choice before it calls camera fixtures/tools.
 if(local.replayBlocked)return {...view,phase:'paused',message:'当前是软件演练，尚未开启自动运行。',primary_action:{kind:'retry',label:'开始自动演练'}};
 if(local.meterStep&&view.phase!=='advancing')return {...view,phase:local.meterTried?'waiting_user':'observing',message:local.meterMessage||view.message,
  primary_action:local.meterTried?{kind:'inspect_meter',label:'重新检测仪表设置'}:null};
 if(['advancing','saving'].includes(view.phase))view.primary_action=null;
 return view;
}
if(typeof module!=='undefined')module.exports={taskView};
