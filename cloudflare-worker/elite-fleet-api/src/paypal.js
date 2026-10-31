// paypal.js — PayPal Webhook → 飞书群机器人 收款提醒
// 路由：POST /api/paypal-webhook?token=pp-ef-2026-9f3a7c2e
// 流程：token 校验 → 只处理 PAYMENT.CAPTURE.COMPLETED / PAYMENT.SALE.COMPLETED 且状态已完成
//       → 按 event id 去重（KV data:pp_seen）→ 组装消息 → 飞书机器人（加签）推送
//
// 配置项（如需更换飞书机器人/密钥，修改下面三个常量后重新部署）：
const FEISHU_WEBHOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/bb7bc6d2-d7f4-4c84-8af4-1299c4937779';
const FEISHU_SECRET = 'ph3qsuH6TZQ4xR0B1ozWyh';      // 飞书机器人「安全设置-加签」密钥
const PP_WEBHOOK_TOKEN = 'pp-ef-2026-9f3a7c2e';      // 配置在 PayPal 后台 webhook URL 的 ?token=

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' }
  });

// 飞书加签：key = timestamp + "\n" + secret，HMAC-SHA256，空消息，base64
async function feishuSign(timestamp, secret) {
  const data = new TextEncoder().encode(timestamp + '\n' + secret);
  const key = await crypto.subtle.importKey(
    'raw', data, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, new ArrayBuffer(0));
  const bytes = new Uint8Array(sig);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

async function postFeishu(text) {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const sign = await feishuSign(timestamp, FEISHU_SECRET);
  const payload = { msg_type: 'text', content: { text }, timestamp, sign };
  const r = await fetch(FEISHU_WEBHOOK, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  let code = r.status;
  try { const j = await r.json(); if (j && j.code !== undefined) code = j.code; } catch (e) {}
  return code;
}

function fmtMoney(value, currency) {
  return (currency || '') + ' ' + (value == null ? '—' : String(value));
}

function bjTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}

// 从 v2 capture / v1 sale 资源中提取展示字段
function extractFields(type, resource) {
  if (type === 'PAYMENT.CAPTURE.COMPLETED') {
    const payer = resource.payer || {};
    const name = ((payer.name && (payer.name.given_name || '') + ' ' + (payer.name.surname || '')).trim()) || '';
    return {
      amount: fmtMoney(resource.amount && resource.amount.value, resource.amount && resource.amount.currency_code),
      payer: name || (payer.email_address || '') || (payer.payer_id || ''),
      email: payer.email_address || '',
      txn: resource.id || '',
      invoice: resource.invoice_id || resource.custom_id || '',
      time: resource.update_time || resource.create_time || ''
    };
  }
  if (type === 'PAYMENT.SALE.COMPLETED') {
    const info = (resource.payer && resource.payer.payer_info) || {};
    const name = ((info.first_name || '') + ' ' + (info.last_name || '')).trim();
    return {
      amount: fmtMoney(resource.amount && resource.amount.total, resource.amount && resource.amount.currency),
      payer: name || info.email || (resource.payer && resource.payer.payer_id) || '',
      email: info.email || '',
      txn: resource.id || '',
      invoice: resource.invoice_number || resource.custom || '',
      time: resource.update_time || resource.create_time || ''
    };
  }
  return null;
}

export async function handlePayPalWebhook(request, env) {
  if (request.method === 'GET') {
    return json({ ok: true, service: 'paypal-webhook', status: 'ready' });
  }
  if (request.method !== 'POST') {
    return json({ ok: false, error: 'method not allowed' }, 405);
  }

  const url = new URL(request.url);
  if (url.searchParams.get('token') !== PP_WEBHOOK_TOKEN) {
    return json({ ok: false, error: 'Forbidden' }, 403);
  }

  let event;
  try {
    event = await request.json();
  } catch (e) {
    return json({ ok: false, error: 'bad payload: ' + e.message }, 400);
  }

  const type = event.event_type || '';
  const resource = event.resource || {};
  const completed = type === 'PAYMENT.CAPTURE.COMPLETED' || type === 'PAYMENT.SALE.COMPLETED';
  if (!completed) {
    return json({ ok: true, ignored: type });
  }
  const state = (type === 'PAYMENT.CAPTURE.COMPLETED') ? resource.status : resource.state;
  if ((state || '').toLowerCase() !== 'completed') {
    return json({ ok: true, ignored: 'state:' + state });
  }

  // 去重：同 event id 只提醒一次
  const eventId = event.id || '';
  if (eventId) {
    const raw = await env.EF_DATA.get('data:pp_seen', 'text');
    const seen = raw ? JSON.parse(raw) : [];
    if (seen.includes(eventId)) {
      return json({ ok: true, dup: true, event: eventId });
    }
    seen.push(eventId);
    if (seen.length > 500) seen.shift();
    await env.EF_DATA.put('data:pp_seen', JSON.stringify(seen));
  }

  const f = extractFields(type, resource) || {};
  const lines = [
    '【PayPal 收款提醒】',
    '状态：已完成',
    '金额：' + f.amount,
    '付款人：' + (f.payer || '—'),
    '邮箱：' + (f.email || '—'),
    '交易号：' + (f.txn || '—'),
    '订单号：' + (f.invoice || '—'),
    '到账时间：' + bjTime(f.time) + '（北京时间）',
    '请到 PayPal 后台核对到账。'
  ];

  let feishu;
  try {
    feishu = await postFeishu(lines.join('\n'));
  } catch (e) {
    return json({ ok: false, error: 'feishu failed: ' + e.message, event: eventId }, 502);
  }
  return json({ ok: true, event: eventId, feishuCode: feishu });
}
