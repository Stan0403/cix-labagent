const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map();
function element(tag,text,cls){return {tag,textContent:text||'',className:cls||'',dataset:{},children:[],checked:false,
 append(...items){this.children.push(...items);},replaceChildren(){this.children=[];}};}
for(const id of ['equipment-checks','camera','live-equipment-status','toggle-equipment-live'])nodes.set(id,element('div'));
function all(){const result=[];function walk(n){if(typeof n!=='object')return;result.push(n);for(const c of n.children||[])walk(c);}for(const n of nodes.values())walk(n);return result;}
const document={hidden:false,createTextNode:t=>t,querySelectorAll(selector){
 if(selector==='[data-object]')return all().filter(n=>n.dataset.object);
 const cls=selector.split('[')[0].slice(1);
 return all().filter(n=>n.className.split(' ').includes(cls)&&(!selector.includes('[data-parts]')||n.dataset.parts));
}};
let timerId=0;const timers=new Map();let drawing=0,requests=0,resolveRequest;
const state={id:'session',step:0,status:'running',mode:'live',steps:[{id:'equipment',gate:'equipment'}],
 plan:{id:'plan',object_labels:{battery:'干电池',multimeter:'万用表',probes:'红黑表笔'}},facts:{},revision:8};
const S={session:state,equipmentKey:null,equipmentEdited:new Set(),equipmentPreview:null,liveEquipment:true,
 liveRunning:false,liveController:null,liveTimer:null,stream:{},observing:false,autoRunning:false};
nodes.get('camera').readyState=2;S.media=nodes.get('camera');
const context={S,document,elem:element,$:id=>nodes.get(id),toggle(){},drawObjects(){drawing++;},
 gate:()=>state.steps[state.step].gate,capture:async()=>new Blob(['jpeg']),
 api:async(path,options)=>{requests++;assert.match(path,/equipment-preview$/);return new Promise(resolve=>{resolveRequest=resolve;});},
 Blob,FormData,AbortController,performance,console,
 setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},clearTimeout(id){timers.delete(id);}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
const result=(classes=['red_probe','black_probe'])=>({preview:true,step_id:'equipment',present:{battery:false,multimeter:false,probes:classes.length===2?true:null},boxes:classes.map((model_class,i)=>({label:'probes',model_class,bbox:[.1+i*.3,.2,.1,.3]}))});
const parts=()=>document.querySelectorAll('.equipment-part');
const parent=()=>document.querySelectorAll('.equipment-checkbox').find(n=>n.value==='probes');
async function run(){
 context.renderEquipmentChecklist(state);
 assert.equal(parts().length,2);
 assert.equal(nodes.get('equipment-checks').children.filter(n=>n.tag==='label').length,4);
 context.acceptEquipmentPreview(result(['red_probe']));
 assert.equal(parts()[0].checked,true);assert.equal(parts()[1].checked,false);assert.equal(parent().checked,false);
 assert.equal(context.equipmentBoxName(result().boxes[0]),'红表笔');
 assert.equal(context.equipmentBoxName(result().boxes[1]),'黑表笔');
 context.acceptEquipmentPreview(result());assert.equal(parent().checked,true);
 parts()[1].checked=false;S.equipmentEdited.add('probes');context.syncProbeAggregates();
 context.acceptEquipmentPreview(result());assert.equal(parent().checked,false);assert.equal(parts()[1].checked,false);
 S.equipmentEdited.clear();context.acceptEquipmentPreview(result([]));assert.equal(S.boxes.length,0);assert.equal(parent().checked,false);
 const pending=context.runEquipmentPreview();await new Promise(setImmediate);
 assert.equal(requests,1);await context.runEquipmentPreview();assert.equal(requests,1,'must keep one request in flight');
 resolveRequest(result());await pending;assert.equal(S.boxes.length,2);
 assert.equal(state.revision,8,'preview cannot change workflow revision');
 const oldDraw=drawing,second=context.runEquipmentPreview();await new Promise(setImmediate);
 S.liveEquipment=false;context.stopEquipmentPreview();resolveRequest(result([]));await second;
 assert.equal(drawing,oldDraw,'ignore response after pause');assert.equal(S.liveRunning,false);
 S.liveEquipment=true;document.hidden=true;context.scheduleEquipmentPreview();assert.equal(S.liveTimer,null);
 document.hidden=false;state.steps[0].gate='measurement';context.scheduleEquipmentPreview();assert.equal(S.liveTimer,null);
 state.steps[0].gate='equipment';context.scheduleEquipmentPreview();assert.notEqual(S.liveTimer,null);
 console.log('PASS: split probes, pair confirmation, manual edits, disappearing boxes, single-flight, pause/stale result, visibility and step lifecycle');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
