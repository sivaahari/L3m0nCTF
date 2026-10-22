(()=>{'use strict';
function initSlate(){
const body=document.body;
let s=Number(body.dataset.retry);
if(!(s>0))return;
const jitter=/^(\d+)-(\d+)$/.exec(body.dataset.retryJitter||'');
if(jitter)s=Number(jitter[1])+Math.floor(Math.random()*(Number(jitter[2])-Number(jitter[1])+1));
const target=body.dataset.retryUrl||location.pathname+location.search;
const num=document.querySelector('[data-retry-s]');
const text=document.querySelector('[data-retry-text]');
const bar=document.querySelector('[data-retry-bar]');
const stop=document.querySelector('[data-retry-stop]');
if(bar){
bar.className=bar.className.replace(/\brb-\d+\b/,`rb-${s}`);
const fill=bar.firstElementChild;
if(fill){fill.style.animation='none';void fill.offsetWidth;fill.style.animation='';}
}
const end=Date.now()+s*1000;
let timer=0;
const tick=()=>{
const left=Math.max(0,Math.ceil((end-Date.now())/1000));
if(num)num.textContent=String(left);
if(left===0){location.replace(target);return;}
timer=setTimeout(tick,((end-Date.now())%1000)+20);
};
tick();
if(stop){
stop.hidden=false;
stop.addEventListener('click',()=>{
clearTimeout(timer);
if(bar)bar.classList.add('stopped');
if(text)text.textContent='Retrying is paused. Try again when you are ready.';
stop.hidden=true;
const again=document.querySelector('.slate-way .btn');
if(again)again.focus();
});
}
}
function run(name,fn){
try{fn();}catch(err){console.error(`[l3m0n] ${name} failed`,err);}
}
run('slate',initSlate);
})();