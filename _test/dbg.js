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
const run=c=>{try{return vm.runInContext(c,sandbox)}catch(e){return 'ERR:'+e.message}};

console.log('priceKeywords("包车 · 市内（8小时）") =', JSON.stringify(run('priceKeywords("包车 · 市内（8小时）")')));
console.log('priceKeywords("包车 · 市内") =', JSON.stringify(run('priceKeywords("包车 · 市内")')));
console.log('priceKeywords("跨城 · 上海 → 千岛湖酒店") =', JSON.stringify(run('priceKeywords("跨城 · 上海 → 千岛湖酒店")')));
console.log('\n上海 市内 相关价:');
console.log(run('JSON.stringify(S.prices.filter(p=>p.city==="上海"&&String(p.route).includes("市内")).slice(0,3))'));
console.log('\n尝试 route="上海市区 包车 市内":', run('guessQuote({city:"上海",vehicle:"五座",route:"上海市区 包车 市内"})'));
console.log('尝试 route="包车 · 市内（8小时）":', run('guessQuote({city:"上海",vehicle:"五座",route:"包车 · 市内（8小时）"})'));
console.log('\npriceKeywords 源码:');
console.log(run('priceKeywords.toString()'));
