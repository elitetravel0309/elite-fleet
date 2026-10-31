// 全量渲染检查：调用每个 render 函数，确保不抛异常
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
const run=c=>{try{ vm.runInContext(c,sandbox); return {ok:true}; }catch(e){ return {ok:false,err:e.message+'\n'+(e.stack||'').split('\n').slice(1,4).join('\n')}; }};

const renders = [
  'renderAll()', 'renderToday()', 'renderOrders()', 'renderGuests()', 'renderHotels()',
  'renderFleet()', 'renderPrices()', 'renderKpis()', 'renderInquiries()', 'renderFinance()',
  'renderTrade()', 'renderAgentQueue()', 'renderReport()', 'renderGallery()',
  'renderFollowupPanel()', 'renderVersionList()', 'buildDriverFilters()', 'buildFleetFilters()',
  'renderWeek()', 'renderUpcoming()', 'renderWaInbox({total:1,unread:0})',
  'renderCommunicationList()'
];

console.log('=== 渲染函数执行检查 ===');
let pass=0, fail=0;
for(const r of renders){
  const res = run(r);
  console.log((res.ok?'  ✓ ':'  ✗ ')+r.padEnd(34)+(res.ok?'':'  → '+res.err));
  res.ok?pass++:fail++;
}
console.log('\n=== 关键交互路径 ===');
const flows = [
  ['打开订单弹窗', 'openOrder(S.orders[0].id)'],
  ['打开客人弹窗', 'openGuestModal(S.guests[0].id)'],
  ['打开车队弹窗', 'openFleetModal(S.fleets[0].id)'],
  ['打开司机弹窗', 'openDriverModal(S.drivers[0].id)'],
  ['打开参考价弹窗', 'openPriceModal(S.prices[0].id||null)'],
  ['打开智能报价分析', 'enhancedSmartPrice(S.orders[0])'],
  ['生成行程单', 'generateItinerary(S.orders[0].id)'],
  ['生成服务信息', 'serviceMsg(S.orders[0].id)'],
  ['生成车单', 'tripMsg(S.orders[0].id)'],
  ['打开沟通记录', 'openCommunicationModal("guest", S.orders[0].id, "测试")'],
  ['司机空闲查询', 'checkDriverAvailability(S.orders[0].date)'],
  ['订单状态流转', 'advanceOrderStatus(S.orders[0].id)'],
  ['查重提示文案', 'duplicateWarningText(findDuplicateOrders({id:"x",guest:S.orders[0].guest,date:S.orders[0].date}))'],
  ['导出CSV（不落盘）', 'typeof exportOrdersCSV==="function"'],
];
for(const [name, code] of flows){
  const res = run(code);
  console.log((res.ok?'  ✓ ':'  ✗ ')+name.padEnd(34)+(res.ok?'':'  → '+res.err));
  res.ok?pass++:fail++;
}

console.log('\n=== 数据完整性 ===');
// 注：参考价/司机/车队在加载时会与 SEED 合并（应用原有行为），故只校验业务数据未被破坏
const integrity = [
  ['订单数未变', 'S.orders.length===90'],
  ['客人数未减少（ensureGuests 自动补全档案）', 'S.guests.length>=51'],
  ['收款数未变', '(S.payments||[]).length===3'],
  ['参考价已合并种子（>=230）', 'S.prices.length>=230'],
  ['司机已合并种子（>=24）', 'S.drivers.length>=24'],
  ['车队已合并种子（>=17）', 'S.fleets.length>=17'],
  ['沟通记录为新增数组', 'Array.isArray(S.communications)'],
  ['价格库可匹配多数订单', '(()=>{ const os=S.orders.filter(o=>o.route&&o.vehicle); const hit=os.filter(o=>findPriceInPool(o,"cost")!=null||findPriceInPool(o,"quote")!=null); return hit.length/os.length>0.4; })()'],
  ['订单原始报价字段未被改写', '(()=>{ const o=S.orders.find(x=>x.quote); return o && o.quote===o.price || !o.price; })()'],
];
for(const [name, code] of integrity){
  const res = run(code);
  const val = res.ok? vm.runInContext(code, sandbox) : null;
  console.log((res.ok&&val?'  ✓ ':'  ✗ ')+name.padEnd(34)+(res.ok?('→ '+JSON.stringify(val)):'  → '+res.err));
  (res.ok&&val)?pass++:fail++;
}

console.log(`\n通过 ${pass} / ${pass+fail}`+(fail?`  失败 ${fail}`:''));
process.exit(fail?1:0);
