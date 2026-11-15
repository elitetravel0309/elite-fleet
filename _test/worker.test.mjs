// Worker 端点集成测试：用内存 KV 模拟，验证端点契约与鉴权
// 2026-11-15：凭据改为 env Secret，测试统一使用「测试专用假密钥」，仓库里不再出现真实凭据
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', 'cloudflare-worker', 'elite-fleet-api', 'src', 'index.js'), 'utf8');

// 去掉外部依赖（避免拉起飞书 webhook / SMTP socket），改为桩
const patched = src
  .replace(
    "import { handlePayPalWebhook } from './paypal.js';",
    "const handlePayPalWebhook = async () => new Response('{}', { status: 200 });"
  )
  .replace(
    "import { sendReceiptEmail, sendConfirmationEmails } from './emailer.js';",
    `const sendReceiptEmail = async (env, p) => !env.SMTP_HOST
      ? { ok:false, error:'邮件服务未配置', code:503 }
      : { ok:true, to:p.email, subject:'Payment Receipt', sentAt:new Date().toISOString() };
     const sendConfirmationEmails = async () => ({ ok:true, scanned:0, pending:0, sent:0, failed:0, results:[] });`
  );
const mod = await import('data:text/javascript;base64,' + Buffer.from(patched).toString('base64'));
const worker = mod.default;

// 内存 KV
const kv = new Map();
const env = {
  EF_UPLOAD_KEY: 'test-upload-key',
  EF_AUTH_USER: 'elite',
  EF_AUTH_PASS: 'test-auth-pass',
  EF_INQ_SECRET: 'test-inq-secret',
  EF_WA_VERIFY_TOKEN: 'test-wa-verify',
  EF_TWILIO_TOKEN: 'test-twilio-token',
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
const basic = 'Basic ' + Buffer.from(env.EF_AUTH_USER+':'+env.EF_AUTH_PASS).toString('base64');
const inq = { 'x-api-secret': env.EF_INQ_SECRET };

let pass=0, fail=0;
const check=(name, cond, detail='')=>{ console.log((cond?'  ✓ ':'  ✗ ')+name+(cond?'':'  → '+detail)); cond?pass++:fail++; };

console.log('=== 询盘接口鉴权 ===');
let r = await call('/api/inquiries?limit=500', { headers: inq });
check('GET /api/inquiries 用 x-api-secret → 200（修复前 401）', r.status===200, 'status='+r.status);

r = await call('/api/inquiries?limit=500', { headers:{ Authorization: basic } });
check('GET /api/inquiries 用 Basic Auth → 200', r.status===200, 'status='+r.status);

r = await call('/api/inquiries?limit=500');
check('GET /api/inquiries 无凭据 → 403', r.status===403, 'status='+r.status);

r = await call('/api/inquiries?limit=500', { headers:{ 'x-api-secret':'old-leaked-inq-key' } });
check('旧的泄露密钥已被拒绝', r.status===403, 'status='+r.status);

console.log('\n=== 询盘状态回写（前端 pushSiteInquiryStatus 依赖）===');
r = await call('/api/inquiries/inq_1', {
  method:'PUT', headers:{ ...inq, 'Content-Type':'application/json' },
  body: JSON.stringify({ status:'contacted' })
});
let body = await r.json().catch(()=>({}));
check('PUT /api/inquiries/:id → 200（修复前 404）', r.status===200, 'status='+r.status);
check('PUT 更新了状态', body.inquiry && body.inquiry.status==='contacted', JSON.stringify(body));
check('PUT 未污染原始 id', body.inquiry && body.inquiry.id==='inq_1');

r = await call('/api/inquiries/nonexistent', {
  method:'PUT', headers:{ ...inq, 'Content-Type':'application/json' },
  body: JSON.stringify({ status:'won' })
});
check('PUT 不存在的 id → 404', r.status===404, 'status='+r.status);

console.log('\n=== WhatsApp 收件箱（此前只写不读）===');
r = await call('/api/wa-inbox?limit=100', { headers: inq });
body = await r.json().catch(()=>({}));
check('GET /api/wa-inbox → 200', r.status===200, 'status='+r.status);
check('返回消息列表', Array.isArray(body.messages) && body.messages.length===2, JSON.stringify(body.counts));
check('统计未读数', body.counts && body.counts.unread===1, JSON.stringify(body.counts));

r = await call('/api/wa-inbox?new=1', { headers: inq });
body = await r.json().catch(()=>({}));
check('GET /api/wa-inbox?new=1 只返回未读', body.messages && body.messages.length===1);

r = await call('/api/wa-inbox');
check('GET /api/wa-inbox 无凭据 → 403', r.status===403, 'status='+r.status);

r = await call('/api/wa-inbox/wamid.1/status', {
  method:'POST', headers:{ ...inq, 'Content-Type':'application/json' },
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

console.log('\n=== 上传与工作台数据（乐观锁 / 并发合并）===');
r = await call('/api/upload?key='+env.EF_UPLOAD_KEY, {
  method:'POST', headers:{ 'Content-Type':'application/json' },
  body: JSON.stringify({ data:{ orders:[{id:'x1'}], prices:[], _lastModified: Date.now() } })
});
body = await r.json().catch(()=>({}));
check('POST /api/upload 正常密钥 → 200', r.status===200 && body.ok, JSON.stringify(body));
check('返回递增版本号 rev', Number(body.rev)>=1, JSON.stringify(body));

r = await call('/api/upload?key=wrong', {
  method:'POST', headers:{ 'Content-Type':'application/json' },
  body: JSON.stringify({ data:{ orders:[] } })
});
check('POST /api/upload 错误密钥 → 403', r.status===403, 'status='+r.status);
r = await call('/api/upload?key=old-leaked-upload-key', { method:'POST', body:'{"data":{"orders":[]}}' });
check('旧的泄露上传密钥已被拒绝', r.status===403, 'status='+r.status);

r = await call('/api/data', { headers:{ Authorization: basic } });
check('GET /api/data Basic Auth → 200', r.status===200, 'status='+r.status);
check('GET /api/data 暴露 X-EF-Rev 供前端做乐观锁', !!r.headers.get('X-EF-Rev'), String(r.headers.get('X-EF-Rev')));
const curRev = Number(r.headers.get('X-EF-Rev')||0);

r = await call('/api/data');
check('GET /api/data 无凭据 → 401', r.status===401, 'status='+r.status);

// 模拟：A 设备基于 rev 编辑，同时 B 设备已写入 cloud-only 订单
kv.set('data:workbench', JSON.stringify({
  orders:[{id:'o1'},{id:'cloud-only',guest:'B设备新增'}], prices:[]
}));
kv.set('meta:workbench_rev', String(curRev+1));
r = await call('/api/upload?key='+env.EF_UPLOAD_KEY, {
  method:'POST', headers:{ 'Content-Type':'application/json' },
  body: JSON.stringify({ data:{ orders:[{id:'o1',guest:'A设备修改'}], prices:[] }, baseVersion: curRev })
});
body = await r.json().catch(()=>({}));
const mergedData = JSON.parse(kv.get('data:workbench'));
check('版本落后 → 200 且标记 merged', r.status===200 && body.merged===true, JSON.stringify(body));
check('云端独有订单未被整包覆盖', mergedData.orders.some(o=>o.id==='cloud-only'), JSON.stringify(mergedData.orders));
check('上传方的修改被保留', mergedData.orders.some(o=>o.id==='o1' && o.guest==='A设备修改'));
check('合并后 rev 继续递增', Number(body.rev)===curRev+2, String(body.rev));

r = await call('/api/upload?key='+env.EF_UPLOAD_KEY, {
  method:'POST', headers:{ 'Content-Type':'application/json' },
  body: JSON.stringify({ data:{ orders:[{id:'o1'}], prices:[] }, baseVersion: Number(body.rev) })
});
body = await r.json().catch(()=>({}));
check('版本一致 → 不触发合并', r.status===200 && body.merged===false, JSON.stringify(body));

console.log('\n=== 每日快照与备份端点 ===');
await worker.scheduled({ scheduledTime: Date.now(), cron:'0 18 * * *' }, env, { waitUntil(){} });
r = await call('/api/backups', { headers:{ Authorization: basic } });
body = await r.json().catch(()=>({}));
check('GET /api/backups → 1 天快照', r.status===200 && body.backups && body.backups.length===1, JSON.stringify(body).slice(0,200));
const bk = (body.backups && body.backups[0] && body.backups[0].keys || [])[0];
if(bk){
  r = await call('/api/backups/'+encodeURIComponent(bk), { headers:{ Authorization: basic } });
  check('可下载指定快照内容', r.status===200 && (await r.text()).indexOf('orders')>=0);
}
r = await call('/api/backups/backup:2026-01-01:../etc', { headers:{ Authorization: basic } });
check('非法备份 key → 400（防路径穿越）', r.status===400, 'status='+r.status);
r = await call('/api/backups');
check('备份列表需登录 → 401', r.status===401, 'status='+r.status);

console.log('\n=== WhatsApp 出站回复 / 收据邮件 ===');
r = await call('/api/wa-send', {
  method:'POST', headers:{ ...inq, 'Content-Type':'application/json' },
  body: JSON.stringify({ to:'8613800138000', text:'Hello', mid:'wamid.1' })
});
check('未配置 Meta 凭据 → 503 明确报错', r.status===503, 'status='+r.status);
r = await call('/api/wa-send');
check('未登录调用 wa-send → 401', r.status===401, 'status='+r.status);

r = await call('/api/send-receipt', {
  method:'POST', headers:{ Authorization: basic, 'Content-Type':'application/json' },
  body: JSON.stringify({ email:'guest@example.com', receiptNo:'R-1', paid:100 })
});
check('未配置 SMTP → 503 明确报错（不静默失败）', r.status===503, 'status='+r.status);
r = await call('/api/send-receipt', { method:'POST', body:'{}' });
check('未登录调用 send-receipt → 401', r.status===401, 'status='+r.status);

console.log('\n=== 配置齐全时的出站闭环 ===');
const realFetch = globalThis.fetch;
env.EF_WA_TOKEN = 'test-meta-token';
env.EF_WA_PHONE_ID = '123456789';
globalThis.fetch = async () => new Response(JSON.stringify({ messages:[{ id:'wamid.out.1' }] }), {
  status:200, headers:{ 'Content-Type':'application/json' }
});
r = await call('/api/wa-send', {
  method:'POST', headers:{ Authorization: basic, 'Content-Type':'application/json' },
  body: JSON.stringify({ to:'+86 138 0013 8000', text:'Hello from Elite Travel', mid:'wamid.1' })
});
body = await r.json().catch(()=>({}));
check('POST /api/wa-send → 200 且返回 Meta messageId', r.status===200 && body.messageId==='wamid.out.1', JSON.stringify(body));
check('出站后原消息自动标记已回复', JSON.parse(kv.get('data:wa_inbox')).find(m=>m.mid==='wamid.1').status==='replied');
check('出站消息写入审计 outbox', JSON.parse(kv.get('data:wa_outbox')||'[]').length===1);
globalThis.fetch = realFetch;
delete env.EF_WA_TOKEN; delete env.EF_WA_PHONE_ID;

env.SMTP_HOST = 'smtp.example.com';
r = await call('/api/send-receipt', {
  method:'POST', headers:{ ...inq, 'Content-Type':'application/json' },
  body: JSON.stringify({ email:'guest@example.com', receiptNo:'R-1', paid:100 })
});
body = await r.json().catch(()=>({}));
check('配置 SMTP 后收据邮件发送成功', r.status===200 && body.ok && body.to==='guest@example.com', JSON.stringify(body));
r = await call('/api/snapshot', { method:'POST', headers:{ ...inq } });
body = await r.json().catch(()=>({}));
check('手动触发快照 → 200（x-api-secret 亦可）', r.status===200 && body.ok, JSON.stringify(body));
delete env.SMTP_HOST;

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

console.log('\n=== WhatsApp 询盘导入源（KV 只读）===');
kv.set('page:whatsapp-priority.json', JSON.stringify({ ok:true, inquiries:[
  { id:'waq_1', guest:'优先客人', contact:'+8613800000000', status:'following' }
] }));
kv.set('page:whatsapp-inquiries.json', JSON.stringify({ ok:true, inquiries:[
  { id:'waq_1', guest:'优先客人', contact:'+8613800000000', status:'following' },
  { id:'waq_2', guest:'历史客人', contact:'+8613900000000', status:'quoted' }
] }));
r = await call('/api/wa-inquiries');
check('未登录拉取 WA 询盘 → 401', r.status===401);
r = await call('/api/wa-inquiries', { headers:{ Authorization:basic } });
body = await r.json().catch(()=>({}));
check('Basic Auth 拉取优先清单 → 200', r.status===200 && body.inquiries.length===1, JSON.stringify(body).slice(0,120));
r = await call('/api/wa-inquiries', { headers:inq });
body = await r.json().catch(()=>({}));
check('x-api-secret 跨域拉取 → 200', r.status===200 && body.inquiries.length===1);
r = await call('/api/wa-inquiries?all=1', { headers:inq });
body = await r.json().catch(()=>({}));
check('?all=1 拉取全量 → 200', r.status===200 && body.inquiries.length===2);
kv.delete('page:whatsapp-priority.json');
r = await call('/api/wa-inquiries', { headers:inq });
check('KV 未上传时明确返回 404', r.status===404);

console.log(`\n通过 ${pass} / ${pass+fail}`+(fail?`  失败 ${fail}`:''));
process.exit(fail?1:0);
