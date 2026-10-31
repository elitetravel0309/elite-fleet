// 轻量 DOM stub：用于在 Node 中加载 index.html 的脚本并执行新增函数
class El {
  constructor(tag){ this.tagName=(tag||'div').toUpperCase(); this.style={}; this.dataset={}; this.children=[]; this._html=''; this.value=''; this.textContent=''; this.innerHTML=''; this.checked=false; this.disabled=false; this.options=[]; this.classList={ _s:new Set(), add(...c){c.forEach(x=>this._s.add(x));}, remove(...c){c.forEach(x=>this._s.delete(x));}, contains(c){return this._s.has(c);}, toggle(c,on){ if(on===undefined){ on=!this._s.has(c); } on?this._s.add(c):this._s.delete(c); return on; } }; this.addEventListener=()=>{}; }
  appendChild(c){ this.children.push(c); return c; }
  remove(){}
  querySelector(){ return new El('div'); }
  querySelectorAll(){ return []; }
  setAttribute(){}
  getAttribute(){ return null; }
  removeAttribute(){}
  focus(){}
  click(){}
}
const els = {};
global.document = {
  getElementById(id){ if(!els[id]) els[id]=new El('div'); return els[id]; },
  querySelector(){ return new El('div'); },
  querySelectorAll(){ return []; },
  createElement(tag){ return new El(tag); },
  body: new El('body'),
  addEventListener(){},
  get documentElement(){ return new El('html'); }
};
global.window = {
  open(){ return { document:{ write(){}, close(){} } }; },
  print(){},
  addEventListener(){},
  localStorage: null
};
const store = {};
global.localStorage = {
  getItem(k){ return store[k]!==undefined?store[k]:null; },
  setItem(k,v){ store[k]=String(v); },
  removeItem(k){ delete store[k]; },
  clear(){ for(const k in store) delete store[k]; }
};
global.navigator = { clipboard:{ writeText(){ return Promise.resolve(); } } };
global.location = { href:'http://localhost/', origin:'http://localhost', protocol:'http:', host:'localhost', hostname:'localhost', port:'', pathname:'/', search:'', hash:'' };
global.confirm = ()=>global.__confirmResult__!==false;
global.alert = ()=>{};
global.Blob = class { constructor(){} };
global.fetch = ()=>Promise.reject(new Error('no-net'));
global.setTimeout = (fn)=>0;
global.clearTimeout = ()=>{};
global.setInterval = ()=>0;
global.clearInterval = ()=>{};
global.addDays = undefined;

module.exports = { El, els, store };
