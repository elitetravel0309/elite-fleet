// 用真实数据测量定价匹配率改善（修复前 vs 修复后）
const fs=require('fs');
const path=require('path');
const { El, els } = require('./dom-stub.js');
const html = fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
const scripts=[...html.matchAll(/<script[^>]*>(.*?)<\/script>/gs)].map(m=>m[1]);
const data=JSON.parse(fs.readFileSync(path.join(__dirname,'..','data','elite-fleet-data.json'),'utf8'));
const vm=require('vm');
const sandbox={ document:global.document,window:global.window,localStorage:global.localStorage,
  navigator:global.navigator,confirm:global.confirm,alert:global.alert,Blob:global.Blob,fetch:global.fetch,
  setTimeout:global.setTimeout,clearTimeout:global.clearTimeout,setInterval:global.setInterval,clearInterval:global.clearInterval,
  console,Date,Math,JSON,Object,Array,String,Number,Boolean,RegExp,Error,parseInt,parseFloat,isNaN,isFinite,
  encodeURIComponent,decodeURIComponent,location:global.location,btoa:s=>Buffer.from(s).toString('base64')};
sandbox.URL=Object.assign(Object.create(Object.getPrototypeOf(global.URL)),global.URL,{createObjectURL(){return 'blob:x'},revokeObjectURL(){}});
sandbox.global=sandbox; sandbox.self=sandbox;
vm.createContext(sandbox);
sandbox.localStorage.setItem('elite-fleet-v1',JSON.stringify(data));
vm.runInContext(scripts[0],sandbox,{filename:'i.js'});
const run=c=>vm.runInContext(c,sandbox);

// 旧实现（修复前）：字符串相等 + 不剥括号
const OLD = `
(function(){
  function oldPk(route){
    if(!route) return [];
    return route.split(/[·+]/).map(s=>s.trim()).filter(s=>s.length>=2 && s!=='包车' && s!=='单程' && s!=='包天' && s!=='全程包车' && s!=='车队日租');
  }
  function oldFind(o, field){
    if(!o.route) return null;
    const city=o.city||detectCity(o.route,o.airport,'');
    const vehicle=o.vehicle||'';
    for(const p of S.prices){ if(!p[field]) continue; if(city&&p.city&&p.city!==city) continue;
      if(vehicle&&p.vehicle&&p.vehicle!==vehicle) continue;
      const kws=oldPk(p.route); for(const kw of kws){ if(kw&&o.route.includes(kw)) return p[field]; }
      if(p.route&&o.route.includes(p.route)) return p[field]; }
    for(const p of S.prices){ if(!p.field) continue; }
    for(const p of S.prices){ if(!p[field]) continue; if(city&&p.city&&p.city!==city) continue;
      const kws=oldPk(p.route); for(const kw of kws){ if(kw&&o.route.includes(kw)) return p[field]; } }
    return null;
  }
  const orders=S.orders.filter(o=>o.route&&o.vehicle);
  let tierHit=0, nullCnt=0, priced=0;
  const wrong=[];
  for(const o of orders){
    const c=oldFind(o,'cost')||oldFind(o,'quote');
    if(c==null){ nullCnt++; continue; }
    priced++;
    const hit=S.prices.find(p=>(p.cost===c||p.quote===c)&&p.city===(o.city||detectCity(o.route,o.airport,''))&&p.vehicle===o.vehicle);
    if(hit) tierHit++; else wrong.push([o.id,o.vehicle]);
  }
  return { total:orders.length, priced, tierHit, nullCnt, wrong:wrong.length };
})()
`;
console.log('=== 修复前（字符串相等 + 括号未剥离）===');
console.log(JSON.stringify(run(OLD), null, 2));

console.log('\n=== 修复后（档位归一 + 评分制 + 关键词剥离括号）===');
const NEW = `
(function(){
  const orders=S.orders.filter(o=>o.route&&o.vehicle);
  let tierHit=0, nullCnt=0, priced=0, wrong=0;
  for(const o of orders){
    const c=findPriceInPool(o,'cost')!=null?findPriceInPool(o,'cost'):findPriceInPool(o,'quote');
    if(c==null){ nullCnt++; continue; }
    priced++;
    const city=o.city||detectCity(o.route,o.airport,'');
    const p=S.prices.find(x=>(x.cost===c||x.quote===c)&&x.city===city);
    if(p && sameVehicleTier(o.vehicle,p.vehicle)) tierHit++;
    else if(p && (normalizeVehicle(p.vehicle).tier==='charter'||normalizeVehicle(o.vehicle).tier==='charter')) tierHit++;
    else wrong++;
  }
  return { total:orders.length, priced, tierHit, nullCnt, wrong };
})()
`;
console.log(JSON.stringify(run(NEW), null, 2));
