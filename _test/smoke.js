// 冒烟测试：加载 index.html 脚本，验证新增函数可执行
const fs = require('fs');
const path = require('path');
const { El, els } = require('./dom-stub.js');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const scripts = [...html.matchAll(/<script[^>]*>(.*?)<\/script>/gs)].map(m => m[1]);

// 注入真实业务数据
const data = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'elite-fleet-data.json'), 'utf8'));

// 先在脚本前注入 S 与数据
const prelude = `
var S = ${JSON.stringify(data)};
var __ERRORS__ = [];
window.addEventListener = function(){};
`;

// 直接在 sandbox context 中执行脚本（顶层 function/let 声明进入该 context 的词法作用域，
// 后续 runInContext 可访问），并把加载期错误记录下来
const body = scripts[0];

const vm = require('vm');
const sandbox = {
  document: global.document, window: global.window, localStorage: global.localStorage,
  navigator: global.navigator, confirm: global.confirm, alert: global.alert,
  Blob: global.Blob, fetch: global.fetch,
  setTimeout: global.setTimeout, clearTimeout: global.clearTimeout,
  setInterval: global.setInterval, clearInterval: global.clearInterval,
  console, Date, Math, JSON, Object, Array, String, Number, Boolean, RegExp, Error,
  parseInt, parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent,
  location: global.location,
  btoa: s=>Buffer.from(s).toString('base64'),
  __ERRORS__: []
};
// URL 必须保留原生实现（宿主 fs shim 依赖 instanceof URL），单独包装 createObjectURL
sandbox.URL = Object.assign(Object.create(Object.getPrototypeOf(global.URL)), global.URL, {
  createObjectURL(){ return 'blob:x'; }, revokeObjectURL(){}
});
sandbox.global = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);

// 预置 localStorage，让 loadState() 读到真实数据
sandbox.localStorage.setItem('elite-fleet-v1', JSON.stringify(data));

try {
  vm.runInContext(body, sandbox, { filename: 'index.html.js' });
} catch (e) {
  sandbox.__ERRORS__.push(e);
  console.log('加载错误堆栈:\n' + (e.stack||e.message).split('\n').slice(0,8).join('\n'));
}

const run = (code) => {
  try { return { ok:true, val: vm.runInContext(code, sandbox) }; }
  catch(e){ return { ok:false, err: e.message }; }
};

const checks = [];
const check = (name, code, expect) => {
  const r = run(code);
  let pass = r.ok;
  if (pass && expect !== undefined) pass = JSON.stringify(r.val) === JSON.stringify(expect);
  checks.push({ name, pass, detail: r.ok ? JSON.stringify(r.val) : 'ERR: ' + r.err });
};

console.log('=== 加载期错误 ===');
console.log(sandbox.__ERRORS__.length ? sandbox.__ERRORS__.map(e=>e.message).join('\n') : '(无)');

console.log('\n=== 车型归一化引擎 ===');
check('normalizeVehicle 五座 → sedan', 'normalizeVehicle("五座").tier', 'sedan');
check('normalizeVehicle 5-seater sedan → sedan', 'normalizeVehicle("5-seater sedan").tier', 'sedan');
check('normalizeVehicle 7-seater MPV → mpv7', 'normalizeVehicle("7-seater MPV (Buick GL8)").tier', 'mpv7');
check('normalizeVehicle 岚图梦想家 → mpv7', 'normalizeVehicle("岚图梦想家").tier', 'mpv7');
check('normalizeVehicle 14座考斯特或同级 → van', 'normalizeVehicle("14座考斯特或同级").tier', 'van');
check('normalizeVehicle 包车 → charter', 'normalizeVehicle("包车").tier', 'charter');
check('normalizeVehicle 景点门票 → 无档位', 'normalizeVehicle("景点门票").tier', '');
check('sameVehicleTier 五座 vs 五座·经济轿车', 'sameVehicleTier("五座","五座 · 经济轿车")', true);
check('sameVehicleTier 五座 vs 七座', 'sameVehicleTier("五座","七座")', false);
check('sameVehicleTier 包车 vs 九座', 'sameVehicleTier("包车","九座")', true);

console.log('\n=== 定价引擎 ===');
// 北京参考价是成本价口径（quote=0），上海/西安是对外报价口径，故分别验证
check('guessCost 五座北京慕田峪 → 有成本价', 'guessCost({city:"北京",vehicle:"五座",route:"首都机场 → 慕田峪长城 → 酒店"})!==null', true);
check('guessQuote 上海包车 → 有对外报价', 'guessQuote({city:"上海",vehicle:"五座",route:"浦东机场 → 上海市区酒店 市内 包车"})!==null', true);
check('上海包车五座→700 / 七座→900 / 九座→1400', '(()=>{const r=["五座","七座","九座"].map(v=>guessQuote({city:"上海",vehicle:v,route:"上海市区 包车 市内"}));return JSON.stringify(r);})()', '[700,900,1400]');
check('上海包车53座→2100', 'guessQuote({city:"上海",vehicle:"53座",route:"上海市区 包车 市内"})', 2100);
check('guessQuote 上海跨城 → 命中九座价', '(()=>{const o={city:"上海",vehicle:"九座",route:"上海 → 千岛湖酒店 跨城"};const q=guessQuote(o);return q;})()', 2300);
check('guessQuote 上海五座跨城 → 命中五座价', '(()=>{const o={city:"上海",vehicle:"五座",route:"上海 → 千岛湖酒店 跨城"};const q=guessQuote(o);return q;})()', 1700);
check('同档位优先：九座不套五座价', '(()=>{const o={city:"上海",vehicle:"九座",route:"上海 → 千岛湖酒店 跨城"};return guessQuote(o)===2300;})()', true);
check('北京九座无价时降级到就近档位而非报错', 'guessCost({city:"北京",vehicle:"九座",route:"北京华腾美居酒店 → 慕田峪长城 → 北京火车站"})!==null', true);
check('车型不匹配时降权而非误配', 'guessCost({city:"北京",vehicle:"五座",route:"大兴机场 → 慕田峪长城 → 首都机场T3"})!==null', true);
check('空路线返回 null', 'guessQuote({city:"北京",vehicle:"五座",route:""})===null', true);

console.log('\n=== 新增批量函数（存在性 + 可执行） ===');
const fns = ['batchAssignDriver','batchPrintTripMsg','batchGenerateServiceMsg','batchExportExcel','batchMarkDone',
             'saveCommunication','renderCommunicationList','openCommunicationModal','closeCommunicationModal',
             'deleteCommunication','batchSelectedOrders','findPriceInPool','nearestTierTier','normalizeVehicle'];
for(const f of fns){
  check('定义 ' + f, `typeof ${f}`, 'function');
}

console.log('\n=== 批量操作实际执行 ===');
check('选中2单', 'selectedOrders.add(S.orders[0].id); selectedOrders.add(S.orders[1].id); selectedOrders.size', 2);
check('batchSelectedOrders 返回2条', 'batchSelectedOrders().length', 2);
check('batchMarkDone 标记完成', 'batchMarkDone(); S.orders.filter(o=>o.id===S.orders[0].id)[0].status', 'done');
check('沟通记录保存', 'commCtx={type:"guest",orderId:S.orders[0].id,relatedName:"测试客人"}; $("commContent").value="客人确认10月26日接机，浦东机场T2接站"; saveCommunication(); (S.communications||[]).length', 1);
check('沟通记录字段', 'S.communications[0].content', '客人确认10月26日接机，浦东机场T2接站');
check('沟通记录关联订单', 'S.communications[0].orderId===S.orders[0].id', true);
check('renderCommunicationList 不报错', 'renderCommunicationList(); true', true);
check('batchGenerateServiceMsg 生成文本', '(()=>{ selectedOrders.clear(); selectedOrders.add(S.orders[0].id); selectedOrders.add(S.orders[1].id); batchGenerateServiceMsg(); return $("tripMsgBox").value.length>0; })()', true);
check('服务信息按客人分段', '(()=>{ const v=$("tripMsgBox").value; return v.includes("服务信息") && v.includes("————————————"); })()', true);
check('batchPrintTripMsg 不抛错', 'batchPrintTripMsg(); true', true);
check('batchAssignDriver 打开弹窗', 'batchAssignDriver(); !!document.getElementById("batchAssignOverlay")', true);

console.log('\n=== 收款归一化 ===');
check('type=定金 → receive', 'normalizePayment({type:"定金",status:"received",amount:100}).type', 'receive');
check('type=定金 保留 subtype', 'normalizePayment({type:"定金",status:"received",amount:100}).subtype', 'deposit');
check('status=received → completed', 'normalizePayment({type:"定金",status:"received",amount:100}).status', 'completed');
check('status=已收 → completed', 'normalizePayment({type:"收款",status:"已收",amount:100}).status', 'completed');
check('type=支出 → pay', 'normalizePayment({type:"支出",status:"已完成",amount:50}).type', 'pay');
check('USD 折算人民币', 'normalizePayment({type:"定金",status:"received",amount:100,currency:"USD"}).amountCNY', 712);
check('CNY 不折算', 'normalizePayment({type:"定金",status:"received",amount:100,currency:"CNY"}).amountCNY', 100);
check('财务页收入不再为 0', '(()=>{ const ps=normalizedPayments(); return ps.filter(p=>p.type==="receive"&&p.status==="completed").reduce((s,p)=>s+p.amountCNY,0)>0; })()', true);
check('paidOf 识别历史定金记录', '(()=>{ const o=S.orders.find(x=>x.id==="ord_20261102_esther"); return o? paidOf(o)>0 : false; })()', true);

console.log('\n=== 新增业务功能 ===');
const fns2 = ['findDuplicateOrders','duplicateWarningText','getUnpaidAlerts','getDriverWorkload',
              'syncWaInbox','toggleWaInbox','renderWaInbox','waToInquiry','waUseMessage','waMarkReplied'];
for(const f of fns2){
  check('定义 ' + f, `typeof ${f}`, 'function');
}
check('重复检测：同客人同日期能查出', '(()=>{ const o=S.orders[0]; const d=findDuplicateOrders({id:"__new__",guest:o.guest,date:o.date}); return d.length>=1; })()', true);
check('重复检测：不同客人查不出', '(()=>{ const o=S.orders[0]; const d=findDuplicateOrders({id:"__new__",guest:"完全不存在的人"+Math.random(),date:o.date}); return d.length===0; })()', true);
check('重复检测：排除自己', '(()=>{ const o=S.orders[0]; const d=findDuplicateOrders(o); return !d.some(x=>x.id===o.id); })()', true);
check('未收款预警能算出条目', 'getUnpaidAlerts().length>0', true);
check('未收款预警含金额差', '(()=>{ const a=getUnpaidAlerts(); return a.length===0 || (a[0].due>0 && a[0].quote>=a[0].paid); })()', true);
check('司机负载统计有数据', 'getDriverWorkload().length>0', true);
check('司机负载含接单数与工时', '(()=>{ const l=getDriverWorkload(); return l.length===0 || (l[0].orders>0 && l[0].estHours===l[0].orders*4); })()', true);
check('司机负载按月过滤', '(()=>{ const l=getDriverWorkload("1999-01"); return l.length===0; })()', true);
check('WhatsApp 转询价单建记录', '(()=>{ waMessages=[{mid:"m1",name:"WA客户",wa_id:"+8613800000000",text:"Hi, need airport pickup at PVG on Nov 5, 7-seater",ts:Date.now(),status:"new"}]; const n0=(S.inquiries||[]).length; waToInquiry("m1"); return (S.inquiries||[]).length===n0+1; })()', true);
check('WhatsApp 消息解析出城市/车型', '(()=>{ const i=(S.inquiries||[]).find(x=>x.id==="wa_m1"); return i? (i.vehicle!=="" ) : false; })()', true);
check('WhatsApp 用消息填充解析框', '(()=>{ waUseMessage("m1"); return $("agentMsg").value.length>0; })()', true);
check('WhatsApp 渲染列表不报错', 'renderWaInbox({total:1,unread:1}); true', true);

console.log('\n=== 结果 ===');
let pass=0, fail=0;
for(const c of checks){
  console.log((c.pass?'  ✓ ':'  ✗ ')+c.name.padEnd(46)+(c.pass?'':('  → '+c.detail)));
  c.pass?pass++:fail++;
}
console.log(`\n通过 ${pass} / ${pass+fail}`+(fail?`  失败 ${fail}`:''));
process.exit(fail?1:0);
