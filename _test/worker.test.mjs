// Worker 端点集成测试：用内存 KV 模拟，验证端点契约与鉴权
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', 'cloudflare-worker', 'elite-fleet-api', 'src', 'index.js'), 'utf8');

// 去掉 paypal 依赖（避免拉起飞书 webhook），改为桩
const patched = src.replace(
  "import { handlePayPalWebhook } from './paypal.js';",
  "const handlePayPalWebhook = async () => new Response('{}', { status: 200 });"
);
const mod = await import('data:text/javascript;base64,' + Buffer.from(patched).toString('base64'));
const worker = mod.default;

// 内存 KV
const kv = new Map();
const env = {
  EF_DATA: {
    async get(k){ return kv.has(k) ? kv.get(k) : null; },
    async put(k,v){ kv.set(k,v); },
    async delete(k){ kv.delete(k); }
  }
};

// 预置数据
kv.set('data:inquiries', JSON.stringify([
  { id:'inq_1', name:'测试客人', status:'new', service_date:'2026-11-01' },
  { id:'inq_2', name:'另一客人', status:'new' }
]));
kv.set('data:wa_inbox', JSON.stringify([
  { mid:'wamid.1', wa_id:'8613800138000', name:'WhatsApp客户', text:'Hi, need airport pickup', ts:Date.now(), status:'new' },
  { mid:'wamid.2', wa_id:'8613900139000', name:'另一客户', text:'7-seater please', ts:Date.now(), status:'replied' }
]));
kv.set('data:workbench', JSON.stringify({
  orders:[{ id:'o1', date:'2026-11-01', time:'10:00', route:'北京 → 慕田峪', vehicle:'五座', quote:800, cost:650, customerName:'SECRET' }],
  prices:[{ city:'北京', route:'慕田峪 · 机场接送', vehicle:'五座', cost:650, quote:0 }],
  drivers:[{ name:'张三', phone:'13800000000' }],
  guests:[{ name:'机密客人', passportNo:'E12345' }]
}));

const call = (path, opts={}) => worker.fetch(new Request('https://fleet.chinawondercars.com'+path, opts), env);
const basic = 'Basic ' + Buffer.from('elite:EF-Fleet-2026').toString('base64');

let pass=0, fail=0;
const check=(name, cond, detail='')=>{ console.log((cond?'  ✓ ':'  ✗ ')+name+(cond?'':'  → '+detail)); cond?pass++:fail++; };

console.log('=== 询盘接口鉴权 ===');
let r = await call('/api/inquiries?limit=500', { headers:{ 'x-api-secret':'ef-inq-2026' } });
check('GET /api/inquiries 用 x-api-secret → 200（修复前 401）', r.status===200, 'status='+r.status);

r = await call('/api/inquiries?limit=500', { headers:{ Authorization: basic } });
check('GET /api/inquiries 用 Basic Auth → 200', r.status===200, 'status='+r.status);

r = await call('/api/inquiries?limit=500');
check('GET /api/inquiries 无凭据 → 403', r.status===403, 'status='+r.status);

console.log('\n=== 询盘状态回写（前端 pushSiteInquiryStatus 依赖）===');
r = await call('/api/inquiries/inq_1', {
  method:'PUT', headers:{ 'x-api-secret':'ef-inq-2026', 'Content-Type':'application/json' },
  body: JSON.stringify({ status:'contacted' })
});
let body = await r.json().catch(()=>({}));
check('PUT /api/inquiries/:id → 200（修复前 404）', r.status===200, 'status='+r.status);
check('PUT 更新了状态', body.inquiry && body.inquiry.status==='contacted', JSON.stringify(body));
check('PUT 未污染原始 id', body.inquiry && body.inquiry.id==='inq_1');

r = await call('/api/inquiries/nonexistent', {
  method:'PUT', headers:{ 'x-api-secret':'ef-inq-2026', 'Content-Type':'application/json' },
  body: JSON.stringify({ status:'won' })
});
check('PUT 不存在的 id → 404', r.status===404, 'status='+r.status);

console.log('\n=== WhatsApp 收件箱（此前只写不读）===');
r = await call('/api/wa-inbox?limit=100', { headers:{ 'x-api-secret':'ef-inq-2026' } });
body = await r.json().catch(()=>({}));
check('GET /api/wa-inbox → 200', r.status===200, 'status='+r.status);
check('返回消息列表', Array.isArray(body.messages) && body.messages.length===2, JSON.stringify(body.counts));
check('统计未读数', body.counts && body.counts.unread===1, JSON.stringify(body.counts));

r = await call('/api/wa-inbox?new=1', { headers:{ 'x-api-secret':'ef-inq-2026' } });
body = await r.json().catch(()=>({}));
check('GET /api/wa-inbox?new=1 只返回未读', body.messages && body.messages.length===1);

r = await call('/api/wa-inbox');
check('GET /api/wa-inbox 无凭据 → 403', r.status===403, 'status='+r.status);

r = await call('/api/wa-inbox/wamid.1/status', {
  method:'POST', headers:{ 'x-api-secret':'ef-inq-2026', 'Content-Type':'application/json' },
  body: JSON.stringify({ status:'replied' })
});
body = await r.json().catch(()=>({}));
check('POST /api/wa-inbox/:mid/status 标记已回复 → 200', r.status===200, 'status='+r.status);
const after = JSON.parse(kv.get('data:wa_inbox'));
check('状态已持久化', after.find(m=>m.mid==='wamid.1').status==='replied');

console.log('\n=== 报价智能体公开数据（不得泄露订单 PII）===');
r = await call('/api/public-data');
body = await r.json().catch(()=>({}));
check('GET /api/public-data → 200', r.status===200, 'status='+r.status);
check('返回 prices', Array.isArray(body.prices) && body.prices.length===1);
check('公开接口不返回 orders（防客人信息泄露）', body.orders===undefined, JSON.stringify(body));
check('公开数据无客人字段', JSON.stringify(body).indexOf('机密客人')===-1);
check('公开数据无司机字段', JSON.stringify(body).indexOf('张三')===-1);
check('公开数据无护照号', JSON.stringify(body).indexOf('E12345')===-1);

r = await call('/api/quote-orders', { headers:{ Authorization: basic } });
body = await r.json().catch(()=>({}));
check('GET /api/quote-orders Basic Auth → 200', r.status===200, 'status='+r.status);
check('返回 orders（quote-agent 下拉用）', Array.isArray(body.orders) && body.orders.length===1, JSON.stringify(body.orders));
r = await call('/api/quote-orders');
check('GET /api/quote-orders 无凭据 → 401', r.status===401, 'status='+r.status);

console.log('\n=== 上传与工作台数据 ===');
r = await call('/api/upload?key=EF-KV-2026-9f3a7c2e', {
  method:'POST', headers:{ 'Content-Type':'application/json' },
  body: JSON.stringify({ data:{ orders:[{id:'x1'}], prices:[], _lastModified: Date.now() } })
});
body = await r.json().catch(()=>({}));
check('POST /api/upload 正常密钥 → 200', r.status===200 && body.ok, JSON.stringify(body));

r = await call('/api/upload?key=wrong', {
  method:'POST', headers:{ 'Content-Type':'application/json' },
  body: JSON.stringify({ data:{ orders:[] } })
});
check('POST /api/upload 错误密钥 → 403', r.status===403, 'status='+r.status);

r = await call('/api/data', { headers:{ Authorization: basic } });
check('GET /api/data Basic Auth → 200', r.status===200, 'status='+r.status);

r = await call('/api/data');
check('GET /api/data 无凭据 → 401', r.status===401, 'status='+r.status);

console.log('\n=== WhatsApp Webhook 写入仍正常 ===');
r = await call('/api/wa-webhook', {
  method:'POST', headers:{ 'Content-Type':'application/json' },
  body: JSON.stringify({ entry:[{ changes:[{ value:{ messages:[{ id:'wamid.3', from:'8613700000000', type:'text', text:{ body:'新消息' } }], contacts:[{ profile:{ name:'新客户' } }] } }] }] })
});
body = await r.json().catch(()=>({}));
check('POST /api/wa-webhook → 200', r.status===200 && body.ok, JSON.stringify(body).slice(0,120));
const inbox3 = JSON.parse(kv.get('data:wa_inbox'));
check('webhook 写入收件箱', inbox3.length===3);

console.log('\n=== 客人提交询盘（公开）===');
r = await call('/api/inquiry', {
  method:'POST', headers:{ 'Content-Type':'application/json' },
  body: JSON.stringify({ name:'新询价客人', whatsapp:'+8613800000000', service_date:'2026-11-05', city:'北京' })
});
body = await r.json().catch(()=>({}));
check('POST /api/inquiry → 200', r.status===200 && body.ok, JSON.stringify(body));

console.log('\n=== 蜜罐防机器人 ===');
r = await call('/api/inquiry', {
  method:'POST', headers:{ 'Content-Type':'application/json' },
  body: JSON.stringify({ name:'bot', whatsapp:'x', service_date:'2026-11-05', _company:'spam' })
});
body = await r.json().catch(()=>({}));
check('蜜罐字段 → 假成功不落库', body.ok===true && !body.id, JSON.stringify(body));

console.log(`\n通过 ${pass} / ${pass+fail}`+(fail?`  失败 ${fail}`:''));
process.exit(fail?1:0);
