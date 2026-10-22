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
function channelKeys(channels){
const nos=channels.map((c)=>c.no);
const usable=nos.every((n)=>Number.isInteger(n)&&n>=0&&n<=99)&&new Set(nos).size===nos.length;
return new Map(channels.map((c)=>[c.id,usable?c.no:c.id]));
}
let platformKeys=new Map();
function boardByKey(data){
if(!data||!Array.isArray(data.channels)||!Array.isArray(data.programmes))return data;
platformKeys=channelKeys(data.channels);
return{
...data,
channels:data.channels.map((c)=>({...c,id:platformKeys.get(c.id)})),
programmes:data.programmes.map((p)=>({...p,channel:platformKeys.has(p.channel)?platformKeys.get(p.channel):p.channel})),
};
}
function challengeByKey(data){
if(!data||!data.l3mon||!platformKeys.has(data.l3mon.channel))return data;
return{...data,l3mon:{...data.l3mon,channel:platformKeys.get(data.l3mon.channel)}};
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
const board=async(etag,o={})=>{
const r=await apiCall('GET',ROUTES.board,{...o,etag});
if(r.ok&&r.data)r.data=boardByKey(r.data);
return r;
};
const ticks=()=>apiCall('GET',ROUTES.ticks,{retries:1});
const notifications=(sinceId)=>apiCall('GET',`${ROUTES.notifications}?since_id=${encodeURIComponent(sinceId||0)}`,{retries:1});
const challenge=async(id,etag)=>{
const r=await apiCall('GET',ROUTES.challenge(id),{etag});
if(r.ok&&r.data)r.data=challengeByKey(r.data);
return r;
};
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
function initShow(form){
const btn=$('[data-show]',form);
const input=$('#f-password',form);
if(!btn||!input)return;
btn.addEventListener('click',()=>{
const show=input.type==='password';
input.type=show?'text':'password';
btn.setAttribute('aria-pressed',String(show));
});
form.addEventListener('submit',()=>{input.type='password';btn.setAttribute('aria-pressed','false');});
}
function initRetry(form){
const s=Number(form.dataset.retryAfter);
if(!(s>0))return;
const btn=$('[data-submit]',form);
const wait=$('[data-wait]',form);
btn.disabled=true;
wait.hidden=false;
countdown(s,(left)=>{
const m=Math.ceil(left/60);
wait.textContent=left>90?`You can try again in about ${m} minutes.`:`You can try again in ${left} s.`;
},()=>{
btn.disabled=false;
wait.textContent='You can try again now.';
});
}
function initLogin(){
const form=$('[data-login]');
if(!form)return;
const name=$('#f-name',form);
const pass=$('#f-password',form);
const btn=$('[data-submit]',form);
const host=form.parentElement;
initShow(form);
initRetry(form);
for(const input of[name,pass])input.addEventListener('input',()=>{if(input.value)clearFieldError(input);});
let sent=false;
form.addEventListener('submit',(e)=>{
if(sent||btn.getAttribute('aria-disabled')==='true'||btn.disabled){e.preventDefault();return;}
const problems=[];
for(const[input,msg]of[[name,'Enter your username or email.'],[pass,'Enter your password.']]){
if(!input.value.trim()){fieldError(input,msg);problems.push({id:input.id,text:msg});}else clearFieldError(input);
}
if(problems.length){
e.preventDefault();
showSummary(host,problems);
sfx('bonk');
return;
}
sent=true;
setPending(btn,'Tuning in…');
sfx('click');
const card=$('[data-card]');
if(card&&!reduced())card.classList.add('flick');
});
addEventListener('pageshow',(e)=>{
if(!e.persisted)return;
sent=false;
clearPending(btn);
const card=$('[data-card]');
if(card)card.classList.remove('flick');
});
}
function run(name,fn){
try{fn();}catch(err){console.error(`[l3m0n] ${name} failed`,err);}
}
run('dialog',initDialog);
run('prefs',initPrefs);
run('shell',initShell);
run('keys',initKeys);
run('login',initLogin);
})();