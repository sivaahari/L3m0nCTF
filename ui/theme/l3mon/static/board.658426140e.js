(()=>{'use strict';
const clamp=(v,lo,hi)=>Math.min(hi,Math.max(lo,v));
function makeStore(storage,prefix=''){
const mem=new Map();
return{
get(key,fallback){
if(mem.has(key))return mem.get(key);
try{
const v=storage?storage.getItem(prefix+key):null;
return v===null||v===undefined?fallback:v;
}catch{
return fallback;
}
},
set(key,value){
const v=String(value);
mem.set(key,v);
try{if(storage)storage.setItem(prefix+key,v);}catch{}
},
};
}
function localStore(prefix){
let st=null;
try{st=window.localStorage;}catch{}
return makeStore(st,prefix);
}
const store=localStore('l3m0n.v1.');
const motionQuery=typeof matchMedia==='function'?matchMedia('(prefers-reduced-motion: reduce)'):null;
const app={sound:false,vol:6,ch:1,state:'on'};
const bus=new Map();
function on(evt,fn){
if(!bus.has(evt))bus.set(evt,[]);
bus.get(evt).push(fn);
}
function emit(evt,data){
for(const fn of bus.get(evt)||[])fn(data);
}
const $=(sel,root=document)=>root.querySelector(sel);
const $$=(sel,root=document)=>[...root.querySelectorAll(sel)];
const reduced=()=>!!(motionQuery&&motionQuery.matches);
function typing(target){
const el=target&&target.nodeType===1?target:document.activeElement;
return!!(el&&(el.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)));
}
let announceTimer=0;
function announce(text){
const el=$('[data-announce]');
if(!el)return;
el.textContent='';
clearTimeout(announceTimer);
announceTimer=setTimeout(()=>{el.textContent=text;},60);
}
let toastTimer=0;
function toast(text,ms=3600){
const el=$('[data-toast]');
if(el){
el.textContent=text;
el.classList.add('show');
clearTimeout(toastTimer);
toastTimer=setTimeout(()=>el.classList.remove('show'),ms);
}
announce(text);
}
function playOnce(el,keyframes,options){
if(reduced()||!el.animate)return{finished:Promise.resolve(),cancel(){}};
return el.animate(keyframes,options);
}
function openDialog(title,fill,opener){
const dlg=$('[data-dlg]');
if(!dlg||typeof dlg.showModal!=='function')return;
$('[data-dlg-title]').textContent=title;
const body=$('[data-dlg-body]');
body.replaceChildren();
fill(body);
dlg.opener=opener||document.activeElement;
if(!dlg.open)dlg.showModal();
}
function mk(tag,cls,text){
const e=document.createElement(tag);
if(cls)e.className=cls;
if(text!==undefined)e.textContent=text;
return e;
}
const SVG='http://www.w3.org/2000/svg';
function icon(id,cls='ico'){
const s=document.createElementNS(SVG,'svg');
s.setAttribute('class',cls);
s.setAttribute('aria-hidden','true');
const u=document.createElementNS(SVG,'use');
u.setAttribute('href',`#${id}`);
s.append(u);
return s;
}
const ALERT_ICON={error:'i-warn',warn:'i-warn',info:'i-info',ok:'i-check'};
function makeAlert({kind='info',lead='',text=''}){
const box=mk('div',`alert alert-${kind}`);
const p=mk('p');
if(lead)p.append(mk('b','alert-lead',lead),' ');
p.append(text);
box.append(icon(ALERT_ICON[kind]||'i-info'),p);
return box;
}
function showAlert(host,msg,{focus=msg.kind==='error'||msg.kind==='warn'}={}){
for(const old of $$(':scope > .alert, :scope > .summary',host))old.remove();
const box=makeAlert(msg);
box.setAttribute('data-alert','');
host.prepend(box);
if(focus){
box.tabIndex=-1;
box.focus({preventScroll:false});
if(!reduced()){box.classList.remove('shake');void box.offsetWidth;box.classList.add('shake');}
}
return box;
}
function fieldError(input,text){
const field=input.closest('[data-field]')||input.parentElement;
const id=`${input.id}-e`;
let e=document.getElementById(id);
if(!e){
e=mk('p','field-e');
e.id=id;
const anchor=input.closest('.input-row')||input;
anchor.before(e);
}
e.replaceChildren(icon('i-warn'),text);
field.classList.add('has-error');
input.setAttribute('aria-invalid','true');
const ids=(input.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean);
if(!ids.includes(id))input.setAttribute('aria-describedby',[...ids,id].join(' '));
}
function clearFieldError(input){
const id=`${input.id}-e`;
const e=document.getElementById(id);
if(e)e.remove();
const field=input.closest('[data-field]')||input.parentElement;
field.classList.remove('has-error');
input.removeAttribute('aria-invalid');
const ids=(input.getAttribute('aria-describedby')||'').split(/\s+/).filter((x)=>x&&x!==id);
if(ids.length)input.setAttribute('aria-describedby',ids.join(' '));else input.removeAttribute('aria-describedby');
}
function showSummary(host,items,heading='Check these first'){
for(const old of $$(':scope > .alert, :scope > .summary',host))old.remove();
const box=mk('div','summary');
box.tabIndex=-1;
box.setAttribute('data-summary','');
const list=mk('ul');
for(const it of items){
const a=mk('a',null,it.text);
a.href=`#${it.id}`;
a.addEventListener('click',(ev)=>{ev.preventDefault();const f=document.getElementById(it.id);if(f)f.focus();});
const li=mk('li');
li.append(a);
list.append(li);
}
box.append(mk('h2','summary-h',heading),list);
host.prepend(box);
box.focus();
return box;
}
const pendingLabels=new WeakMap();
function setPending(btn,label){
if(!pendingLabels.has(btn))pendingLabels.set(btn,[...btn.childNodes]);
btn.setAttribute('aria-disabled','true');
btn.setAttribute('aria-busy','true');
btn.replaceChildren(label);
}
function clearPending(btn){
btn.removeAttribute('aria-disabled');
btn.removeAttribute('aria-busy');
if(pendingLabels.has(btn)){btn.replaceChildren(...pendingLabels.get(btn));pendingLabels.delete(btn);}
}
function countdown(seconds,onTick,onDone){
const end=Date.now()+seconds*1000;
let timer=0;
let last=-1;
const step=()=>{
const left=Math.max(0,Math.ceil((end-Date.now())/1000));
if(left!==last){last=left;if(left>0)onTick(left);}
if(left===0){onDone();return;}
timer=setTimeout(step,Math.min(1000,end-Date.now()-(left-1)*1000)+5);
};
step();
return()=>clearTimeout(timer);
}
async function copyText(text,selectEl){
try{
if(window.isSecureContext&&navigator.clipboard){await navigator.clipboard.writeText(text);return'copied';}
}catch{}
if(selectEl){
const r=document.createRange();
r.selectNodeContents(selectEl);
const sel=getSelection();
sel.removeAllRanges();
sel.addRange(r);
}
return'select';
}
function sessionEnded(){
const here=location.pathname;
openDialog('Session ended',(body)=>{
const p=mk('p');
p.append(mk('b','alert-lead','SESSION ENDED.'),' Sign in again to carry on. What you typed is kept.');
const a=mk('a','btn btn-main','Sign in again');
a.href=`/login?next=${encodeURIComponent(here)}`;
const actions=mk('p','dlg-actions');
actions.append(a);
body.append(p,actions);
});
}
function initDialog(){
const dlg=$('[data-dlg]');
if(!dlg)return;
$('[data-dlg-close]',dlg).addEventListener('click',()=>dlg.close());
dlg.addEventListener('click',(e)=>{if(e.target===dlg)dlg.close();});
dlg.addEventListener('close',()=>{
const o=dlg.opener;
if(o&&o.focus&&document.contains(o))o.focus({preventScroll:true});
});
}
let ctx=null;
let master=null;
let noiseBuf=null;
let voices=0;
let soundOn=false;
let masterGain=0.43;
const lastAt=new Map();
const MIN_GAP={hover:90,click:40,tick:35,static:150,boing:150};
const LOW=new Set(['hover','tick','click']);
const MAX_VOICES=8;
function makeContext(){
if(ctx)return ctx;
const AC=typeof window!=='undefined'&&(window.AudioContext||window.webkitAudioContext);
if(!AC)return null;
ctx=new AC();
master=ctx.createGain();
master.gain.value=masterGain;
const comp=ctx.createDynamicsCompressor();
comp.threshold.value=-18;comp.knee.value=12;comp.ratio.value=4;comp.attack.value=0.003;comp.release.value=0.15;
master.connect(comp);
comp.connect(ctx.destination);
return ctx;
}
function unlockAudio(){
const c=makeContext();
if(!c)return false;
if(c.state!=='running')c.resume().catch(()=>{});
return true;
}
function setSoundOn(on){
soundOn=!!on;
if(soundOn)unlockAudio();
}
function setVolume(knob){
masterGain=Math.pow(Math.max(0,Math.min(10,knob))/10,2)*1.2;
if(master&&ctx)master.gain.setTargetAtTime(masterGain,ctx.currentTime,0.03);
}
function audioVisibility(hidden){
if(!ctx)return;
if(hidden&&ctx.state==='running')ctx.suspend().catch(()=>{});
else if(!hidden&&soundOn&&ctx.state==='suspended')ctx.resume().catch(()=>{});
}
function noiseSource(){
if(!noiseBuf){
noiseBuf=ctx.createBuffer(1,ctx.sampleRate,ctx.sampleRate);
const d=noiseBuf.getChannelData(0);
for(let i=0;i<d.length;i++)d[i]=Math.random()*2-1;
}
const s=ctx.createBufferSource();
s.buffer=noiseBuf;
return s;
}
function finish(node,startAt,stopAt){
voices++;
node.onended=()=>{voices--;};
node.start(startAt);
node.stop(stopAt);
}
function envelope(g,t,peak,attack,decay){
const a=Math.max(0.002,attack);
g.gain.setValueAtTime(0,t);
g.gain.linearRampToValueAtTime(peak,t+a);
g.gain.exponentialRampToValueAtTime(0.0001,t+a+decay);
return t+a+decay+0.03;
}
function chain(node,t,peak,attack,decay,filter){
const g=ctx.createGain();
g.gain.value=0;
let head=node;
if(filter){
const f=ctx.createBiquadFilter();
f.type=filter.type;f.frequency.setValueAtTime(filter.f,t);if(filter.q)f.Q.value=filter.q;
if(filter.to)f.frequency.exponentialRampToValueAtTime(filter.to,t+(filter.over||0.1));
node.connect(f);head=f;
}
head.connect(g);
g.connect(master);
return envelope(g,t,peak,attack,decay);
}
function tone(t,o){
const osc=ctx.createOscillator();
osc.type=o.type||'sine';
osc.frequency.setValueAtTime(o.f0,t);
if(o.f1)osc.frequency.exponentialRampToValueAtTime(o.f1,t+(o.over||o.d||0.1));
if(o.then)osc.frequency.exponentialRampToValueAtTime(o.then,t+(o.over||0)+(o.over2||0.08));
let lfo=null;
if(o.vib){
lfo=ctx.createOscillator();
lfo.frequency.value=o.vib.rate;
const depth=ctx.createGain();
depth.gain.setValueAtTime(o.vib.depth,t);
if(o.vib.to!==undefined)depth.gain.linearRampToValueAtTime(o.vib.to,t+(o.vib.span||o.d));
lfo.connect(depth);depth.connect(osc.frequency);
}
const stopAt=chain(osc,t,o.peak,o.a||0.003,o.d,o.filter);
finish(osc,t,stopAt);
if(lfo){lfo.start(t);lfo.stop(stopAt);}
}
function hiss(t,o){
const s=noiseSource();
const stopAt=chain(s,t,o.peak,o.a||0.002,o.d,o.filter);
finish(s,t,Math.min(stopAt,t+0.95));
}
const SOUNDS={
power(t){
hiss(t,{filter:{type:'lowpass',f:1200},peak:0.25,a:0.002,d:0.04});
tone(t,{f0:120,f1:45,over:0.14,peak:0.5,a:0.002,d:0.18});
tone(t,{type:'sawtooth',f0:100,peak:0.12,a:0.02,d:0.65,filter:{type:'lowpass',f:320}});
tone(t+0.08,{type:'triangle',f0:220,f1:880,over:0.6,peak:0.05,a:0.06,d:0.3,filter:{type:'lowpass',f:2200}});
},
off(t){
tone(t,{f0:900,f1:60,over:0.26,peak:0.14,a:0.002,d:0.26});
hiss(t,{filter:{type:'highpass',f:3000},peak:0.06,d:0.06});
tone(t+0.24,{type:'square',f0:2000,peak:0.04,a:0.001,d:0.008});
},
click(t){
tone(t,{type:'triangle',f0:1400,f1:700,over:0.025,peak:0.14,a:0.001,d:0.03});
hiss(t,{filter:{type:'bandpass',f:2500,q:1.2},peak:0.06,a:0.001,d:0.015});
},
hover(t){tone(t,{f0:2400,peak:0.02,a:0.002,d:0.012});},
static(t){
tone(t,{type:'triangle',f0:1400,f1:700,over:0.02,peak:0.1,a:0.001,d:0.02});
hiss(t,{filter:{type:'bandpass',f:1800,q:0.7},peak:0.14,a:0.005,d:0.18});
},
tick(t){
const k=1+(Math.random()-0.5)*0.08;
hiss(t,{filter:{type:'bandpass',f:3200*k,q:4},peak:0.12,a:0.001,d:0.006});
tone(t,{f0:180*k,peak:0.05,a:0.001,d:0.015});
},
boing(t){
tone(t,{type:'triangle',f0:180,f1:360,over:0.35,peak:0.2,a:0.005,d:0.45,vib:{rate:18,depth:40,to:0,span:0.4},filter:{type:'lowpass',f:2200}});
},
squeak(t){
tone(t,{f0:900,f1:1700,over:0.07,then:1100,over2:0.08,peak:0.1,a:0.003,d:0.16,vib:{rate:30,depth:60},filter:{type:'bandpass',f:1400,q:2}});
},
bonk(t){
tone(t,{f0:260,f1:90,over:0.09,peak:0.35,a:0.001,d:0.17});
hiss(t,{filter:{type:'lowpass',f:1200},peak:0.18,a:0.001,d:0.03});
tone(t,{type:'triangle',f0:520,f1:180,over:0.06,peak:0.08,a:0.001,d:0.08});
},
ding(t){
tone(t,{f0:1318.5,peak:0.16,a:0.003,d:0.9});
tone(t,{f0:2637,peak:0.04,a:0.003,d:0.5});
tone(t,{f0:3639,peak:0.03,a:0.002,d:0.25});
},
tada(t){
for(const f of[523.25,659.25,783.99])tone(t,{type:'sawtooth',f0:f,peak:0.045,a:0.005,d:0.1,filter:{type:'lowpass',f:800,to:2600,over:0.08}});
for(const f of[523.25,659.25,783.99,1046.5])tone(t+0.15,{type:'sawtooth',f0:f,peak:0.045,a:0.008,d:0.65,vib:{rate:5.5,depth:4},filter:{type:'lowpass',f:800,to:2600,over:0.08}});
},
jingle(t){
const notes=[[523.25,0,0.15],[659.25,0.18,0.15],[783.99,0.36,0.15],[880,0.54,0.09],[783.99,0.64,0.15],[1046.5,0.82,0.6]];
notes.forEach(([f,at,len],i)=>tone(t+at,{type:'square',f0:f,peak:0.07,a:0.004,d:len,vib:i===5?{rate:6,depth:5}:null,filter:{type:'lowpass',f:3000}}));
[[130.81,0],[98,0.36],[130.81,0.82]].forEach(([f,at])=>tone(t+at,{type:'triangle',f0:f,peak:0.12,a:0.01,d:0.3}));
SOUNDS.ding(t+1.45);
},
sad(t){
[392,370,349,330].forEach((f,i)=>tone(t+i*0.3,{type:'sawtooth',f0:f,peak:0.08,a:0.02,d:i===3?0.7:0.3,vib:i===3?{rate:6,depth:8}:null,filter:{type:'lowpass',f:1200}}));
},
};
function sfx(name,retried=false){
try{
if(!soundOn||!ctx||(typeof document!=='undefined'&&document.hidden))return;
if(ctx.state!=='running'){
if(!retried&&(ctx.state==='suspended'||ctx.state==='interrupted'))ctx.resume().then(()=>sfx(name,true)).catch(()=>{});
return;
}
const fn=SOUNDS[name];
if(!fn)return;
const now=performance.now();
const gap=MIN_GAP[name]||0;
if(gap&&now-(lastAt.get(name)||-1e9)<gap)return;
if(voices>=MAX_VOICES&&LOW.has(name))return;
lastAt.set(name,now);
fn(ctx.currentTime+0.01);
}catch{}
}
function setSound(wantOn,opts={}){
const persist=opts.persist!==false;
app.sound=wantOn;
setSoundOn(wantOn);
if(persist)store.set('sound',wantOn?'1':'0');
for(const b of $$('[data-sound]')){
b.setAttribute('aria-pressed',String(wantOn));
const use=$('use',b);
if(use)use.setAttribute('href',wantOn?'#i-sound':'#i-mute');
}
if(wantOn&&opts.ding!==false)sfx('ding');
if(persist)announce(wantOn?'Sound on':'Sound off');
}
function initPrefs(){
const vol=Number(store.get('vol','6'));
setVolume(Number.isFinite(vol)?Math.min(10,Math.max(0,Math.round(vol))):6);
for(const b of $$('[data-sound]'))b.addEventListener('click',()=>setSound(!app.sound));
const saved=store.get('sound','');
setSound(false,{persist:false});
if(saved==='1'){
const gestures=['click','keydown','touchend'];
const arm=(e)=>{
for(const t of gestures)removeEventListener(t,arm,true);
if(e.target&&e.target.closest&&e.target.closest('[data-sound]'))return;
setSound(true,{ding:false,persist:false});
};
for(const t of gestures)addEventListener(t,arm,true);
for(const b of $$('[data-sound]')){b.setAttribute('aria-pressed','true');const use=$('use',b);if(use)use.setAttribute('href','#i-sound');}
app.sound=true;
}else if(!saved&&!reduced()){
const b=$('[data-sound]');
setTimeout(()=>{if(b&&!app.sound){b.classList.add('hint');setTimeout(()=>b.classList.remove('hint'),1800);}},1500);
}
}
const keyBinds=[];
const keysOn=()=>store.get('keys','1')!=='0';
const setKeysOn=(on)=>store.set('keys',on?'1':'0');
function bindKey(keys,label,fn){
keyBinds.push({keys:[].concat(keys),label,fn});
}
const keyList=()=>keyBinds.map((b)=>({keys:b.keys,label:b.label}));
function initKeys(){
document.addEventListener('keydown',(e)=>{
if(e.defaultPrevented||e.ctrlKey||e.altKey||e.metaKey||e.repeat||typing(e.target)||!keysOn())return;
const k=e.key.length===1?e.key.toLowerCase():e.key;
const hit=keyBinds.find((b)=>b.keys.includes(k)||b.keys.includes(e.key));
if(!hit)return;
if(document.querySelector('dialog[open]'))return;
e.preventDefault();
hit.fn(e);
});
}
const STATUS_CODE={400:'invalid',401:'auth_required',403:'forbidden',404:'not_found',409:'conflict',413:'too_large',429:'ratelimited',500:'server_error',501:'not_implemented',502:'restarting',503:'busy',504:'restarting'};
const headerOf=(headers,name)=>{
if(!headers)return null;
if(typeof headers.get==='function')return headers.get(name);
const k=Object.keys(headers).find((x)=>x.toLowerCase()===name.toLowerCase());
return k?headers[k]:null;
};
const seconds=(v)=>{
const n=Number(v);
return Number.isFinite(n)&&n>0?Math.ceil(n):undefined;
};
const codeForStatus=(status)=>STATUS_CODE[status]||(status>=500?'server_error':'invalid');
function normalise(status,headers,body){
const res={ok:(status>=200&&status<300)||status===304,status,data:null};
const etag=headerOf(headers,'etag');
if(etag)res.etag=etag;
if(status===304){res.notModified=true;return res;}
if(status===0){res.error={code:body==='timeout'?'timeout':'offline',message:''};return res;}
const retryHeader=seconds(headerOf(headers,'retry-after'));
const requestId=headerOf(headers,'x-request-id')||undefined;
if(body&&typeof body==='object'&&!Array.isArray(body)){
if('data'in body)res.data=body.data;
if(res.ok&&body.success!==false)return res;
if(body.success===false&&typeof body.error==='string'){
const e={code:body.error,message:String(body.message||''),retryAfter:seconds(body.retry_after)??retryHeader,requestId:body.request_id||requestId};
if(body.errors)e.fields=fieldsOf(body.errors);
if(body.phase)e.phase=body.phase;
const extra={};
for(const[k,v]of Object.entries(body))if(!['success','error','message','retry_after','errors','phase','request_id','data'].includes(k))extra[k]=v;
if(Object.keys(extra).length)e.extra=extra;
res.error=e;
return res;
}
if(body.success===false&&body.errors){
res.error={code:'invalid',message:'',fields:fieldsOf(body.errors),requestId};
return res;
}
if(!res.ok&&body.success===true){
res.error={code:codeForStatus(status),message:String((body.data&&body.data.message)||''),retryAfter:retryHeader,requestId};
return res;
}
if(!res.ok){
res.error={code:codeForStatus(status),message:String(body.message||''),retryAfter:retryHeader,requestId};
return res;
}
return res;
}
if(!res.ok)res.error={code:codeForStatus(status),message:'',retryAfter:retryHeader,requestId};
return res;
}
function fieldsOf(errors){
const out={};
for(const[k,v]of Object.entries(errors))out[k]=Array.isArray(v)?v.map(String):[String(v)];
return out;
}
const ERROR_COPY={
auth_required:['SESSION ENDED.','Sign in again to carry on. What you typed is kept.'],
csrf:['SESSION CHANGED.','Reload the page to carry on.'],
forbidden:['RESTRICTED.','That is not available to your account.'],
banned:['OFF AIR.','This studio has been suspended.'],
unverified:['VERIFY FIRST.','Verify your email address to carry on.'],
no_team:['NO STUDIO YET.','Set up your studio to carry on.'],
phase_closed:['NOT NOW.','That is closed right now.'],
not_found:['NO SIGNAL.','This programme is not on air.'],
invalid:['CHECK THE FORM.','Some fields need another look.'],
conflict:['ALREADY DONE.','That has already happened.'],
quota:['TOO MANY RUNNING.','Your studio already has the most instances it can run.'],
renew_limit:['NOT YET.','This instance cannot be renewed yet.'],
too_large:['TOO MUCH AT ONCE.','Shorten what you typed and send it again.'],
ratelimited:['PLEASE STAND BY.','Slow down a little and try again.'],
cooldown:['PLEASE STAND BY.','Please wait a moment before starting it again.'],
server_error:['TECHNICAL DIFFICULTY.','Something broke on our side.'],
restarting:['BACK IN A MOMENT.','The platform is restarting.'],
busy:['SERVER BUSY.','Lots of viewers tuned in at once.'],
waiting_room:['YOU ARE IN LINE.','You will be let in by yourself.'],
maintenance:['BACK SOON.','Short break for upgrades.'],
capacity:['ALL HOSTS BUSY.','All instance hosts are busy.'],
not_implemented:['COMING UP NEXT.','This part of the prototype is built in a later slice.'],
offline:['NO CONNECTION.','Could not reach the studio. Check your connection and try again.'],
timeout:['NO CONNECTION.','The studio took too long to answer. Try again.'],
};
const FLAG_HINTS={
nudge:['CHECK THE FORMAT.','Flags look like L3m0n{...}. Press Send again to send it anyway.'],
empty:['','Type a flag first.'],
};
const reply=(kind,lead,text,more={})=>({kind,lead,text,retryAfter:0,lockInput:false,sound:null,...more});
function replyFor(r){
const d=r.data&&typeof r.data==='object'?r.data:null;
const extra=(d&&d.l3mon)||{};
if(d&&d.status){
const reason=extra.reason;
if(d.status==='correct'){
const pts=Number.isFinite(extra.value)?`+${extra.value} TRP`:'TRP added';
const reel=extra.reel?` · reel ${extra.reel} of ${extra.reels_needed}`:'';
return reply('ok','SIGNAL LOCKED.',pts+reel,{lockInput:true,sound:extra.channel_complete?'jingle':'tada'});
}
if(d.status==='already_solved')return reply('info','ALREADY ON AIR.','Your studio solved this one. No extra TRP.',{lockInput:true,sound:'ding'});
if(reason==='processing')return reply('wait','ONE MOMENT.','Another flag from your studio is being checked. Send again in a second.',{retryAfter:2});
if(reason==='ratelimited'||(d.status==='ratelimited')){
const s=(r.error&&r.error.retryAfter)||extra.retry_after||10;
return reply('wait','PLEASE STAND BY.',`Too many tries. Send again in ${s} s.`,{retryAfter:s,sound:'sad'});
}
if(reason==='no_tries')return reply('no','NO TRIES LEFT.','Your studio has used every try for this programme.',{lockInput:true,sound:'sad'});
if(reason==='ended')return reply('info',"THAT'S A WRAP.",'The broadcast has ended. Submissions are closed.',{lockInput:true});
if(reason==='paused'||d.status==='paused')return reply('wait','PAUSED.','Submissions are paused for a moment. Your flag was not checked.');
if(d.status==='incorrect'){
const left=extra.tries_left;
return reply('no','NO SIGNAL.',`That is not the flag.${Number.isFinite(left)?` ${left} ${left===1?'try':'tries'} left.`:''}`,{sound:'bonk'});
}
}
if(r.ok)return reply('ok','','');
const e=r.error||{code:codeForStatus(r.status)};
if(e.code==='phase_closed'&&(e.phase==='before'||(e.extra&&e.extra.phase==='before')))return reply('info','NOT ON AIR YET.','Submissions open when the broadcast starts.');
if(e.code==='phase_closed'&&e.phase==='ended')return reply('info',"THAT'S A WRAP.",'The broadcast has ended. Submissions are closed.',{lockInput:true});
if(e.code==='phase_closed'&&e.phase==='paused')return reply('wait','PAUSED.','Submissions are paused for a moment. Your flag was not checked.');
if(e.code==='busy'){
const s=e.retryAfter||6;
return reply('wait','SERVER BUSY.',`Lots of viewers tuned in at once. Nothing was sent. Try again in ${s} s.`,{retryAfter:s,sound:'static'});
}
if(e.code==='ratelimited'||e.code==='cooldown'){
const s=e.retryAfter||10;
const first=e.code==='cooldown'?'Please wait a moment.':'Slow down a little.';
return reply('wait','PLEASE STAND BY.',`${first} Try again in ${s} s.`,{retryAfter:s,sound:'sad'});
}
if(e.code==='auth_required')return reply('session',...ERROR_COPY.auth_required);
if(e.code==='csrf')return reply('session',...ERROR_COPY.csrf);
if(e.code==='server_error')return reply('no','TECHNICAL DIFFICULTY.',`Something broke on our side${e.requestId?` (request ${e.requestId})`:''}. Try again, or report it.`);
const copy=ERROR_COPY[e.code]||ERROR_COPY.server_error;
return reply('no',copy[0],copy[1],{retryAfter:e.retryAfter||0});
}
function flagReplyFor(r){
const out=replyFor(r);
const e=r.error;
if(!e||(r.data&&r.data.status))return out;
if(e.code==='busy')out.text=`Lots of viewers tuned in at once. Your flag was not checked. Send again in ${out.retryAfter} s.`;
else if(e.code==='offline'||e.code==='timeout')out.text='Could not reach the studio. Check your connection and send again.';
else if(e.code==='auth_required')out.text='Sign in again to carry on. What you typed is kept.';
else if(e.code==='csrf')out.text='Reload the page to carry on. Your flag is kept.';
else if(e.code==='server_error')out.text=`Something broke on our side${e.requestId?` (request ${e.requestId})`:''}. Send again, or report it.`;
return out;
}
function valueNote(value,start,floor){
return`${value<start?'Started':'Starts'} at ${start} TRP. Falls as more studios solve it, down to ${floor}.`;
}
let skewMs=0;
function syncClock(dateHeader,serverTimeS){
const now=Date.now();
if(Number.isFinite(serverTimeS))skewMs=serverTimeS*1000-now;
else if(dateHeader){
const t=Date.parse(dateHeader);
if(Number.isFinite(t))skewMs=t-now;
}
return skewMs;
}
const serverNow=()=>Date.now()+skewMs;
function fmtTtl(ms){
const total=Math.max(0,Math.ceil((Number(ms)||0)/1000));
const h=Math.floor(total/3600);
const m=Math.floor((total%3600)/60);
const s=total%60;
const ss=String(s).padStart(2,'0');
return h?`${h}:${String(m).padStart(2,'0')}:${ss}`:`${m}:${ss}`;
}
function ticker(fn){
let timer=0;
let stopped=false;
const tick=()=>{
if(stopped||document.hidden)return;
fn();
timer=setTimeout(tick,1000-(Date.now()%1000)+5);
};
const onVis=()=>{clearTimeout(timer);if(!document.hidden)tick();};
document.addEventListener('visibilitychange',onVis);
timer=setTimeout(tick,1000-(Date.now()%1000)+5);
return()=>{stopped=true;clearTimeout(timer);document.removeEventListener('visibilitychange',onVis);};
}
const ROUTES={
session:'/api/v1/l3mon/session',
resend:'/api/v1/l3mon/auth/resend',
teamAvailable:'/api/v1/l3mon/teams/available',
board:'/api/v1/l3mon/board',
ticks:'/api/v1/l3mon/ticks',
notifications:'/api/v1/notifications',
challenge:(id)=>`/api/v1/challenges/${encodeURIComponent(id)}`,
attempt:'/api/v1/challenges/attempt',
hint:(id)=>`/api/v1/hints/${encodeURIComponent(id)}`,
unlocks:'/api/v1/unlocks',
instance:(cid)=>`/api/v1/l3mon/instances/${encodeURIComponent(cid)}`,
guide:'/api/v1/l3mon/guide',
guideEpg:'/api/v1/l3mon/guide/epg',
scoreboard:'/api/v1/l3mon/scoreboard',
scoreboardRows:'/api/v1/l3mon/scoreboard/rows',
devState:'/__dev/api/state',
};
const API_TIMEOUT_MS=15000;
const API_RETRY_CODES=new Set(['busy','restarting','maintenance','ratelimited']);
const apiPause=(ms)=>new Promise((r)=>setTimeout(r,ms));
const apiCsrf=()=>{
const m=document.querySelector('meta[name="csrf-token"]');
return m?m.getAttribute('content'):'';
};
async function apiCall(method,url,opts={}){
const unsafe=method!=='GET';
const tries=unsafe?1:(opts.retries??3);
let result=null;
for(let attempt=1;attempt<=tries;attempt++){
result=await apiOnce(method,url,opts);
emit('net',result);
const code=result.error&&result.error.code;
if(result.ok||!API_RETRY_CODES.has(code)||attempt===tries)break;
const wait=Math.max(result.error.retryAfter||0,2+Math.random()*6);
if(opts.onWait)opts.onWait(Math.ceil(wait));
await apiPause(wait*1000);
}
return result;
}
async function apiOnce(method,url,opts){
const headers={Accept:opts.html?'text/html':'application/json'};
if(method!=='GET')headers['CSRF-Token']=apiCsrf();
if(opts.etag)headers['If-None-Match']=opts.etag;
let body;
if(opts.body!==undefined){headers['Content-Type']='application/json';body=JSON.stringify(opts.body);}
const ctl=typeof AbortController==='function'?new AbortController():null;
const timer=ctl?setTimeout(()=>ctl.abort(),API_TIMEOUT_MS):0;
try{
const res=await fetch(url,{method,headers,body,credentials:'same-origin',cache:'no-store',signal:ctl?ctl.signal:undefined});
let parsed=null;
const type=res.headers.get('content-type')||'';
if(res.status!==304){
const text=await res.text();
if(type.includes('application/json')){try{parsed=JSON.parse(text);}catch{parsed=text;}}else parsed=text;
}
const r=normalise(res.status,res.headers,parsed);
if(opts.html&&r.ok&&!r.notModified&&typeof parsed==='string')r.html=parsed;
syncClock(res.headers.get('date'),r.data&&typeof r.data==='object'?r.data.server_time:undefined);
return r;
}catch(e){
return normalise(0,null,e&&e.name==='AbortError'?'timeout':'offline');
}finally{
clearTimeout(timer);
}
}
const session=(o)=>apiCall('GET',ROUTES.session,o);
const resendVerification=()=>apiCall('POST',ROUTES.resend,{body:{}});
const teamAvailable=(name)=>apiCall('GET',`${ROUTES.teamAvailable}?name=${encodeURIComponent(name)}`,{retries:1});
const board=(etag,o={})=>apiCall('GET',ROUTES.board,{...o,etag});
const ticks=()=>apiCall('GET',ROUTES.ticks,{retries:1});
const notifications=(sinceId)=>apiCall('GET',`${ROUTES.notifications}?since_id=${encodeURIComponent(sinceId||0)}`,{retries:1});
const challenge=(id,etag)=>apiCall('GET',ROUTES.challenge(id),{etag});
const attempt=(challengeId,submission)=>apiCall('POST',ROUTES.attempt,{body:{challenge_id:challengeId,submission}});
const hint=(id)=>apiCall('GET',ROUTES.hint(id));
const unlockHint=(id)=>apiCall('POST',ROUTES.unlocks,{body:{target:id,type:'hints'}});
const instance=(cid)=>apiCall('GET',ROUTES.instance(cid),{retries:1});
const startInstance=(cid)=>apiCall('POST',ROUTES.instance(cid));
const renewInstance=(cid)=>apiCall('PATCH',ROUTES.instance(cid));
const stopInstance=(cid)=>apiCall('DELETE',ROUTES.instance(cid));
const guide=(etag)=>apiCall('GET',ROUTES.guide,{etag});
const guideEpg=(etag)=>apiCall('GET',ROUTES.guideEpg,{etag,html:true});
const scoreboard=(etag)=>apiCall('GET',ROUTES.scoreboard,{etag});
const scoreboardRows=(etag)=>apiCall('GET',ROUTES.scoreboardRows,{etag,html:true});
const devState=()=>apiCall('GET',ROUTES.devState,{retries:1});
const devSet=(patch)=>apiCall('POST',ROUTES.devState,{body:patch});
const SHOWN=20;
const nf={all:[],ver:null,loading:false,again:false,prev:null};
const nfSeen=()=>{
const[a,b]=String(store.get('seen','0')).split(':');
const id=Number(a);
const ver=b===undefined?null:Number(b);
return{id:Number.isInteger(id)&&id>=0?id:0,ver:Number.isInteger(ver)?ver:null};
};
const nfMax=()=>nf.all.reduce((m,n)=>Math.max(m,n.id),0);
const nfUnread=()=>{const s=nfSeen().id;return nf.all.filter((n)=>n.id>s).length;};
function nfBadge(){
const n=nfUnread();
const text=n>9?'9+':String(n);
for(const b of $$('[data-notif-n]')){
const changed=b.textContent!==text;
b.hidden=n===0;
b.textContent=n?text:'';
if(n&&changed){b.classList.remove('pop');void b.offsetWidth;b.classList.add('pop');}
}
for(const bell of $$('[data-notif]'))bell.setAttribute('aria-label',n?`Notifications, ${n} new`:'Notifications');
if(nf.prev!==null&&n>nf.prev&&!$('[data-board]'))announce(`${n} unread notification${n===1?'':'s'}.`);
nf.prev=n;
}
async function nfLoad(){
if(nf.loading){nf.again=true;return;}
nf.loading=true;
try{
const since=nfMax();
const r=await notifications(since);
if(r.ok&&Array.isArray(r.data)){
const have=new Set(nf.all.map((n)=>n.id));
for(const n of r.data)if(Number.isInteger(n.id)&&!have.has(n.id))nf.all.push({id:n.id,title:String(n.title||'News'),content:String(n.content||'')});
nf.all.sort((a,b)=>a.id-b.id);
nfBadge();
}
}finally{
nf.loading=false;
if(nf.again){nf.again=false;nfLoad();}
}
}
function nfOpen(bell){
store.set('seen',`${nfMax()}:${nf.ver??0}`);
nfBadge();
openDialog('Notifications',(body)=>{
const items=[...nf.all].reverse().slice(0,SHOWN);
if(!items.length){body.append(mk('p',null,'Nothing to report yet. All quiet on the airwaves.'));return;}
const list=mk('ul','nt-dlg');
for(const n of items){
const li=mk('li');
li.append(mk('b','nt-t',n.title));
if(n.content)li.append(' ',mk('span','nt-c',n.content));
list.append(li);
}
body.append(list);
const more=mk('a','nt-all','Open the full list');
more.href='/notifications';
const p=mk('p','dlg-note');
p.append(more);
body.append(p);
},bell);
}
function nfRestarted(v){
const s=nfSeen();
return(nf.ver!==null&&v<nf.ver)||(s.ver!==null&&v<s.ver);
}
function initNotif(){
const bells=$$('[data-notif]');
if(!bells.length)return;
for(const bell of bells){
bell.addEventListener('click',(e)=>{
if(e.defaultPrevented||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
e.preventDefault();
nfOpen(bell);
});
}
on('net',(r)=>{
const v=r&&r.ok&&r.data&&!Array.isArray(r.data)?r.data.notif_ver:undefined;
if(!Number.isFinite(v))return;
const first=nf.ver===null;
if(nfRestarted(v)){nf.all=[];nf.prev=null;store.set('seen',`0:${v}`);}
if(first||v!==nf.ver){nf.ver=v;nfLoad();}
});
nfLoad();
}
const SPOKEN={bad:'You are offline. The page keeps trying.',wait:'The service is busy. The page keeps trying.'};
function pillShow(state,text){
const pill=$('[data-pill]');
if(!pill)return;
const changed=pill.hidden||pill.dataset.state!==state;
pill.dataset.state=state;
pill.className=`pill pill-${state}`;
pill.textContent=text;
pill.hidden=false;
if(changed)announce(SPOKEN[state]);
}
function pillHide(){
const pill=$('[data-pill]');
if(!pill||pill.hidden)return;
pill.hidden=true;
pill.textContent='';
delete pill.dataset.state;
announce('You are back online.');
}
const CLICKS='.btn, .osd-btn, .brand, .osd-menu-btn, .linkbtn, .chipbtn, [role="tab"], .tile-a, summary, .seg-o, .choice, .check, .epg-b';
const HOVERS='.btn, .osd-btn, .chipbtn, [role="tab"], .tile-a, .seg-o';
function initMotionClass(){
if(!motionQuery)return;
const sync=()=>document.documentElement.classList.toggle('rm',motionQuery.matches);
sync();
motionQuery.addEventListener('change',sync);
}
function initMenu(){
const btn=$('[data-menu]');
const panel=$('[data-menu-panel]');
if(!btn||!panel)return;
const set=(open)=>{panel.classList.toggle('open',open);btn.setAttribute('aria-expanded',String(open));};
btn.addEventListener('click',()=>set(btn.getAttribute('aria-expanded')!=='true'));
document.addEventListener('keydown',(e)=>{
if(e.key==='Escape'&&btn.getAttribute('aria-expanded')==='true'&&panel.contains(document.activeElement)){set(false);btn.focus();}
});
document.addEventListener('click',(e)=>{if(btn.getAttribute('aria-expanded')==='true'&&!panel.contains(e.target)&&!btn.contains(e.target)&&!(e.target.closest&&e.target.closest('dialog')))set(false);});
}
const KEY_NAMES={'?':'?',m:'M'};
function showHelp(opener){
openDialog('Help and shortcuts',(body)=>{
const list=mk('ul','keys');
for(const k of keyList()){
const li=mk('li');
for(const key of k.keys.slice(0,2))li.append(mk('kbd',null,KEY_NAMES[key]||key));
li.append(document.createTextNode(k.label));
list.append(li);
}
const sw=mk('button','btn btn-sm toggle','Single-key shortcuts');
sw.type='button';
sw.setAttribute('aria-pressed',String(keysOn()));
sw.addEventListener('click',()=>{
const next=!keysOn();
setKeysOn(next);
sw.setAttribute('aria-pressed',String(next));
toast(next?'Single-key shortcuts are on.':'Single-key shortcuts are off.');
});
const swp=mk('p','keys-switch');
swp.append(sw);
body.append(mk('p',null,'These keys work when you are not typing in a field:'),list,swp,
mk('p','dlg-note','If your system asks for reduced motion, the pages keep still.'));
},opener);
}
function initPill(){
addEventListener('offline',()=>pillShow('bad','OFFLINE'));
on('net',(r)=>{
const code=r.error&&r.error.code;
if(r.ok)pillHide();
else if(code==='offline'||code==='timeout')pillShow('bad','OFFLINE');
else if(['busy','restarting','maintenance','waiting_room'].includes(code))pillShow('wait',r.error.retryAfter?`BUSY · retrying in ${r.error.retryAfter} s`:'BUSY');
});
}
function initCrawl(){
const crawl=$('[data-crawl]');
if(!crawl)return;
const lists=$$('.crawl-list',crawl);
if(lists.length!==2)return;
const btn=mk('button','crawl-btn');
btn.type='button';
btn.setAttribute('aria-label','Pause news crawl');
btn.setAttribute('aria-pressed','false');
btn.addEventListener('click',()=>btn.setAttribute('aria-pressed',String(crawl.classList.toggle('paused'))));
crawl.append(btn);
const fit=()=>crawl.style.setProperty('--crawl-d',`${Math.max(12,lists[0].scrollWidth/80).toFixed(1)}s`);
fit();
if('ResizeObserver'in window)new ResizeObserver(fit).observe(lists[0]);
}
function initBurst(){
if(typeof matchMedia!=='function'||!matchMedia('(hover: hover) and (pointer: fine)').matches)return;
const pool=[];
let next=0;
document.addEventListener('pointerdown',(e)=>{
if(e.pointerType!=='mouse'||e.button!==0||reduced())return;
if(pool.length<4){
const el=mk('div','burst');
el.setAttribute('aria-hidden','true');
for(let i=0;i<6;i++)el.append(mk('i'));
document.body.append(el);
pool.push(el);
}
const el=pool[next++%pool.length];
el.style.transform=`translate(${e.clientX}px, ${e.clientY}px)`;
[...el.children].forEach((ray,i)=>{
const a=i*60+(Math.random()*20-10);
ray.animate([{transform:`rotate(${a}deg) translateY(0) scale(.2)`,opacity:1},{transform:`rotate(${a}deg) translateY(-26px) scale(1)`,opacity:0}],{duration:380,easing:'cubic-bezier(.22,1,.36,1)'});
});
},{passive:true});
}
function initSounds(){
let last=null;
document.addEventListener('pointerover',(e)=>{
if(e.pointerType!=='mouse')return;
const t=e.target.closest?e.target.closest(HOVERS):null;
if(t===last)return;
last=t;
if(t)sfx('hover');
},{passive:true});
document.addEventListener('click',(e)=>{if(e.target.closest&&e.target.closest(CLICKS))sfx('click');});
}
const NS='l3m0n.v1.';
function initSignOut(){
document.addEventListener('submit',(e)=>{
const f=e.target;
if(!(f instanceof HTMLFormElement)||!f.matches('.osd-out'))return;
try{
for(let i=sessionStorage.length-1;i>=0;i--){
const k=sessionStorage.key(i);
if(k&&k.startsWith(NS+'draft.'))sessionStorage.removeItem(k);
}
}catch{}
});
}
function initShell(){
initMotionClass();
initSignOut();
initMenu();
initPill();
initCrawl();
initBurst();
initSounds();
initNotif();
const help=$('[data-help]');
if(help)help.addEventListener('click',()=>showHelp(help));
bindKey('?','Help and shortcuts (this list)',()=>showHelp(help));
bindKey('m','Sound on or off',()=>setSound(!app.sound));
let ended=false;
on('net',(r)=>{if(!ended&&r.error&&r.error.code==='auth_required'){ended=true;sessionEnded();}});
document.addEventListener('visibilitychange',()=>audioVisibility(document.hidden));
}
const TILE_COLS=6;
const TILE_MAX_ROW=11;
const TILE_CATS={
web:['Web','i-web'],crypto:['Crypto','i-crypto'],forensics:['Forensics','i-forensics'],osint:['OSINT','i-osint'],
pwn:['Pwn','i-pwn'],reverse:['Reverse','i-reverse'],hardware:['Hardware','i-hardware'],rf:['RF','i-rf'],
ai:['AI','i-ai'],web3:['Web3','i-web3'],misc:['Misc','i-misc'],
};
const TILE_DIFF={warmup:[1,'warm-up'],easy:[2,'easy'],medium:[3,'medium'],hard:[4,'hard'],insane:[5,'insane']};
const tileNumber=(p)=>String(p.number).padStart(3,'0');
const tileLive=(p)=>!!(p.instance&&p.instance.state==='running');
function tileName(p){
const parts=[p.name,(TILE_CATS[p.category]||[p.category])[0],`${p.value} TRP`,(TILE_DIFF[p.difficulty]||[0,p.difficulty])[1],p.solved_by_me?'solved':'not solved'];
if(tileLive(p))parts.push('your instance is running');
if(!p.solved_by_me&&p.first_blood_open)parts.push('first blood open');
return parts.join(', ');
}
function tileBadge(p){
if(p.solved_by_me)return['ok','✓','SOLVED'];
if(tileLive(p))return['live','●','LIVE'];
if(p.first_blood_open)return['fb','★','FIRST BLOOD'];
return null;
}
function tileGridMove({count,cols,pos,key,ctrl=false,ok=()=>true}){
const c=Math.max(1,cols|0);
const scan=(from,step,until)=>{
for(let i=from;step>0?i<until:i>=until;i+=step)if(ok(i))return i;
return pos;
};
const rowStart=Math.floor(pos/c)*c;
const rowEnd=Math.min(count,rowStart+c);
switch(key){
case'ArrowRight':return scan(pos+1,1,count);
case'ArrowLeft':return scan(pos-1,-1,0);
case'ArrowDown':return scan(pos+c,c,count);
case'ArrowUp':return scan(pos-c,-c,0);
case'Home':return ctrl?scan(0,1,count):scan(rowStart,1,rowEnd);
case'End':return ctrl?scan(count-1,-1,0):scan(rowEnd-1,-1,rowStart);
default:return pos;
}
}
function tileClasses(li,base,i){
const r=Math.floor(i/TILE_COLS);
li.className=`${base} tc-${i%TILE_COLS} tr-${Math.min(r,TILE_MAX_ROW)}${li.classList.contains('locking')?' locking':''}`;
if(r>TILE_MAX_ROW)li.style.setProperty('--r',String(r));
}
function tilePatch(li,p,i){
tileClasses(li,'tile',i);
li.dataset.id=String(p.id);
li.dataset.state=p.solved_by_me?'solved':'open';
const a=li.firstElementChild;
const href=`/board/${p.slug}`;
if(a.getAttribute('href')!==href)a.setAttribute('href',href);
a.dataset.slug=p.slug;
a.setAttribute('aria-label',tileName(p));
const cat=TILE_CATS[p.category]||[p.category,'i-misc'];
for(const el of li.querySelectorAll('[data-k]')){
const k=el.dataset.k;
if(k==='name')el.textContent=p.name;
else if(k==='pts')el.textContent=String(p.value);
else if(k==='no')el.textContent=tileNumber(p);
else if(k==='cat')el.textContent=cat[0];
else if(k==='badge'){
const b=tileBadge(p);
el.hidden=!b;
if(b){
el.className=`tile-badge b-${b[0]}`;
const word=document.createElement('span');
word.className='tile-bw';
word.textContent=` ${b[2]}`;
el.replaceChildren(b[1],word);
}
}
}
const use=li.querySelector('.tile-cat use');
if(use&&use.getAttribute('href')!==`#${cat[1]}`)use.setAttribute('href',`#${cat[1]}`);
const diff=li.querySelector('.tile-diff');
if(diff)diff.className=`tile-diff d-${(TILE_DIFF[p.difficulty]||[3])[0]}`;
}
function tileMake(tpl,p,i){
const li=tpl.content.firstElementChild.cloneNode(true);
tilePatch(li,p,i);
return li;
}
function tileSoon(i){
const li=document.createElement('li');
li.setAttribute('aria-hidden','true');
tileClasses(li,'tile tile-fill tile-soon',i);
const pic=document.createElement('i');
pic.className='tile-pic soft';
const label=document.createElement('span');
label.className='tile-soon-l';
label.textContent='COMING UP';
li.append(pic,label);
return li;
}
function tileFiller(i){
const li=document.createElement('li');
li.setAttribute('aria-hidden','true');
tileClasses(li,'tile tile-fill',i);
const pic=document.createElement('i');
pic.className='tile-pic soft';
li.append(pic);
return li;
}
const fltEmpty=()=>({q:'',cats:new Set(),diff:'',show:'all'});
const fltActive=(f)=>!!(f.q.trim()||f.cats.size||f.diff||f.show!=='all');
const fltCount=(f)=>(f.q.trim()?1:0)+f.cats.size+(f.diff?1:0)+(f.show!=='all'?1:0);
function fltMatch(p,f){
if(f.show==='open'&&p.solved_by_me)return false;
if(f.show==='solved'&&!p.solved_by_me)return false;
if(f.diff&&p.difficulty!==f.diff)return false;
if(f.cats.size&&!f.cats.has(p.category))return false;
const words=f.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
if(!words.length)return true;
const hay=`${p.name} ${String(p.number).padStart(3,'0')} ${p.category}`.toLowerCase();
return words.every((w)=>hay.includes(w));
}
const fltPlural=(n)=>`${n} programme${n===1?'':'s'}`;
const fltCountText=(n,total,active,coming=0)=>(active?`${n} of ${fltPlural(total)} match`:fltPlural(total))+(coming>0?` · ${coming} coming up`:'');
const fltMeter=(n,total)=>{const on=n>0&&total>0?Math.max(1,Math.round((n/total)*6)):0;return'▰'.repeat(on)+'▱'.repeat(6-on);};
const fltSay=(n,total)=>`${n} of ${fltPlural(total)} match.`;
function initFilters(form,{onChange,onView}){
const state=fltEmpty();
const q=form.querySelector('[data-q]');
const diff=form.querySelector('[data-diff]');
const cats=[...form.querySelectorAll('[data-cat]')];
const toggle=form.querySelector('[data-flt-toggle]');
const more=form.querySelector('[data-flt-more]');
const radios=(name)=>[...form.querySelectorAll(`input[name="${name}"]`)];
const labelEl=toggle&&toggle.querySelector('[data-flt-label]');
const label=()=>{
if(!labelEl)return;
const n=fltCount(state);
labelEl.textContent=n?`Search & filter (${n})`:'Search & filter';
toggle.classList.toggle('on',n>0);
};
const setOpen=(open)=>{
if(!toggle||!more)return;
toggle.setAttribute('aria-expanded',String(open));
more.classList.toggle('open',open);
};
const changed=()=>{label();onChange(state);};
form.addEventListener('submit',(e)=>e.preventDefault());
q.addEventListener('input',()=>{state.q=q.value.slice(0,60);changed();});
q.addEventListener('keydown',(e)=>{
if(e.key==='Escape'&&q.value){e.preventDefault();e.stopPropagation();q.value='';state.q='';changed();}
});
for(const b of cats){
b.addEventListener('click',()=>{
const on=b.getAttribute('aria-pressed')!=='true';
b.setAttribute('aria-pressed',String(on));
if(on)state.cats.add(b.dataset.cat);else state.cats.delete(b.dataset.cat);
changed();
});
}
diff.addEventListener('change',()=>{state.diff=diff.value;changed();});
for(const r of radios('show'))r.addEventListener('change',()=>{if(r.checked){state.show=r.value;changed();}});
for(const r of radios('view'))r.addEventListener('change',()=>{if(r.checked)onView(r.value);});
if(toggle&&more){
toggle.addEventListener('click',()=>setOpen(toggle.getAttribute('aria-expanded')!=='true'));
form.addEventListener('keydown',(e)=>{
if(e.key!=='Escape'||toggle.getAttribute('aria-expanded')!=='true'||(e.target===q&&q.value))return;
e.preventDefault();
e.stopPropagation();
setOpen(false);
toggle.focus();
});
form.addEventListener('focusout',(e)=>{
if(toggle.getAttribute('aria-expanded')==='true'&&e.relatedTarget&&!form.contains(e.relatedTarget))setOpen(false);
});
document.addEventListener('pointerdown',(e)=>{
if(toggle.getAttribute('aria-expanded')==='true'&&!form.contains(e.target))setOpen(false);
});
}
const setShow=(v)=>{state.show=v;for(const r of radios('show'))r.checked=r.value===v;};
return{
state,
clear(){
q.value='';state.q='';
state.cats.clear();
for(const b of cats)b.setAttribute('aria-pressed','false');
diff.value='';state.diff='';
setShow('all');
changed();
},
toggleOpen(){setShow(state.show==='open'?'all':'open');changed();},
focusSearch(){setOpen(true);q.focus();q.select();},
focusToggle(){if(toggle)toggle.focus();},
setView(v){for(const r of radios('view'))r.checked=r.value===v;},
};
}
const SH_KEEP=new Set(['p','br','strong','em','code','pre','ul','ol','li','blockquote','a']);
const SH_DROP=new Set(['script','style','template','noscript','iframe','object','embed','svg','math','head','title','link','meta']);
const SH_MAX_NODES=4000;
const SH_MAX_DEPTH=24;
function safeHref(raw){
const v=String(raw||'').trim();
if(!/^https:\/\//i.test(v))return null;
try{
const u=new URL(v);
return u.protocol==='https:'&&!u.username&&!u.password?u.href:null;
}catch{return null;}
}
function cleanChildren(src,doc,depth=0,budget={n:0}){
const out=doc.createDocumentFragment();
for(const node of Array.from(src.childNodes||[])){
if(++budget.n>SH_MAX_NODES)break;
if(node.nodeType===3){out.append(doc.createTextNode(node.nodeValue));continue;}
if(node.nodeType!==1)continue;
const tag=String(node.nodeName).toLowerCase();
if(SH_DROP.has(tag))continue;
const kids=depth<SH_MAX_DEPTH?cleanChildren(node,doc,depth+1,budget):doc.createTextNode(node.textContent||'');
if(!SH_KEEP.has(tag)){out.append(kids);continue;}
if(tag==='a'){
const href=safeHref(node.getAttribute&&node.getAttribute('href'));
if(!href){out.append(kids);continue;}
const a=doc.createElement('a');
a.setAttribute('href',href);
a.setAttribute('rel','noopener noreferrer');
a.setAttribute('target','_blank');
a.append(kids);
out.append(a);
continue;
}
const el=doc.createElement(tag);
el.append(kids);
out.append(el);
}
return out;
}
function safeHtml(html){
const parsed=new DOMParser().parseFromString(String(html||''),'text/html');
return cleanChildren(parsed.body,document);
}
const FL_NS='l3m0n.v1.';
const FL_DRAFT_MS=30*60e3;
const FL_FORMAT=/^L3m0n\{.+\}$/;
const FL_PHASE_POLL_MS=15000;
const flEl={};
let flS=null;
let flGen=0;
let flCtx=null;
const flKey=(id)=>`${FL_NS}draft.${id}`;
function flDraft(id,value){
try{
if(value===undefined){
const raw=sessionStorage.getItem(flKey(id));
if(!raw)return'';
const d=JSON.parse(raw);
if(!d||typeof d.v!=='string'||!(Date.now()-d.t<FL_DRAFT_MS)){sessionStorage.removeItem(flKey(id));return'';}
return d.v;
}
if(value)sessionStorage.setItem(flKey(id),JSON.stringify({v:value.slice(0,256),t:Date.now()}));
else sessionStorage.removeItem(flKey(id));
}catch{}
return'';
}
function initFlag(ctx){
const form=$('[data-pn-flag]');
if(!form)return;
flCtx=ctx;
Object.assign(flEl,{
form,input:$('#pn-flag-in',form),send:$('[data-pn-send]',form),reply:$('[data-pn-reply]',form),more:$('[data-pn-more]',form),
hint:$('#pn-flag-hint',form),phase:$('[data-pn-phase]'),
});
flEl.hintBase=flEl.hint.textContent;
form.addEventListener('submit',flSubmit);
flEl.input.addEventListener('input',()=>{
if(!flS)return;
flDraft(flS.id,flEl.input.value);
if(flEl.input.value.trim()!==flS.nudged)flS.nudged='';
});
}
function flagOpen(data){
flagClose();
if(!flEl.form)return;
flS={id:data.id,data,nudged:'',gen:++flGen,locked:false};
flEl.form.action=`/board/${data.l3mon.slug}/attempt`;
flEl.form.classList.remove('wrapped');
flSay(null);
flMore(null);
flEl.input.readOnly=false;
flEl.send.removeAttribute('aria-disabled');
flEl.phase.hidden=true;
const left=data.l3mon.tries_left;
flEl.hint.textContent=Number.isFinite(left)?`${flEl.hintBase} ${left} ${left===1?'try':'tries'} left on this programme.`:flEl.hintBase;
flEl.input.value=flDraft(data.id);
if(data.solved_by_me)flLock('Your studio has solved this programme. Nothing more to send.');
else if(Number.isFinite(left)&&left<=0)flLock('Your studio has used every try for this programme.');
const ph=data.l3mon.phase.state;
if(ph==='paused')flPaused();
else if(ph==='ended')flWrap('note');
}
function flagClose(){
if(!flS)return;
if(flS.wait)flS.wait();
clearTimeout(flS.watch);
flGen++;
flS=null;
if(flEl.send)clearPending(flEl.send);
}
function flagFocus(){
if(flEl.input&&!flEl.form.classList.contains('wrapped'))flEl.input.focus();
}
function flSay(rep){
const el=flEl.reply;
el.classList.remove('pop');
if(!rep){el.textContent='';el.removeAttribute('data-kind');return;}
el.dataset.kind=rep.kind;
el.replaceChildren();
if(rep.lead)el.append(mk('b','reply-lead',rep.lead),' ');
el.append(rep.text);
}
function flMore(text,action){
const m=flEl.more;
m.replaceChildren();
m.hidden=!text&&!action;
if(text)m.append(text);
if(action){if(text)m.append(' ');m.append(action);}
}
function flLock(note){
flS.locked=true;
flEl.input.readOnly=true;
flEl.send.setAttribute('aria-disabled','true');
if(note)flMore(note);
}
function flHold(seconds,lead='Send again in'){
if(flS.wait)flS.wait();
flEl.send.setAttribute('aria-disabled','true');
flS.wait=countdown(seconds,(s)=>flMore(`${lead} ${s} s.`),()=>{
if(!flS)return;
flS.wait=null;
if(!flS.locked)flEl.send.removeAttribute('aria-disabled');
flMore('You can send again.');
});
}
function flPaused(){
flEl.phase.replaceChildren(mk('b','reply-lead','PAUSED.'),' Submissions are paused for a moment. Your progress is safe.');
flEl.phase.hidden=false;
flEl.send.setAttribute('aria-disabled','true');
const gen=flS.gen;
const check=async()=>{
if(!flS||flS.gen!==gen)return;
if(document.hidden){flS.watch=setTimeout(check,FL_PHASE_POLL_MS);return;}
const r=await session({retries:1});
if(!flS||flS.gen!==gen)return;
const st=r.ok&&r.data&&r.data.phase?r.data.phase.state:'paused';
if(st==='live'){
flEl.phase.hidden=true;
if(!flS.locked)flEl.send.removeAttribute('aria-disabled');
flSay({kind:'ok',lead:'BACK ON AIR.',text:'You can send flags again.'});
if(flCtx.onPhase)flCtx.onPhase('live');
}else if(st==='ended')flWrap('say');
else flS.watch=setTimeout(check,FL_PHASE_POLL_MS);
};
flS.watch=setTimeout(check,FL_PHASE_POLL_MS);
}
function flWrap(how){
flEl.phase.hidden=true;
flS.locked=true;
flEl.form.classList.add('wrapped');
flEl.input.readOnly=true;
flEl.send.setAttribute('aria-disabled','true');
const rep={kind:'info',lead:"THAT'S A WRAP.",text:'The broadcast has ended. Submissions are closed.'};
if(how==='say')flSay(rep);
else if(how==='note')flMore(`${rep.lead} ${rep.text}`);
}
async function flSubmit(e){
e.preventDefault();
if(!flS||flS.locked||flEl.send.getAttribute('aria-disabled')==='true')return;
const v=flEl.input.value.trim();
if(!v){flSay({kind:'info',lead:FLAG_HINTS.empty[0],text:FLAG_HINTS.empty[1]});flEl.input.focus();return;}
if(!FL_FORMAT.test(v)&&flS.nudged!==v){
flS.nudged=v;
flSay({kind:'wait',lead:FLAG_HINTS.nudge[0],text:FLAG_HINTS.nudge[1]});
flEl.input.focus();
return;
}
const gen=flS.gen;
setPending(flEl.send,'Checking…');
flMore(null);
const r=await attempt(flS.id,v);
if(!flS||gen!==flS.gen)return;
clearPending(flEl.send);
flS.nudged='';
flApply(r);
}
function flApply(r){
const rep=flagReplyFor(r);
const d=r.data&&typeof r.data==='object'?r.data:{};
const l3=d.l3mon||{};
const code=r.error?r.error.code:'';
if(code==='invalid'&&r.error.fields&&r.error.fields.submission){rep.lead='';rep.text=r.error.fields.submission[0];}
if(code==='forbidden'&&!d.status&&r.error.message)rep.text=r.error.message;
flSay(rep);
if(rep.sound)sfx(rep.sound);
const input=flEl.input;
if(d.status==='correct'){
flDraft(flS.id,'');
flLock(null);
if(!reduced()){flEl.reply.classList.add('pop');flBurst(flEl.send);}
flCtx.onCorrect(l3);
}else if(d.status==='already_solved'){
flDraft(flS.id,'');
flLock(null);
flCtx.onAlready();
}else if(l3.reason==='no_tries')flLock(null);
else if(l3.reason==='ended'||(code==='phase_closed'&&r.error.phase==='ended'))flWrap('none');
else if(l3.reason==='paused'||(code==='phase_closed'&&r.error.phase==='paused'))flPaused();
else if(d.status==='incorrect'){
input.classList.remove('shake');
void input.offsetWidth;
if(!reduced())input.classList.add('shake');
input.focus();
input.select();
}else if(rep.retryAfter>0)flHold(rep.retryAfter);
else if(code==='auth_required')flDraft(flS.id,input.value);
else if(code==='csrf'){
flDraft(flS.id,input.value);
const b=mk('button','btn btn-sm','Reload the page');
b.type='button';
b.addEventListener('click',()=>location.reload());
flMore(null,b);
}else if(code==='server_error'){
const b=mk('button','linkbtn','Report it');
b.type='button';
b.addEventListener('click',()=>flCtx.report(b));
flMore(null,b);
}
}
function flBurst(target){
if(!target.getBoundingClientRect)return;
const r=target.getBoundingClientRect();
const el=mk('div','burst');
el.setAttribute('aria-hidden','true');
el.style.transform=`translate(${r.left+r.width/2}px, ${r.top+r.height/2}px)`;
target.closest('dialog').append(el);
for(let i=0;i<10;i++){
const ray=mk('i');
el.append(ray);
const a=i*36;
ray.animate([{transform:`rotate(${a}deg) translateY(0) scale(.3)`,opacity:1},{transform:`rotate(${a}deg) translateY(-40px) scale(1)`,opacity:0}],{duration:520,easing:'cubic-bezier(.22,1,.36,1)'});
}
setTimeout(()=>el.remove(),600);
}
function reportDialog(opener){
openDialog('Report a problem',(body)=>{
body.append(mk('p',null,"Prototype: reports go to the crew's channel in the real platform. Nothing was sent from here."));
},opener);
}
const HN_AD_MS=4000;
const hnEl={};
let hnS=null;
let hnGen=0;
let hnAd=null;
function initHints(ctx){
const ad=$('[data-pn-ad]');
hnEl.list=$('[data-pn-hint-list]');
hnEl.sec=$('[data-pn-hints]');
hnEl.tpl=$('#tpl-hint');
if(!ad||!hnEl.list||!hnEl.tpl)return;
Object.assign(hnEl,{
ad,text:$('[data-ad-text]',ad),reel:$('[data-ad-reel]',ad),err:$('[data-ad-err]',ad),actions:$('[data-ad-actions]',ad),
cancel:$('[data-ad-cancel]',ad),unlock:$('[data-ad-unlock]',ad),watch:$('[data-ad-watch]',ad),
skip:$('[data-ad-skip]',ad),next:$('[data-ad-next]',ad),ctx,
});
hnEl.cancel.addEventListener('click',()=>ad.close());
hnEl.unlock.addEventListener('click',()=>hnUnlock());
hnEl.watch.addEventListener('click',hnPlay);
hnEl.skip.addEventListener('click',hnAdDone);
hnEl.next.addEventListener('click',()=>{
if(!hnAd)return;
if(hnAd.frame===1){hnAd.frame=2;hnEl.reel.dataset.frame='2';hnEl.next.textContent='Unlock the hint';hnEl.next.focus();}else hnAdDone();
});
ad.addEventListener('click',(e)=>{if(e.target===ad&&!(hnAd&&hnAd.busy))ad.close();});
ad.addEventListener('cancel',(e)=>{if(hnAd&&hnAd.busy)e.preventDefault();});
ad.addEventListener('close',()=>{
const was=hnAd;
hnAdStop();
hnAd=null;
if(was&&was.focusAfter&&document.contains(was.focusAfter))was.focusAfter.focus({preventScroll:false});
else if(was&&was.row&&document.contains(was.row)){const b=$('[data-k="unlock"]',was.row);if(b)b.focus({preventScroll:true});}
});
}
function hintsOpen(data,ctx){
if(!hnEl.list)return;
hnS={data,ctx:ctx||hnEl.ctx,gen:++hnGen};
hnDraw();
}
function hintsClose(){
if(hnEl.ad&&hnEl.ad.open)hnEl.ad.close();
hnS=null;
hnGen++;
}
function hintsRefresh(){if(hnS)hnDraw();}
function hnDraw(){
const{data}=hnS;
const ctx=hnS.ctx;
const hints=data.hints||[];
hnEl.sec.hidden=hints.length===0;
const score=ctx.score();
const phase=ctx.phase();
const rows=hints.map((h,i)=>{
const li=hnEl.tpl.content.firstElementChild.cloneNode(true);
const k=(name)=>$(`[data-k="${name}"]`,li);
li.dataset.hint=String(h.id);
li.id=`pn-hint-row-${i+1}`;
k('label').textContent=`Hint ${i+1}`;
k('cost').textContent=`· costs ${h.cost} TRP`;
if(h.html||h.content){
const body=k('body');
body.append(h.html?safeHtml(h.html):mk('p',null,h.content));
body.hidden=false;
body.tabIndex=-1;
k('act').hidden=true;
li.classList.add('open');
return li;
}
const btn=k('unlock');
k('which').textContent=` hint ${i+1}`;
const why=k('why');
let reason='';
if(phase==='paused')reason='Hints are paused for a moment. No TRP is spent while paused.';
else if(phase==='ended')reason='The broadcast has ended. Hints can no longer be unlocked.';
else if(score<h.cost)reason=`Not enough TRP yet. Your studio has ${score} TRP; this hint costs ${h.cost}.`;
if(reason){
why.id=`pn-hint-why-${i+1}`;
why.textContent=reason;
why.hidden=false;
btn.setAttribute('aria-disabled','true');
btn.setAttribute('aria-describedby',why.id);
}
btn.addEventListener('click',()=>{if(btn.getAttribute('aria-disabled')!=='true')hnConfirm(h,i,li,btn);});
return li;
});
hnEl.list.replaceChildren(...rows);
}
function hnConfirm(h,i,row,btn){
const score=hnS.ctx.score();
hnAd={hint:h,n:i+1,row,busy:false,focusAfter:null};
hnEl.text.textContent=`Hint ${i+1} costs ${h.cost} TRP. Your studio's TRP drops from ${score} to ${score-h.cost}. This cannot be undone.`;
hnEl.unlock.textContent=`Unlock for ${h.cost} TRP`;
hnEl.err.textContent='';
hnEl.reel.hidden=true;
hnEl.actions.hidden=false;
hnEl.ad.opener=btn;
hnEl.ad.showModal();
hnEl.cancel.focus();
}
function hnPlay(){
if(!hnAd||hnAd.busy)return;
hnEl.actions.hidden=true;
hnEl.err.textContent='';
hnEl.reel.hidden=false;
const still=reduced();
hnAd.frame=1;
hnEl.reel.dataset.frame='1';
hnEl.reel.classList.toggle('still',still);
hnEl.next.hidden=!still;
hnEl.next.textContent='Next';
hnEl.reel.classList.remove('play');
void hnEl.reel.offsetWidth;
if(!still){
hnEl.reel.classList.add('play');
sfx('jingle');
hnAd.timer=setTimeout(hnAdDone,HN_AD_MS);
}
(still?hnEl.next:hnEl.skip).focus();
}
function hnAdStop(){
if(hnAd)clearTimeout(hnAd.timer);
hnEl.reel.classList.remove('play');
}
function hnAdDone(){
if(!hnAd||hnAd.busy)return;
hnAdStop();
hnEl.reel.hidden=true;
hnEl.actions.hidden=false;
hnUnlock();
}
async function hnUnlock(){
if(!hnAd||hnAd.busy||!hnS)return;
const{hint:h,n,row}=hnAd;
const gen=hnS.gen;
hnAd.busy=true;
setPending(hnEl.unlock,'Unlocking…');
hnEl.watch.setAttribute('aria-disabled','true');
hnEl.unlock.focus();
const r=await unlockHint(h.id);
const ok=r.ok||(r.error&&r.error.code==='conflict');
let hr=null;
if(ok)hr=await hint(h.id);
clearPending(hnEl.unlock);
hnEl.watch.removeAttribute('aria-disabled');
if(!hnAd)return;
hnAd.busy=false;
if(!hnS||gen!==hnS.gen){hnEl.ad.close();return;}
if(ok&&hr&&hr.ok){
const at=hnS.data.hints.findIndex((x)=>x.id===h.id);
if(at>=0)hnS.data.hints[at]={...hnS.data.hints[at],...hr.data};
if(r.ok&&r.data&&r.data.l3mon&&Number.isFinite(r.data.l3mon.score))hnS.ctx.setScore(r.data.l3mon.score);
hnDraw();
const body=$(`#pn-hint-row-${n} [data-k="body"]`);
hnAd.focusAfter=body;
hnEl.ad.close();
if(r.ok){sfx('ding');toast(`Hint unlocked. ${h.cost} TRP spent.`);}
return;
}
const e=(hr&&!hr.ok?hr:r).error||{};
let text;
if(e.code==='invalid'&&e.fields&&e.fields.score)text=`NOT ENOUGH TRP. Your studio has ${hnS.ctx.score()} TRP; this hint costs ${h.cost}.`;
else if(e.code==='phase_closed')text=`NOT NOW. ${e.message||'Hints are closed right now.'}`;
else{const rep=replyFor(hr&&!hr.ok?hr:r);text=`${rep.lead} ${rep.text}`.trim();}
hnEl.err.textContent=text;
if(row)hnEl.ad.opener=$('[data-k="unlock"]',row);
}
const IN_POLL_MS=1500;
const IN_GIVE_UP_MS=40000;
const inEl={};
let inS=null;
let inGen=0;
const inShow=(el,on)=>{el.hidden=!on;};
const inLeft=()=>(inS&&inS.view?inS.view.expires_in-(Date.now()-inS.at)/1000:0);
function inDuration(ttlMin){
const s=Math.round(ttlMin*60);
return s<120?`${s} seconds`:`${Math.round(s/60)} minutes`;
}
function initInstance(){
const sec=$('[data-pn-inst]');
if(!sec)return;
Object.assign(inEl,{
sec,status:$('[data-inst-status]',sec),conn:$('[data-inst-conn]',sec),addr:$('[data-inst-addr]',sec),slot:$('[data-inst-open-slot]',sec),
ttlP:$('[data-inst-ttl]',sec),ttl:$('[data-ttl]',sec),warn:$('[data-inst-warn]',sec),others:$('[data-inst-others]',sec),
start:$('[data-inst-start]',sec),renew:$('[data-inst-renew]',sec),stop:$('[data-inst-stop]',sec),retry:$('[data-inst-retry]',sec),
why:$('[data-inst-why]',sec),quota:$('[data-inst-quota]',sec),copy:$('[data-inst-copy]',sec),
});
inEl.start.addEventListener('click',()=>inStart());
inEl.retry.addEventListener('click',()=>(inS&&inS.view&&inS.view.state==='error'?inStart():inFetch()));
inEl.renew.addEventListener('click',inRenew);
inEl.stop.addEventListener('click',()=>inStop(inS&&inS.cid,inEl.stop));
inEl.copy.addEventListener('click',async()=>{
const how=await copyText(inEl.addr.textContent,inEl.addr);
toast(how==='copied'?'Address copied.':'Address selected. Press Ctrl+C to copy.');
});
}
function instOpen(data){
instClose();
if(!inEl.sec)return;
const live=!!(data&&data.l3mon&&data.l3mon.live);
inShow(inEl.sec,live);
if(!live)return;
inS={cid:data.id,name:data.name,phase:data.l3mon.phase.state,view:null,at:0,gen:++inGen,warned5:false,warned1:false};
inEl.sec.dataset.state='loading';
inEl.status.textContent='Checking your instance…';
for(const b of[inEl.start,inEl.renew,inEl.stop,inEl.retry,inEl.conn,inEl.ttlP,inEl.why,inEl.others])inShow(b,false);
inEl.quota.textContent='';
inFetch();
}
function instSetPhase(state){
if(!inS)return;
inS.phase=state;
if(inS.view)inRender(inS.view);else inFetch();
}
function instClose(){
if(!inS)return;
if(inS.stopTick)inS.stopTick();
clearTimeout(inS.poll);
if(inS.wait)inS.wait();
inGen++;
inS=null;
}
async function inFetch(){
if(!inS)return;
const gen=inS.gen;
const r=await instance(inS.cid);
if(!inS||gen!==inS.gen)return;
if(r.ok&&r.data)inRender(r.data);
else inProblem(r);
}
function inProblem(r,{button=inEl.retry}={}){
const rep=replyFor(r);
const code=r.error&&r.error.code;
inEl.status.textContent=`${rep.lead} ${r.error&&r.error.message&&code!=='server_error'?r.error.message:rep.text}`.trim();
if(code!=='phase_closed'&&button)inShow(button,true);
}
function inRender(v){
const prev=inS.view?inS.view.state:null;
inS.view=v;
inS.at=Date.now();
if(inS.stopTick){inS.stopTick();inS.stopTick=null;}
clearTimeout(inS.poll);
const sec=inEl.sec;
sec.dataset.state=v.state;
sec.classList.remove('expiring');
sec.removeAttribute('aria-busy');
for(const b of[inEl.start,inEl.renew,inEl.stop,inEl.retry,inEl.conn,inEl.ttlP,inEl.why,inEl.others])inShow(b,false);
inEl.quota.textContent=`Your studio has ${v.quota.used} of ${v.quota.limit} instances running.`;
const readOnly=inS.phase==='paused'||inS.phase==='ended';
const s=v.state;
if(s==='running'){
inEl.status.textContent='● RUNNING';
const c=v.connection||{};
inEl.addr.textContent=c.kind==='http'?c.url:c.command||`${c.host} ${c.port}`;
inEl.slot.replaceChildren();
if(c.kind==='http'){
const a=mk('a','btn btn-sm btn-ghost','Open');
a.href=c.url;
a.target='_blank';
a.rel='noopener noreferrer';
a.addEventListener('click',(e)=>{e.preventDefault();toast('Prototype: this address is a placeholder.');});
inEl.slot.append(a);
}
inShow(inEl.conn,true);
inShow(inEl.ttlP,true);
inShow(inEl.renew,true);
inShow(inEl.stop,true);
inS.warned5=inLeft()<=300;
inS.warned1=inLeft()<=60;
inS.renewOpen=null;
inTick();
inS.stopTick=ticker(inTick);
if(prev==='starting'){sfx('power');announce(`Your instance is running. It expires in ${fmtTtl(v.expires_in*1000)}.`);}
}else if(s==='starting'||s==='stopping'){
inEl.status.textContent=s==='starting'?'Warming up the set…':'Switching off…';
sec.setAttribute('aria-busy','true');
if(!inS.pollSince)inS.pollSince=Date.now();
if(Date.now()-inS.pollSince>IN_GIVE_UP_MS){
inEl.status.textContent='This is taking longer than usual.';
sec.removeAttribute('aria-busy');
inShow(inEl.retry,true);
inS.pollSince=0;
return;
}
inS.poll=setTimeout(inFetch,IN_POLL_MS);
return;
}else{
if(prev==='stopping'){sfx('off');announce('Your instance is switched off.');}
if(readOnly){
inEl.status.textContent=inS.phase==='ended'?"THAT'S A WRAP. Instances are switched off.":'PAUSED. Instances cannot start while the broadcast is paused.';
}else if(s==='expired'){
inEl.status.textContent='This instance has expired. Start a new one when you need it.';
inShow(inEl.start,true);
}else if(s==='error'){
inEl.status.textContent='That instance did not start. Try again, or report a problem.';
inEl.retry.textContent='Try again';
inShow(inEl.retry,true);
}else{
inEl.status.textContent=`Start your own copy of this programme. It runs for ${inDuration(v.ttl_min)}.`;
inShow(inEl.start,true);
if(v.cooldown_in>0)inWait(v.cooldown_in,'Start opens again in');
}
}
inS.pollSince=0;
}
function inTick(){
if(!inS||!inS.view||inS.view.state!=='running')return;
const left=inLeft();
inEl.ttl.textContent=fmtTtl(left*1000);
const v=inS.view;
const elapsed=(Date.now()-inS.at)/1000;
const renewIn=v.renew_in-elapsed;
const can=renewIn<=0&&v.renewals_left>0&&inS.phase!=='ended';
if(can!==inS.renewOpen){
inS.renewOpen=can;
if(can){inEl.renew.removeAttribute('aria-disabled');inEl.renew.removeAttribute('aria-describedby');inShow(inEl.why,false);}
else{
inEl.renew.setAttribute('aria-disabled','true');
inEl.renew.setAttribute('aria-describedby','pn-inst-why');
const win=v.renew_window>=120?`${Math.round(v.renew_window/60)} minutes remain`:`${v.renew_window} seconds remain`;
inEl.why.textContent=v.renewals_left>0?`Renew opens when less than ${win}.`:'This instance cannot be renewed again.';
inShow(inEl.why,true);
}
}
const low=left<=300;
inEl.sec.classList.toggle('expiring',low);
inShow(inEl.warn,low);
if(low&&!inS.warned5){inS.warned5=true;sfx('ding');announce('Your instance has 5 minutes left. Renew to keep it.');}
if(left<=60&&!inS.warned1){inS.warned1=true;announce('Your instance has 1 minute left.');}
if(left<=0){
if(inS.stopTick){inS.stopTick();inS.stopTick=null;}
announce('Your instance has expired.');
inFetch();
}
}
function inWait(seconds,lead){
if(inS.wait)inS.wait();
inEl.start.setAttribute('aria-disabled','true');
inShow(inEl.why,true);
inS.wait=countdown(seconds,(s)=>{inEl.why.textContent=`${lead} ${s} s.`;},()=>{
if(!inS)return;
inS.wait=null;
inEl.start.removeAttribute('aria-disabled');
inEl.why.textContent='You can start it now.';
});
}
async function inStart(){
if(!inS||inEl.start.getAttribute('aria-disabled')==='true')return;
const gen=inS.gen;
const btn=inS.view&&inS.view.state==='error'?inEl.retry:inEl.start;
setPending(btn,'Starting…');
const r=await startInstance(inS.cid);
clearPending(btn);
if(!inS||gen!==inS.gen)return;
inShow(inEl.others,false);
if(r.ok){inS.pollSince=Date.now();inRender({...inS.view,state:'starting'});return;}
const code=r.error&&r.error.code;
if(code==='quota')return inQuota(r);
if(code==='cooldown'||code==='capacity'){
const s=r.error.retryAfter||(code==='cooldown'?30:20);
inEl.status.textContent=code==='cooldown'?`PLEASE STAND BY. You can start it again in ${s} s.`:`All instance hosts are busy. Try again in ${s} s.`;
return inWait(s,code==='cooldown'?'Start opens again in':'Try again in');
}
if(code==='conflict')return inFetch();
inShow(inEl.start,false);
inProblem(r,{button:btn});
}
function inQuota(r){
const others=(r.error.extra&&r.error.extra.others)||[];
inEl.status.textContent=`Your studio already has ${others.length||'the most'} instances running. Stop one first:`;
const list=inEl.others;
list.replaceChildren();
for(const o of others){
const li=mk('li','inst-other');
const t=mk('span','inst-other-t');
t.append(mk('b',null,o.name),o.expires_in?` · expires in ${fmtTtl(o.expires_in*1000)}`:'');
const b=mk('button','btn btn-sm btn-danger');
b.type='button';
b.append('Stop',mk('span','sr-only',` ${o.name}`));
b.addEventListener('click',()=>inStop(o.challenge_id,b,li,o.name));
li.append(t,b);
list.append(li);
}
inShow(list,others.length>0);
inShow(inEl.start,true);
const limit=inS.view&&inS.view.quota?inS.view.quota.limit:others.length;
inEl.quota.textContent=`Your studio has ${others.length} of ${limit} instances running.`;
}
async function inRenew(){
if(!inS||inEl.renew.getAttribute('aria-disabled')==='true')return;
const gen=inS.gen;
setPending(inEl.renew,'Renewing…');
const r=await renewInstance(inS.cid);
clearPending(inEl.renew);
if(!inS||gen!==inS.gen)return;
if(r.ok){
announce(`Renewed. Your instance runs for another ${inDuration(r.data.expires_in/60)}.`);
inFetch();
return;
}
const msg=r.error&&r.error.message;
inEl.why.textContent=msg||replyFor(r).text;
inShow(inEl.why,true);
}
async function inStop(cid,btn,li,name){
if(!inS||!cid)return;
const gen=inS.gen;
setPending(btn,'Stopping…');
const r=await stopInstance(cid);
clearPending(btn);
if(!inS||gen!==inS.gen)return;
if(li){
if(r.ok||(r.error&&r.error.code==='conflict')){
li.remove();
announce(`${name} is switching off. Start this one when it is off.`);
if(!inEl.others.children.length)inShow(inEl.others,false);
inS.poll=setTimeout(inFetch,1800);
}else inProblem(r,{button:inEl.start});
return;
}
if(r.ok){inS.pollSince=Date.now();inRender({...inS.view,state:'stopping'});}
else if(r.error&&r.error.code==='conflict')inFetch();
else inProblem(r,{button:inEl.stop});
}
const PN_PATH=/^\/board\/([a-z0-9_]{3,41})$/;
const PN_CATS={crypto:['Crypto','i-crypto'],misc:['Misc','i-misc'],web:['Web','i-web'],forensics:['Forensics','i-forensics'],osint:['OSINT','i-osint'],hardware:['Hardware','i-hardware'],rf:['RF','i-rf'],reverse:['Reverse','i-reverse'],web3:['Web3','i-web3'],ai:['AI','i-ai'],pwn:['Pwn','i-pwn']};
const PN_DIFF={warmup:[1,'Warm-up'],easy:[2,'Easy'],medium:[3,'Medium'],hard:[4,'Hard'],insane:[5,'Insane']};
const PN_EVENT=' | L3m0nCTF 2026';
const pnEl={};
let pnS=null;
const pnK=(name)=>$(`[data-pn="${name}"]`,pnEl.dlg);
const pnFmtSize=(n)=>(n<1024?`${n} B`:n<1048576?`${(n/1024).toFixed(1)} KB`:`${(n/1048576).toFixed(1)} MB`);
function initPanel({onSolved}={}){
const dlg=$('[data-panel]');
if(!dlg||typeof dlg.showModal!=='function'||pnS)return;
pnEl.dlg=dlg;
pnEl.body=$('[data-pn-body]',dlg);
pnEl.load=$('[data-pn-load]',dlg);
pnEl.loadT=$('[data-pn-load-t]',dlg);
pnEl.files=$('[data-pn-files]',dlg);
pnEl.fileList=$('[data-pn-file-list]',dlg);
pnEl.fileTpl=$('#tpl-file');
pnEl.task=$('[data-pn-task]',dlg);
pnEl.solved=$('[data-pn-solved]',dlg);
pnEl.diff=$('[data-pn-diff]',dlg);
pnEl.head=$('.panel-head',dlg);
if('ResizeObserver'in window)new ResizeObserver(()=>{dlg.style.scrollPaddingTop=`${getComputedStyle(pnEl.head).position==='sticky'?pnEl.head.offsetHeight+8:64}px`;}).observe(pnEl.head);
pnS={
onSolved:typeof onSolved==='function'?onSolved:()=>{},slug:null,data:null,opener:null,pushed:false,
fromHistory:false,closing:false,gen:0,rendered:-1,title:'',ch:null,score:0,cache:new Map(),first:true,
};
$('[data-pn-close]',dlg).addEventListener('click',()=>pnClose());
$('[data-pn-retry]',dlg).addEventListener('click',()=>{if(pnS.slug){pnReset();pnLoad(pnS.slug);}});
$('[data-pn-report]',dlg).addEventListener('click',(e)=>reportDialog(e.currentTarget));
dlg.addEventListener('cancel',(e)=>{e.preventDefault();pnClose();});
dlg.addEventListener('close',pnAfterClose);
dlg.addEventListener('click',(e)=>{if(e.target===dlg)pnClose();});
dlg.addEventListener('keydown',(e)=>{
if(e.key!=='f'||e.ctrlKey||e.altKey||e.metaKey||e.repeat||typing(e.target)||!keysOn())return;
e.preventDefault();
flagFocus();
});
initFlag({
onCorrect:pnSolved,report:(b)=>reportDialog(b),
onAlready:()=>{if(pnS.data&&!pnS.data.solved_by_me)pnSolved({value:0,solves:pnS.data.solves});},
onPhase:(state)=>{if(pnS.data)pnS.data.l3mon.phase.state=state;hintsRefresh();instSetPhase(state);},
});
initHints({score:()=>pnS.score,setScore:pnSetScore,phase:()=>(pnS.data?pnS.data.l3mon.phase.state:'live')});
initInstance();
bindKey('f','Put the cursor in the flag box (while a programme is open)',()=>flagFocus());
addEventListener('popstate',pnPop);
pnInitPrefetch();
const m=PN_PATH.exec(location.pathname);
if(m)openPanel(m[1]);
}
function openPanel(slug,{opener=null,push=false}={}){
if(!pnS||typeof slug!=='string'||!/^[a-z0-9_]{3,41}$/.test(slug))return false;
const dlg=pnEl.dlg;
if(pnS.closing){pnS.closing=false;dlg.classList.remove('closing');}
if(dlg.open&&pnS.slug===slug){pnFocusTitle();return true;}
if(!dlg.open){pnS.opener=opener;pnS.title=document.title;}
else if(opener)pnS.opener=opener;
if(push&&location.pathname!==`/board/${slug}`){history.pushState({panel:slug},'',`/board/${slug}`);pnS.pushed=true;}
pnS.slug=slug;
pnS.gen++;
pnReset();
if(!dlg.open){
dlg.classList.toggle('first',pnS.first);
pnS.first=false;
dlg.showModal();
}
pnFocusTitle();
pnLoad(slug);
return true;
}
function closePanel(){pnClose();}
function pnClose(){
const dlg=pnEl.dlg;
if(!dlg||!dlg.open||pnS.closing)return;
pnS.closing=true;
if(reduced()||document.documentElement.classList.contains('rm')){dlg.close();return;}
dlg.classList.add('closing');
let t=0;
const done=()=>{clearTimeout(t);dlg.removeEventListener('animationend',onEnd);if(pnS.closing&&dlg.open)dlg.close();};
const onEnd=(e)=>{if(e.target===dlg)done();};
dlg.addEventListener('animationend',onEnd);
t=setTimeout(done,320);
}
function pnAfterClose(){
const dlg=pnEl.dlg;
dlg.classList.remove('closing','loading');
pnS.closing=false;
flagClose();
hintsClose();
instClose();
pnS.gen++;
pnS.slug=null;
if(pnS.title){
const nm=pnS.data&&pnS.data.name;
document.title=nm&&pnS.title.startsWith(`${nm} | `)?pnS.title.slice(nm.length+3):pnS.title;
}
if(!pnS.fromHistory&&PN_PATH.test(location.pathname)){
if(pnS.pushed)history.back();
else history.replaceState(null,'',pnS.ch===null?'/board':`/board?ch=${pnS.ch}`);
}
pnS.pushed=false;
pnS.fromHistory=false;
const o=pnS.opener;
pnS.opener=null;
if(o&&o.isConnected&&!o.closest('[inert],[hidden]')){o.focus({preventScroll:true});return;}
const h1=document.getElementById('board-h')||$('main h1')||$('h1');
if(h1){
if(!h1.hasAttribute('tabindex'))h1.setAttribute('tabindex','-1');
h1.focus({preventScroll:true});
}
}
function pnPop(){
const m=PN_PATH.exec(location.pathname);
if(m){
openPanel(m[1]);
pnS.pushed=!!(history.state&&history.state.panel===m[1]);
}else if(pnEl.dlg.open){
pnS.fromHistory=true;
pnS.pushed=false;
pnClose();
}
}
function pnFocusTitle(){
const h=$('#pn-title',pnEl.dlg);
h.focus({preventScroll:true});
pnEl.body.scrollTop=0;
pnEl.dlg.scrollTop=0;
}
function pnReset(){
const dlg=pnEl.dlg;
dlg.classList.add('loading');
pnEl.body.setAttribute('aria-busy','true');
pnK('name').textContent='Tuning in…';
for(const k of['channel','category','number','solves','value','author','difficulty','story'])pnK(k).replaceChildren();
pnK('fb').hidden=true;
pnK('sponsor').hidden=true;
pnK('decay').hidden=true;
pnK('decay').textContent='';
pnEl.solved.hidden=true;
pnEl.task.replaceChildren();
pnEl.load.hidden=true;
pnEl.files.hidden=true;
for(const s of dlg.querySelectorAll('[data-pn-sec], [data-pn-flagsec]'))s.hidden=false;
pnS.rendered=-1;
pnS.data=null;
flagClose();
hintsClose();
instClose();
$('[data-pn-hints]',dlg).hidden=true;
$('[data-pn-inst]',dlg).hidden=true;
}
async function pnLoad(slug){
const gen=pnS.gen;
const cached=pnS.cache.get(slug);
if(cached)pnRender(cached);
const r=await challenge(slug,cached?cached.etag:undefined);
if(gen!==pnS.gen)return;
if(r.notModified&&cached){cached.seen=Date.now();return;}
if(r.ok&&r.data&&typeof r.data==='object'){
const entry={data:r.data,etag:r.etag||null,at:Date.now()};
pnS.cache.set(slug,entry);
pnRender(entry);
return;
}
if(!cached)pnFail(r);
}
function pnFail(r){
const rep=replyFor(r);
const nf=r.error&&r.error.code==='not_found';
pnEl.dlg.classList.remove('loading');
pnEl.body.removeAttribute('aria-busy');
pnK('name').textContent=nf?'No signal':'Could not tune in';
pnEl.loadT.textContent=`${rep.lead} ${rep.text}`.trim();
pnEl.load.hidden=false;
$('[data-pn-retry]',pnEl.dlg).hidden=nf;
for(const s of pnEl.dlg.querySelectorAll('[data-pn-sec], [data-pn-flagsec]'))s.hidden=true;
}
function pnRender(entry){
const d=entry.data;
const l3=d.l3mon;
const dlg=pnEl.dlg;
const firstRender=pnS.rendered!==pnS.gen;
pnS.rendered=pnS.gen;
pnS.data=d;
pnS.entry=entry;
pnS.ch=l3.channel;
pnS.score=l3.score;
dlg.classList.remove('loading');
pnEl.body.removeAttribute('aria-busy');
for(let i=0;i<8;i++)dlg.classList.toggle(`pn-k${i}`,i===l3.channel);
pnK('channel').textContent=`CH ${l3.channel} · ${l3.channel_name}`;
pnK('sponsor').hidden=!l3.sponsor;
pnK('sponsor').textContent=l3.sponsor?`Sponsored by ${l3.sponsor.name}`:'';
const cat=PN_CATS[d.category]||[d.category,'i-misc'];
pnK('category').replaceChildren(icon(cat[1]),cat[0]);
pnK('number').textContent=`P${String(l3.number).padStart(3,'0')}`;
pnK('name').textContent=d.name;
pnK('value').textContent=String(d.value);
pnK('author').textContent=l3.author;
const diff=PN_DIFF[l3.difficulty]||[3,l3.difficulty];
pnEl.diff.className=`dmeter d-${diff[0]}`;
pnK('difficulty').textContent=diff[1];
pnK('decay').hidden=!l3.decay;
pnK('decay').textContent=l3.decay?valueNote(d.value,l3.start,l3.floor):'';
pnShowSolved(d);
pnK('story').textContent=l3.story;
pnEl.task.replaceChildren(safeHtml(d.description_html));
pnFiles(entry);
document.title=`${d.name} | ${l3.channel_name} | Channels${PN_EVENT}`;
if(firstRender){
flagOpen(d);
instOpen(d);
}
const ad=$('[data-pn-ad]');
if(firstRender||!(ad&&ad.open))hintsOpen(d);
}
function pnShowSolved(d){
pnK('fb').hidden=!d.l3mon.first_blood_open;
const s=pnK('solves');
s.hidden=d.l3mon.first_blood_open;
s.textContent=`${d.solves} ${d.solves===1?'solve':'solves'}`;
pnEl.solved.hidden=!d.solved_by_me;
pnEl.dlg.classList.toggle('is-solved',!!d.solved_by_me);
}
function pnFiles(entry){
const files=entry.data.l3mon.files||[];
pnEl.files.hidden=files.length===0;
pnEl.fileList.replaceChildren(...files.map((f)=>{
const li=pnEl.fileTpl.content.firstElementChild.cloneNode(true);
const k=(n)=>$(`[data-k="${n}"]`,li);
k('name').textContent=f.name;
k('size').textContent=pnFmtSize(f.size);
k('sha').textContent=`${f.sha256.slice(0,6)}…${f.sha256.slice(-4)}`;
k('sha').title=f.sha256;
k('which').textContent=` ${f.name}`;
const a=k('link');
a.href=f.url;
a.setAttribute('download',f.name);
a.addEventListener('click',(e)=>pnDownload(e,entry,f.name,a));
return li;
}));
}
async function pnDownload(e,entry,name,a){
const f=entry.data.l3mon.files.find((x)=>x.name===name);
if(!f||a.dataset.retried||Date.now()<entry.at+(f.expires_in-20)*1000)return;
e.preventDefault();
const slug=pnS.slug;
const r=await challenge(slug);
if(!r.ok||slug!==pnS.slug)return;
const fresh={data:r.data,etag:r.etag||null,at:Date.now()};
pnS.cache.set(slug,fresh);
pnFiles(fresh);
const link=[...pnEl.fileList.querySelectorAll('[data-k="link"]')].find((x)=>x.getAttribute('download')===name);
if(link){link.dataset.retried='1';link.click();}
}
function pnSolved(l3){
const d=pnS.data;
if(!d)return;
d.solved_by_me=true;
if(Number.isFinite(l3.solves))d.solves=l3.solves;
d.l3mon.first_blood_open=false;
if(Number.isFinite(l3.value))pnS.score+=l3.value;
d.l3mon.score=pnS.score;
if(pnS.entry)pnS.entry.etag=null;
pnShowSolved(d);
hintsRefresh();
try{pnS.onSolved(d.l3mon.slug,{solves:d.solves});}catch(err){console.error('[l3m0n] onSolved failed',err);}
}
function pnSetScore(n){
pnS.score=n;
if(pnS.data)pnS.data.l3mon.score=n;
if(pnS.entry)pnS.entry.etag=null;
}
function pnInitPrefetch(){
let t=0;
const wanted=(e)=>{
const a=e.target&&e.target.closest?e.target.closest('a[href^="/board/"]'):null;
const m=a?PN_PATH.exec(a.getAttribute('href')):null;
return m&&!pnEl.dlg.open?m[1]:null;
};
const go=(e)=>{
const slug=wanted(e);
clearTimeout(t);
if(!slug)return;
const c=pnS.cache.get(slug);
if(c&&Date.now()-(c.seen||c.at)<60e3)return;
t=setTimeout(async()=>{
if(pnEl.dlg.open)return;
const r=await challenge(slug,c?c.etag:undefined);
if(r.ok&&r.data&&typeof r.data==='object')pnS.cache.set(slug,{data:r.data,etag:r.etag||null,at:Date.now()});
else if(r.notModified&&c)c.seen=Date.now();
},150);
};
document.addEventListener('pointerover',go,{passive:true});
document.addEventListener('focusin',go);
document.addEventListener('pointerout',(e)=>{if(wanted(e))clearTimeout(t);},{passive:true});
}
const BD_POLL_MS=15000;
const BD_IDLE_MS=180000;
const BD_IDLE_POLL_MS=60000;
const BD_OFFLINE_MS=20000;
const BD_TUNE_DEBOUNCE_MS=120;
const BD_BANNERS={
ended:['banner-wrap','wrap',"THAT'S A WRAP.",'The broadcast has ended. You can still read every programme.'],
paused:['banner-warn','paused','PAUSED.','Submissions are paused for a moment. Your progress is safe.'],
frozen:['banner-info','frozen','SCOREBOARD FROZEN.','Standings are frozen for the final hour. Your solves still count.'],
};
const bdPhaseKey=(ph)=>(!ph?'':ph.state==='live'?(ph.frozen?'frozen':''):ph.state==='before'?'':ph.state);
const bdMeter=(frac)=>{const on=Math.round(frac*6);return'▰'.repeat(on)+'▱'.repeat(6-on);};
let bdSession=null;
const bdSess=()=>{
if(!bdSession){let s=null;try{s=window.sessionStorage;}catch{}bdSession=makeStore(s,'l3m0n.v1.');}
return bdSession;
};
const bd={
root:null,panel:null,ul:null,tpl:null,tabs:[],flt:null,
data:null,etag:null,ver:null,notifVer:null,notifId:0,
byCh:new Map(),bySlug:new Map(),tiles:new Map(),cells:[],slots:0,chans:[],
ch:0,view:'picture',eff:'picture',narrow:null,rove:null,first:false,loaded:false,panelReady:false,
own:undefined,tuneTimer:0,sayTimer:0,pollTimer:0,lastPoll:0,lastInput:Date.now(),lockTimers:new Map(),
};
const bdChan=(id)=>bd.chans.find((c)=>c.id===id)||null;
const bdList=()=>bd.byCh.get(bd.ch)||[];
const bdStats=(id)=>{
const list=bd.byCh.get(id)||[];
const c=bdChan(id);
const total=c&&Number.isFinite(c.total)?Math.max(c.total,list.length):list.length;
const solved=list.filter((p)=>p.solved_by_me).length;
return{total,shown:list.length,coming:total-list.length,solved,frac:total?solved/total:0};
};
const bdSigText=(pct,s)=>`${pct}% · ${s.solved} of ${s.total} clear`;
const bdCounts=new WeakMap();
function bdCount(el,from,to,fmt=String){
if(!el)return;
const token={};
bdCounts.set(el,token);
const done=()=>{if(bdCounts.get(el)===token&&!token.done){token.done=true;el.textContent=fmt(to);}};
if(reduced()||from===to||document.hidden){done();return;}
const t0=performance.now();
const step=(t)=>{
if(bdCounts.get(el)!==token||token.done)return;
const k=Math.min(1,(t-t0)/600);
if(k>=1){done();return;}
el.textContent=fmt(Math.round(from+(to-from)*(1-(1-k)**3)));
requestAnimationFrame(step);
};
requestAnimationFrame(step);
setTimeout(done,650);
}
function bdReplay(el,cls,ms){
if(!el||reduced())return;
el.classList.remove(cls);
void el.offsetWidth;
el.classList.add(cls);
clearTimeout(bd.lockTimers.get(el));
bd.lockTimers.set(el,setTimeout(()=>{el.classList.remove(cls);bd.lockTimers.delete(el);},ms));
}
function bdChrome(animate=false){
const c=bdChan(bd.ch);
if(!c)return;
bd.root.className=bd.root.className.replace(/\bch-\d+\b/,`ch-${bd.ch}`);
bd.root.dataset.ch=String(bd.ch);
$('[data-tuner-no]',bd.root).textContent=`CH ${String(bd.ch).padStart(2,'0')}`;
$('[data-tuner-name]',bd.root).textContent=c.name;
const story=$('[data-story]',bd.root);
if(story&&bd.data){
if(bd.storyCh!==bd.ch||story.textContent!==(c.synopsis||'')){story.textContent=c.synopsis||'';bd.storyCh=bd.ch;bdReplay(story,'in',700);}
story.classList.remove('wait');
}else if(story)story.classList.toggle('wait',bd.ch!==bd.storyCh);
const ad=$('[data-ad]',bd.root);
if(ad)ad.textContent=c.sponsor?`Sponsored by ${c.sponsor}. These programmes count toward your TRP like any other.`:'';
const sig=$('[data-sig]',bd.root);
if(bd.data){
const s=bdStats(bd.ch);
const pct=Math.round(s.frac*100);
$('[data-meter]',bd.root).textContent=bdMeter(s.frac);
const was=Number((/^(\d+)%/.exec(sig.textContent)||[])[1]);
if(animate&&Number.isFinite(was))bdCount(sig,was,pct,(v)=>bdSigText(v,s));else sig.textContent=bdSigText(pct,s);
}else{
const tab=bd.tabs.find((t)=>Number(t.dataset.ch)===bd.ch);
const pct=tab?Number($('[data-pct]',tab).textContent):0;
$('[data-meter]',bd.root).textContent=bdMeter(pct/100);
sig.textContent=`${pct}%`;
}
for(const t of bd.tabs){
const on=Number(t.dataset.ch)===bd.ch;
t.setAttribute('aria-selected',String(on));
t.tabIndex=on?0:-1;
}
bd.panel.setAttribute('aria-labelledby',`tab-${bd.ch}`);
const osd=$('[data-osd-id]');
if(osd)osd.textContent=`CHANNELS · CH ${String(bd.ch).padStart(2,'0')}`;
if(location.pathname==='/board'){
const t=document.title.split(' | ');
document.title=[c.name,...t.slice(t.indexOf('Channels'))].join(' | ');
try{history.replaceState(history.state,'',`/board?ch=${bd.ch}`);}catch{}
}
bdSess().set('ch',String(bd.ch));
}
function bdStrip(){
for(const t of bd.tabs){
const s=bdStats(Number(t.dataset.ch));
t.className=t.className.replace(/\bsig-\d+\b/,`sig-${Math.round(s.frac*10)}`);
const pct=$('[data-pct]',t);
if(pct)pct.textContent=String(Math.round(s.frac*100));
}
}
function bdTune(ch,{via='strip'}={}){
if(!bdChan(ch)||ch===bd.ch)return;
bd.ch=ch;
bd.rove=null;
bdChrome();
sfx('static');
clearTimeout(bd.tuneTimer);
const show=()=>{
if(bd.loaded){bdRender();bdMotion();}
const c=bdChan(ch);
const s=bdStats(ch);
const n=bdList().filter((p)=>fltMatch(p,bd.flt.state)).length;
announce(`Channel ${ch}, ${c.name}, signal ${Math.round(s.frac*100)} percent. ${fltActive(bd.flt.state)?fltSay(n,s.shown):`${fltCountText(s.shown,s.shown,false,s.coming)}.`}`);
};
if(via==='key')bd.tuneTimer=setTimeout(show,BD_TUNE_DEBOUNCE_MS);else show();
}
const bdStep=(dir,via)=>{
const ids=bd.chans.map((c)=>c.id);
const i=ids.indexOf(bd.ch);
bdTune(ids[(i+dir+ids.length)%ids.length],{via});
};
const bdCell=(p,k)=>(Number.isInteger(p.cell)?p.cell:k);
function bdRender(){
const list=bdList();
const slots=Math.max(bdStats(bd.ch).total,list.reduce((m,p,k)=>Math.max(m,bdCell(p,k)+1),0));
const rows=Math.max(1,Math.ceil(slots/TILE_COLS));
bd.ul.className=`tiles cols-${TILE_COLS} rows-${Math.min(rows,12)}`;
if(rows>12)bd.ul.style.setProperty('--rows',String(rows));else bd.ul.style.removeProperty('--rows');
const frag=document.createDocumentFragment();
bd.tiles=new Map();
bd.slots=slots;
bd.cells=[];
const at=new Map(list.map((p,k)=>[bdCell(p,k),p]));
for(let i=0;i<rows*TILE_COLS;i++){
const p=at.get(i);
let li;
if(p){
li=tileMake(bd.tpl,p,i);
li.style.setProperty('--i',String(Math.min(i,10)));
bd.tiles.set(p.id,li);
}else li=i<slots?tileSoon(i):tileFiller(i);
bd.cells.push(p?li:null);
frag.append(li);
}
bd.ul.replaceChildren(frag);
bd.ul.removeAttribute('aria-hidden');
if(!bd.tiles.has(bd.rove))bd.rove=list.length?list[0].id:null;
bdFilter(false);
$('[data-empty-ch]',bd.root).hidden=list.length>0;
}
function bdMotion(){
if(reduced())return;
bdReplay(bd.panel,'static',450);
bdReplay(bd.ul,'settle',800);
}
function bdRove(id){
bd.rove=id;
for(const[tid,li]of bd.tiles)li.firstElementChild.tabIndex=tid===id?0:-1;
}
function bdFilter(say){
const list=bdList();
const f=bd.flt.state;
const active=fltActive(f);
let n=0;
let firstOk=null;
for(const p of list){
const li=bd.tiles.get(p.id);
if(!li)continue;
const ok=fltMatch(p,f);
if(ok){n++;if(firstOk===null)firstOk=p.id;}
li.dataset.match=ok?'yes':'no';
li.inert=!ok;
}
const roveTile=bd.tiles.get(bd.rove);
bdRove(roveTile&&roveTile.dataset.match==='yes'?bd.rove:firstOk??bd.rove);
$('[data-count]',bd.root).textContent=fltCountText(n,list.length,active,bdStats(bd.ch).coming);
const meter=$('[data-flt-meter]',bd.root);
if(meter)meter.textContent=active&&list.length?fltMeter(n,list.length):'';
const perTrack=new Map();
for(const p of list)perTrack.set(p.category,(perTrack.get(p.category)||0)+1);
for(const b of $$('[data-cat]',bd.root)){
const k=perTrack.get(b.dataset.cat)||0;
$('[data-n]',b).textContent=String(k);
b.classList.toggle('zero',k===0);
b.setAttribute('aria-label',`${$('[data-l]',b).textContent}, ${k} on this channel`);
}
for(const b of $$('[data-clear]',bd.root))if(b.closest('[data-filters]'))b.hidden=!active;
$('[data-empty-filter]',bd.root).hidden=!(active&&n===0&&list.length>0);
clearTimeout(bd.sayTimer);
if(say)bd.sayTimer=setTimeout(()=>announce(fltSay(n,list.length)),500);
}
function bdApplyView(){
bd.eff=bd.narrow?'cards':bd.view;
bd.panel.dataset.view=bd.eff;
bd.flt.setView(bd.view);
}
function bdSetView(v){
if(v!=='picture'&&v!=='cards')return;
bd.view=v;
store.set('view',v);
bdApplyView();
}
function bdIndex(data){
bd.data=data;
bd.chans=data.channels.map((c)=>({id:c.id,name:c.name,synopsis:c.synopsis||'',total:c.total,onAir:c.on_air,coming:c.coming,sponsor:c.sponsor?c.sponsor.name:''}));
bd.byCh=new Map(bd.chans.map((c)=>[c.id,[]]));
bd.bySlug=new Map();
for(const p of data.programmes){
if(!bd.byCh.has(p.channel))bd.byCh.set(p.channel,[]);
bd.byCh.get(p.channel).push(p);
bd.bySlug.set(p.slug,p);
}
for(const list of bd.byCh.values())list.sort((a,b)=>a.order-b.order||a.id-b.id);
bd.ver=data.ver;
if(Number.isFinite(data.notif_ver))bd.notifVer=data.notif_ver;
bd.notifId=Math.max(bd.notifId,Number(data.notif_id)||0);
}
function bdBanner(ph){
if(ph&&ph.state==='before'){location.reload();return;}
const slot=$('[data-banner-slot]');
if(!slot)return;
const key=bdPhaseKey(ph);
const now=$('[data-banner]',slot);
if((now?now.dataset.banner:'')===(key?BD_BANNERS[key][1]:''))return;
if(!key){slot.replaceChildren();announce('The broadcast is live.');return;}
const[cls,id,lead,text]=BD_BANNERS[key];
const p=mk('p',`banner ${cls}`);
p.dataset.banner=id;
p.append(mk('b','banner-lead',lead),` ${text}`);
slot.replaceChildren(p);
announce(`${lead} ${text}`);
}
function bdApply(data,etag){
const firstTime=!bd.loaded;
bdIndex(data);
bd.etag=etag||null;
bdBanner(data.phase);
if(!bdChan(bd.ch)&&bd.chans.length)bd.ch=bd.chans[0].id;
bdStrip();
bdChrome(!firstTime);
bd.panel.removeAttribute('aria-busy');
$('[data-load-error]',bd.root).hidden=true;
if(firstTime){
bd.loaded=true;
bdRender();
if(bd.first)bdMotion();
const slug=bd.root.dataset.slug;
if(slug&&bd.bySlug.has(slug))bdRove(bd.bySlug.get(slug).id);
return;
}
const list=bdList();
const same=list.length===bd.tiles.size&&list.every((p)=>bd.tiles.has(p.id))&&bd.slots===bdStats(bd.ch).total;
if(!same){bdRender();return;}
let fresh=0;
list.forEach((p,k)=>{
const li=bd.tiles.get(p.id);
const was=li.dataset.state;
tilePatch(li,p,bdCell(p,k));
if(was==='open'&&p.solved_by_me){fresh++;bdReplay(li,'locking',1900);}
});
bdFilter(false);
if(fresh)bdComplete(bd.ch);
}
function bdComplete(ch){
const s=bdStats(ch);
if(!s.total||s.solved!==s.total)return;
if(ch===bd.ch)bdReplay($('[data-tuner]',bd.root),'sweep',1300);
announce(`Channel ${ch} is fully tuned in: signal 100 percent.`);
}
async function bdLoad(tickVer){
const r=await board(bd.etag);
if(r.ok&&r.notModified){if(tickVer!==undefined)bd.ver=tickVer;return;}
if(r.ok&&r.data&&Array.isArray(r.data.programmes)){bdApply(r.data,r.etag);bdPanelOnce();return;}
if(!bd.loaded){
const words=replyFor(r);
$('[data-load-text]',bd.root).textContent=`${words.lead} ${words.text}`.trim();
$('[data-load-error]',bd.root).hidden=false;
bd.panel.removeAttribute('aria-busy');
}
bdPanelOnce();
}
function bdPanelOnce(){
if(bd.panelReady)return;
bd.panelReady=true;
initPanel({onSolved:bdSolved});
}
function bdSolved(slug,res={}){
const p=bd.bySlug.get(slug);
if(!p||p.solved_by_me)return;
p.solved_by_me=true;
p.first_blood_open=false;
if(Number.isFinite(res.solves))p.solves=res.solves;
const li=p.channel===bd.ch?bd.tiles.get(p.id):null;
if(li){
tilePatch(li,p,bdCell(p,bdList().indexOf(p)));
bdReplay(li,'locking',1900);
bdFilter(false);
}
bdStrip();
if(p.channel===bd.ch)bdChrome(true);
bdComplete(p.channel);
}
function bdSchedule(ms){
clearTimeout(bd.pollTimer);
if(document.hidden)return;
const idle=Date.now()-bd.lastInput>BD_IDLE_MS;
bd.pollTimer=setTimeout(bdTick,ms??(idle?BD_IDLE_POLL_MS:BD_POLL_MS+(Math.random()*6000-3000)));
}
async function bdTick(){
if(document.hidden)return;
bd.lastPoll=Date.now();
const r=await ticks();
if(!r.ok){
const code=r.error&&r.error.code;
if(code==='auth_required')return;
if(code==='offline'||code==='timeout'){
pillShow('bad','OFFLINE · checking again in 20 s');
bdSchedule(BD_OFFLINE_MS);
return;
}
bdSchedule(Math.max(((r.error&&r.error.retryAfter)||0)*1000,BD_POLL_MS));
return;
}
const d=r.data||{};
const news=bd.notifVer!==null&&d.notif_ver!==bd.notifVer;
const since=bd.notifId;
const moved=d.ver!==bd.ver||(bd.own!==undefined&&d.own!==bd.own);
if(bd.own===undefined)bd.own=d.own;
if(bd.loaded&&moved){await bdLoad(d.ver);bd.own=d.own;}
if(news)await bdNotify(since);
bd.notifVer=d.notif_ver;
bdSchedule();
}
async function bdNotify(since=bd.notifId){
const r=await notifications(since);
if(!r.ok||!Array.isArray(r.data)||!r.data.length)return;
for(const n of r.data)bd.notifId=Math.max(bd.notifId,Number(n.id)||0);
const last=r.data[r.data.length-1];
const line=`${String(last.title||'News').toUpperCase()}. ${last.content||''}`.trim();
toast(line);
sfx('ding');
for(const list of $$('[data-crawl] .crawl-list'))list.prepend(mk('li',null,line));
}
function bdPolling(){
document.addEventListener('visibilitychange',()=>{
if(document.hidden){clearTimeout(bd.pollTimer);return;}
const since=Date.now()-bd.lastPoll;
bdSchedule(since>=BD_POLL_MS?0:BD_POLL_MS-since);
});
const touch=()=>{
const wasIdle=Date.now()-bd.lastInput>BD_IDLE_MS;
bd.lastInput=Date.now();
if(wasIdle)bdSchedule(1000);
};
for(const t of['pointerdown','keydown'])addEventListener(t,touch,{passive:true,capture:true});
addEventListener('online',()=>bdSchedule(500));
bd.lastPoll=Date.now();
bdSchedule();
}
function bdBindStrip(){
const strip=$('[data-strip]',bd.root);
strip.addEventListener('click',(e)=>{
const t=e.target.closest('[role="tab"]');
if(t)bdTune(Number(t.dataset.ch),{via:'strip'});
});
strip.addEventListener('keydown',(e)=>{
const t=e.target.closest('[role="tab"]');
if(!t||e.altKey||e.ctrlKey||e.metaKey)return;
const i=bd.tabs.indexOf(t);
const n=bd.tabs.length;
const to={ArrowRight:(i+1)%n,ArrowLeft:(i-1+n)%n,Home:0,End:n-1}[e.key];
if(to===undefined)return;
e.preventDefault();
const next=bd.tabs[to];
next.focus();
bdTune(Number(next.dataset.ch),{via:'key'});
});
for(const b of $$('[data-step]',bd.root)){
b.addEventListener('click',()=>{
const dir=Number(b.dataset.step);
bdStep(dir,'osd');
});
}
}
function bdBindTiles(){
bd.ul.addEventListener('click',(e)=>{
const a=e.target.closest('.tile-a');
if(!a||e.defaultPrevented||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
e.preventDefault();
openPanel(a.dataset.slug,{opener:a,push:true});
});
bd.ul.addEventListener('focusin',(e)=>{
const li=e.target.closest('.tile');
if(li&&li.dataset.id)bdRove(Number(li.dataset.id));
});
bd.ul.addEventListener('keydown',(e)=>{
const a=e.target.closest('.tile-a');
if(!a||e.altKey||e.metaKey)return;
if(e.key===' '){e.preventDefault();a.click();return;}
if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(e.key))return;
e.preventDefault();
const items=bd.eff==='picture'?bd.cells:bdList().map((p)=>bd.tiles.get(p.id)).filter((li)=>li&&li.dataset.match!=='no');
const cols=getComputedStyle(bd.ul).gridTemplateColumns.split(' ').filter(Boolean).length||TILE_COLS;
const pos=items.indexOf(a.parentElement);
const next=tileGridMove({count:items.length,cols,pos,key:e.key,ctrl:e.ctrlKey,ok:(i)=>!!items[i]&&items[i].dataset.match!=='no'});
if(next!==pos&&items[next])items[next].firstElementChild.focus();
});
}
function bdBindKeys(){
bindKey(['[',']'],'Previous or next channel',(e)=>bdStep(e.key===']'?1:-1,'key'));
bindKey(['0','1','2','3','4','5','6','7'],'Jump to channel 0 to 7',(e)=>bdTune(Number(e.key),{via:'jump'}));
bindKey('/','Search programmes',()=>bd.flt.focusSearch());
bindKey('v','Picture or Cards view',()=>{if(!bd.narrow){bdSetView(bd.view==='picture'?'cards':'picture');announce(`${bd.view==='picture'?'Picture':'Cards'} view`);}});
bindKey('u','Only programmes not solved yet, or all',()=>bd.flt.toggleOpen());
}
function initBoard(){
const root=$('[data-board]');
if(!root)return;
bd.root=root;
bd.panel=$('[data-tilepanel]',root);
bd.ul=$('[data-tiles]',root);
bd.tpl=$('#tpl-tile');
bd.tabs=$$('[role="tab"]',root);
bd.chans=bd.tabs.map((t)=>({id:Number(t.dataset.ch),name:$('.strip-name',t).textContent,sponsor:t.dataset.sponsor||''}));
bd.storyCh=Number(root.dataset.ch);
bd.ch=Number(root.dataset.ch);
bd.first=bdSess().get('ch',null)===null;
if(!root.dataset.slug&&!new URLSearchParams(location.search).has('ch')){
const saved=Number(bdSess().get('ch',''));
if(Number.isInteger(saved)&&saved!==bd.ch&&bdChan(saved))bd.ch=saved;
}
bd.flt=initFilters($('[data-filters]',root),{onChange:()=>bdFilter(true),onView:bdSetView});
for(const b of $$('[data-clear]',root))b.addEventListener('click',()=>{bd.flt.clear();bd.flt.focusToggle();});
$('[data-reload]',root).addEventListener('click',()=>{$('[data-load-error]',root).hidden=true;bd.panel.setAttribute('aria-busy','true');bdLoad();});
const saved=store.get('view','picture');
bd.view=saved==='cards'?'cards':'picture';
const narrow=typeof matchMedia==='function'?matchMedia('(max-width: 767px)'):null;
bd.narrow=!!(narrow&&narrow.matches);
if(narrow)narrow.addEventListener('change',()=>{bd.narrow=narrow.matches;bdApplyView();bdFilter(false);});
bdApplyView();
if(bd.ch!==Number(root.dataset.ch))bdChrome();
else{
bdSess().set('ch',String(bd.ch));
if(location.pathname==='/board'&&!location.search)try{history.replaceState(history.state,'',`/board?ch=${bd.ch}`);}catch{}
}
bdBindStrip();
bdBindTiles();
bdBindKeys();
bdLoad();
bdPolling();
}
function run(name,fn){
try{fn();}catch(err){console.error(`[l3m0n] ${name} failed`,err);}
}
run('dialog',initDialog);
run('prefs',initPrefs);
run('shell',initShell);
run('keys',initKeys);
run('board',initBoard);
})();