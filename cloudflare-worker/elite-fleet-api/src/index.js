// elite-fleet-api — Elite Travel 接送包车工作台 云端后端
// 单 Worker 提供：静态页面（index.html / quote-agent.html / inquiry-form.html）+ 数据 API（KV 存储）
// KV namespace: EF_DATA
//   - page:index.html        工作台主页面（需登录）
//   - page:quote-agent.html  报价智能体页面（公开）
//   - page:inquiry-form.html 客人用车需求表单页（公开）
//   - data:workbench         工作台数据（需登录读取；上传需密钥）
//   - data:inquiries         询盘列表（需登录或 x-api-secret）
//   - data:wa_inbox          WhatsApp 收件箱（webhook 写入，工作台读取）
//
// 访问控制：
//   - 工作台页面 /api/data  需要 HTTP Basic Auth
//   - quote-agent.html /api/public-data 公开（给客户看报价）
//   - inquiry-form.html /api/inquiry 公开（客人提交）
//   - /api/inquiries 接受 Basic Auth 或 x-api-secret（与 cwc-inquiry-proxy 保持一致）
//   - POST /api/upload 需要 ?key= 上传密钥（前端 cfKey）
//
// 安全约定（2026-11-15）：所有凭据一律从 Worker 环境变量 / Secret 读取，
// 源码与 git 仓库内不得再出现任何真实密钥。部署前按 .dev.vars.example 的变量清单执行 wrangler secret put。
// 本地开发可用 .dev.vars（已加入 .gitignore），参考 .dev.vars.example。
function cfg(env) {
  return {
    API_KEY: env.EF_UPLOAD_KEY || '',
    AUTH_USER: env.EF_AUTH_USER || 'elite',
    AUTH_PASS: env.EF_AUTH_PASS || '',
    INQ_SECRET: env.EF_INQ_SECRET || '',
    WA_VERIFY_TOKEN: env.EF_WA_VERIFY_TOKEN || '',
    TWILIO_TOKEN: env.EF_TWILIO_TOKEN || '',
    WA_TOKEN: env.EF_WA_TOKEN || '',
    WA_PHONE_ID: env.EF_WA_PHONE_ID || '',
    WA_API_VERSION: env.EF_WA_API_VERSION || 'v21.0'
  };
}

const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, x-api-secret, Authorization, x-ef-rev',
  'Access-Control-Expose-Headers': 'X-EF-Rev'
};

import { handlePayPalWebhook } from './paypal.js';
import { sendReceiptEmail, sendConfirmationEmails } from './emailer.js';

const json = (obj, status = 200, extra = {}) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', ...cors, ...extra }
  });

function checkAuth(request, env) {
  const c = cfg(env);
  // 未配置密码时一律拒绝，避免「空密码 = 空 Basic」被绕过
  if (!c.AUTH_PASS) return false;
  const h = request.headers.get('Authorization') || '';
  const expect = 'Basic ' + btoa(c.AUTH_USER + ':' + c.AUTH_PASS);
  return h === expect;
}

// 询盘类接口：Basic Auth 或 x-api-secret 均可（与 cwc-inquiry-proxy 保持同一套约定）
function checkInquiryAuth(request, env) {
  if (checkAuth(request, env)) return true;
  const c = cfg(env);
  return !!c.INQ_SECRET && (request.headers.get('x-api-secret') || '') === c.INQ_SECRET;
}

const unauthorized = () =>
  new Response('', {
    status: 401,
    headers: { 'WWW-Authenticate': 'Basic realm="Elite Fleet Workbench"', ...cors }
  });

const forbidden = () => json({ ok: false, error: 'Forbidden' }, 403);

// ---------- data:workbench 版本号（乐观锁） ----------
// KV 是 eventually consistent，用独立递增 rev 判断「是否有别的设备写过」。
// 上传方带上自己最后一次读到的 rev；不一致时服务端做字段级合并，避免整包覆盖丢数据。
const REV_KEY = 'meta:workbench_rev';
const BACKUP_INDEX_KEY = 'meta:backups';
const MERGE_ARRAYS = ['orders','drivers','fleets','prices','vehicles','vehicleGallery',
  'guests','payments','inquiries','driverRatings','hotels'];

async function readRev(env) {
  const v = await env.EF_DATA.get(REV_KEY, 'text');
  return parseInt(v || '0', 10) || 0;
}
async function bumpRev(env) {
  const next = (await readRev(env)) + 1;
  await env.EF_DATA.put(REV_KEY, String(next));
  return next;
}

function dedupById(arr) {
  const seen = new Set(), out = [];
  for (const x of arr || []) {
    if (!x || typeof x !== 'object') continue;
    const k = x.id ? 'id:' + String(x.id) : 'json:' + JSON.stringify(x);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(x);
  }
  return out;
}

// 以本次上传为基底（保留上传方的最新修改），把云端独有的记录按 id/内容并回来
function mergeWorkbench(cloud, incoming) {
  const merged = Object.assign({}, cloud, incoming);
  for (const key of MERGE_ARRAYS) {
    merged[key] = dedupById((incoming[key] || []).concat(cloud[key] || []));
  }
  merged._lastModified = Date.now();
  return merged;
}

// ---------- 每日 KV 快照（Cron Trigger） ----------
const SNAPSHOT_KEYS = ['data:workbench', 'data:inquiries', 'data:wa_inbox'];
async function takeSnapshot(env, label) {
  const day = (label || new Date().toISOString().slice(0, 10));
  const stamp = { day, at: Date.now(), keys: [] };
  for (const k of SNAPSHOT_KEYS) {
    const raw = await env.EF_DATA.get(k, 'text');
    if (raw == null) continue;
    const bk = 'backup:' + day + ':' + k;
    await env.EF_DATA.put(bk, raw);
    stamp.keys.push(bk);
  }
  if (!stamp.keys.length) return { ok: false, error: 'no data to snapshot' };
  const idxRaw = await env.EF_DATA.get(BACKUP_INDEX_KEY, 'text');
  let idx = idxRaw ? JSON.parse(idxRaw) : [];
  idx = idx.filter(x => x.day !== day).concat([stamp]);
  // 只保留最近 14 天
  const keep = new Set(idx.slice(-14).flatMap(x => x.keys));
  const old = idx.slice(0, -14);
  for (const s of old) for (const k of s.keys) await env.EF_DATA.delete(k);
  idx = idx.slice(-14);
  await env.EF_DATA.put(BACKUP_INDEX_KEY, JSON.stringify(idx));
  return { ok: true, day, keys: stamp.keys, retained: idx.length };
}

async function appendOutbox(env, entry) {
  const raw = await env.EF_DATA.get('data:wa_outbox', 'text');
  let out = raw ? JSON.parse(raw) : [];
  out.push(entry);
  if (out.length > 500) out = out.slice(-500);
  await env.EF_DATA.put('data:wa_outbox', JSON.stringify(out));
}

// WhatsApp 消息收件箱：追加结构化条目（按 message id 去重，上限 500 条）
async function appendInbox(env, body) {
  const raw = await env.EF_DATA.get('data:wa_inbox', 'text');
  let inbox = raw ? JSON.parse(raw) : [];
  const seen = new Set(inbox.map(i => i.mid));
  const now = Date.now();
  const entries = (body && body.entry) || [];
  for (const e of entries) {
    const changes = e.changes || [];
    for (const c of changes) {
      const v = c.value || {};
      const msgs = v.messages || [];
      for (const m of msgs) {
        if (seen.has(m.id)) continue;
        seen.add(m.id);
        const contact = (v.contacts || [])[0] || {};
        inbox.push({
          mid: m.id,
          wa_id: m.from || '',
          name: (contact.profile && contact.profile.name) || '',
          text: (m.text && m.text.body) || '',
          type: m.type || 'text',
          ts: (parseInt(m.timestamp || now / 1000, 10)) * 1000,
          receivedAt: now,
          status: 'new'
        });
      }
    }
  }
  if (inbox.length > 500) inbox = inbox.slice(-500);
  await env.EF_DATA.put('data:wa_inbox', JSON.stringify(inbox));
  return inbox;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS') {
      return new Response('', { status: 204, headers: cors });
    }
    const path = url.pathname.replace(/\/+$/, '') || '/';
    const c = cfg(env);

    // ---------- WhatsApp Webhook（公开，Meta 调用） ----------
    if (path === '/api/wa-webhook') {
      if (request.method === 'GET') {
        const mode = url.searchParams.get('hub.mode');
        const token = url.searchParams.get('hub.verify_token');
        const challenge = url.searchParams.get('hub.challenge');
        if (mode === 'subscribe' && !!c.WA_VERIFY_TOKEN && token === c.WA_VERIFY_TOKEN && challenge) {
          return new Response(challenge, {
            status: 200,
            headers: { 'Content-Type': 'text/plain', ...cors }
          });
        }
        return json({ ok: false, error: 'verify failed' }, 403);
      }
      if (request.method === 'POST') {
        try {
          const body = await request.json();
          const inbox = await appendInbox(env, body);
          return json({ ok: true, inbox: inbox.length, last: inbox.length ? inbox[inbox.length - 1] : null });
        } catch (e) {
          return json({ ok: false, error: 'bad payload: ' + e.message }, 400);
        }
      }
      return json({ ok: false, error: 'method not allowed' }, 405);
    }

    // ---------- Twilio WhatsApp Webhook（公开，Twilio 调用，需 ?token= 校验） ----------
    if (path === '/api/twilio-webhook') {
      if (request.method === 'GET') {
        return json({ ok: true, service: 'twilio-webhook', status: 'ready' });
      }
      if (request.method === 'POST') {
        const t = url.searchParams.get('token') || '';
        if (!c.TWILIO_TOKEN || t !== c.TWILIO_TOKEN) return json({ ok: false, error: 'Forbidden' }, 403);
        try {
          const ct = request.headers.get('Content-Type') || '';
          let from = '', body = '', sid = '', name = '';
          if (ct.includes('application/json')) {
            const p = await request.json();
            from = p.From || p.from || '';
            body = p.Body || p.body || '';
            sid = p.MessageSid || p.SmsMessageSid || '';
            name = p.ProfileName || p.profileName || '';
          } else {
            const form = await request.formData();
            from = form.get('From') || '';
            body = form.get('Body') || '';
            sid = form.get('MessageSid') || form.get('SmsMessageSid') || '';
            name = form.get('ProfileName') || '';
          }
          if (!from) return json({ ok: false, error: 'missing From' }, 400);
          const wa_id = from.replace(/^whatsapp:/, '').replace(/^wa:/, '');
          const now = Date.now();
          const raw = await env.EF_DATA.get('data:wa_inbox', 'text');
          let inbox = raw ? JSON.parse(raw) : [];
          const seen = new Set(inbox.map(i => i.mid));
          const mid = sid || ('tw-' + now + '-' + Math.random().toString(36).slice(2, 8));
          if (!seen.has(mid)) {
            inbox.push({
              mid,
              wa_id,
              name: name || '',
              text: body || '',
              type: 'text',
              ts: now,
              receivedAt: now,
              status: 'new'
            });
            if (inbox.length > 500) inbox = inbox.slice(-500);
            await env.EF_DATA.put('data:wa_inbox', JSON.stringify(inbox));
          }
          // Twilio 要求返回 TwiML 或 200
          return new Response('<Response></Response>', {
            status: 200,
            headers: { 'Content-Type': 'text/xml; charset=utf-8', ...cors }
          });
        } catch (e) {
          return json({ ok: false, error: 'bad payload: ' + e.message }, 400);
        }
      }
      return json({ ok: false, error: 'method not allowed' }, 405);
    }

    // ---------- PayPal Webhook（公开，PayPal 调用，需 ?token= 校验） ----------
    if (path === '/api/paypal-webhook') {
      return handlePayPalWebhook(request, env);
    }

    // ---------- 公开：报价智能体页面 ----------
    if (request.method === 'GET' && path === '/quote-agent.html') {
      const html = await env.EF_DATA.get('page:quote-agent.html', 'text');
      if (!html) return json({ ok: false, error: 'quote-agent.html 未上传到 KV' }, 404);
      return new Response(html, {
        status: 200,
        headers: { 'Content-Type': 'text/html; charset=utf-8', ...cors }
      });
    }

    // ---------- 公开：客人用车需求表单页 ----------
    if (request.method === 'GET' && (path === '/inquiry' || path === '/inquiry.html' || path === '/inquiry-form.html')) {
      const html = await env.EF_DATA.get('page:inquiry-form.html', 'text');
      if (!html) return json({ ok: false, error: 'inquiry-form.html 未上传到 KV' }, 404);
      return new Response(html, {
        status: 200,
        headers: { 'Content-Type': 'text/html; charset=utf-8', ...cors }
      });
    }

    // ---------- 公开：客人提交用车需求（POST /api/inquiry） ----------
    if (request.method === 'POST' && path === '/api/inquiry') {
      try {
        const body = await request.json();
        // 蜜罐：隐藏字段被填 → 视为机器人
        if (body._company || body._trap || body.company) {
          return json({ ok: true, received: true }); // 假成功，不落库
        }
        const name = String(body.name || '').trim();
        const whatsapp = String(body.whatsapp || '').trim();
        const email = String(body.email || '').trim();
        const serviceDate = String(body.service_date || '').trim();
        const city = String(body.city || '').trim();
        if (!name || name.length > 80) return json({ ok: false, error: '请填写姓名 / Name is required' }, 400);
        if (!whatsapp && !email) return json({ ok: false, error: '请至少填写 WhatsApp 或邮箱 / Please provide WhatsApp or email' }, 400);
        if (!serviceDate) return json({ ok: false, error: '请填写服务日期 / Service date is required' }, 400);
        // 简单限流：同 IP 60 秒内最多 1 条
        const ip = request.headers.get('CF-Connecting-IP') || request.headers.get('x-real-ip') || 'unknown';
        const lockKey = 'data:inq_lock:' + ip;
        const last = await env.EF_DATA.get(lockKey, 'text');
        const now = Date.now();
        if (last && (now - parseInt(last, 10)) < 60000) {
          return json({ ok: false, error: '提交过于频繁，请稍后再试 / Too many requests, please try later' }, 429);
        }
        const raw = await env.EF_DATA.get('data:inquiries', 'text');
        let inquiries = raw ? JSON.parse(raw) : [];
        const rid = 'inq_' + now + '_' + Math.random().toString(36).slice(2, 6);
        inquiries.push({
          id: rid,
          name, whatsapp, email,
          service_date: serviceDate,
          service_time: String(body.service_time || '').trim(),
          city,
          airport: String(body.airport || '').trim(),
          flight: String(body.flight || '').trim(),
          passengers: String(body.passengers || '').trim() || '1',
          luggage: String(body.luggage || '').trim(),
          vehicle: String(body.vehicle || '').trim(),
          service_type: String(body.service_type || 'general').trim(),
          pickup: String(body.pickup || '').trim(),
          destination: String(body.destination || '').trim(),
          message: String(body.message || '').trim(),
          source_page: 'inquiry-form · fleet.chinawondercars.com',
          status: 'new',
          receivedAt: now
        });
        if (inquiries.length > 1000) inquiries = inquiries.slice(-1000);
        await env.EF_DATA.put('data:inquiries', JSON.stringify(inquiries));
        await env.EF_DATA.put(lockKey, String(now));
        return json({ ok: true, received: true, id: rid });
      } catch (e) {
        return json({ ok: false, error: 'bad payload: ' + e.message }, 400);
      }
    }

    // ---------- 公开：报价页数据（仅价格库，不含订单/客户/司机敏感数据） ----------
    // 注意：quote-agent.html 是公开页面，绝不能在此返回订单（会泄露客人姓名/线路）
    // 运营侧的订单下拉走 /api/quote-orders（需 Basic Auth）
    if (request.method === 'GET' && path === '/api/public-data') {
      const raw = await env.EF_DATA.get('data:workbench', 'text');
      if (!raw) return json({ ok: false, error: '云端暂无数据' }, 404);
      try {
        const d = JSON.parse(raw);
        return json({ prices: Array.isArray(d.prices) ? d.prices : [] });
      } catch (e) {
        return json({ ok: false, error: '数据解析失败' }, 500);
      }
    }

    // ---------- 询盘列表（表单/Email/WhatsApp 桥接写入；工作台「同步网站询盘」拉取） ----------
    // 鉴权：Basic Auth 或 x-api-secret（与 cwc-inquiry-proxy 同一套约定）
    if (request.method === 'GET' && path === '/api/inquiries') {
      if (!checkInquiryAuth(request, env)) return forbidden();
      const raw = await env.EF_DATA.get('data:inquiries', 'text');
      const inquiries = raw ? JSON.parse(raw) : [];
      const limit = parseInt(url.searchParams.get('limit') || '0', 10);
      const list = (limit > 0 && inquiries.length > limit) ? inquiries.slice(-limit) : inquiries;
      return json({ ok: true, inquiries: list });
    }

    // ---------- 更新单条询盘状态（工作台「推送询价状态」回写） ----------
    if (request.method === 'PUT' && path.startsWith('/api/inquiries/')) {
      if (!checkInquiryAuth(request, env)) return forbidden();
      const id = decodeURIComponent(path.split('/')[3] || '');
      if (!id) return json({ ok: false, error: 'missing inquiry id' }, 400);
      let patch = {};
      try { patch = await request.json() || {}; } catch (e) { /* 允许空 body */ }
      const raw = await env.EF_DATA.get('data:inquiries', 'text');
      const inquiries = raw ? JSON.parse(raw) : [];
      let hit = false;
      const updated = inquiries.map(q => {
        if (String(q.id) === id) {
          hit = true;
          return { ...q, ...patch, id: q.id, updatedAt: Date.now() };
        }
        return q;
      });
      if (!hit) return json({ ok: false, error: 'inquiry not found' }, 404);
      await env.EF_DATA.put('data:inquiries', JSON.stringify(updated));
      const saved = updated.find(q => String(q.id) === id);
      return json({ ok: true, inquiry: saved });
    }

    // ---------- WhatsApp 收件箱：工作台读取（webhook 写入，此前只写不读） ----------
    if (path === '/api/wa-inbox' || path.startsWith('/api/wa-inbox/')) {
      if (!checkInquiryAuth(request, env)) return forbidden();
      if (request.method === 'GET' && path === '/api/wa-inbox') {
        const raw = await env.EF_DATA.get('data:wa_inbox', 'text');
        let inbox = raw ? JSON.parse(raw) : [];
        const limit = parseInt(url.searchParams.get('limit') || '100', 10);
        const onlyNew = url.searchParams.get('new') === '1';
        if (onlyNew) inbox = inbox.filter(i => i.status === 'new');
        const list = (limit > 0 && inbox.length > limit) ? inbox.slice(-limit) : inbox;
        const counts = {
          total: inbox.length,
          unread: inbox.filter(i => i.status === 'new').length,
          replied: inbox.filter(i => i.status === 'replied').length
        };
        return json({ ok: true, messages: list, counts });
      }
      if (request.method === 'POST') {
        // 标记已处理：POST /api/wa-inbox/:mid/status  body {status:'replied'}
        const parts = path.split('/');           // ['', 'api', 'wa-inbox', ':mid', 'status']
        const mid = decodeURIComponent(parts[3] || '');
        if (!mid || parts[3] === 'status') return json({ ok: false, error: 'missing message id' }, 400);
        let status = 'replied';
        try {
          const body = await request.json();
          if (body && body.status) status = String(body.status);
        } catch (e) { /* 允许空 body */ }
        const raw = await env.EF_DATA.get('data:wa_inbox', 'text');
        let inbox = raw ? JSON.parse(raw) : [];
        let hit = false;
        inbox = inbox.map(i => {
          if (i.mid === mid) { hit = true; return { ...i, status }; }
          return i;
        });
        if (!hit) return json({ ok: false, error: 'message not found' }, 404);
        await env.EF_DATA.put('data:wa_inbox', JSON.stringify(inbox));
        return json({ ok: true, mid, status });
      }
      return json({ ok: false, error: 'method not allowed' }, 405);
    }

    // ---------- 上传保存数据（前端 doUpload 协议：POST /api/upload?key=xxx，body JSON {data:S}） ----------
    // 鉴权用 ?key= 上传密钥，置于 Basic Auth 闸门之外：
    // 这样从 GitHub Pages 等静态站点跨设备上传也能成功（浏览器不会附带 Basic 凭据）
    if (request.method === 'POST' && path === '/api/upload') {
      const key = url.searchParams.get('key') || '';
      if (!c.API_KEY || key !== c.API_KEY) return json({ ok: false, error: 'Forbidden' }, 403);
      try {
        const text = await request.text();
        if (!text) return json({ ok: false, error: 'empty body' }, 400);
        const payload = JSON.parse(text);
        const data = payload && payload.data ? payload.data : payload;
        if (!data || !Array.isArray(data.orders)) {
          return json({ ok: false, error: '数据格式不对：缺少 orders 数组' }, 400);
        }
        const currentRev = await readRev(env);
        const rawBase = payload.baseVersion != null ? payload.baseVersion : request.headers.get('x-ef-rev');
        const baseVersion = (rawBase === null || rawBase === undefined || rawBase === '') ? NaN : Number(rawBase);
        const cloudRaw = await env.EF_DATA.get('data:workbench', 'text');
        let cloud = null;
        try { cloud = cloudRaw ? JSON.parse(cloudRaw) : null; } catch (e) { cloud = null; }
        // 客户端带上了 rev 且落后于云端 → 说明期间有别的设备写过：
        // 服务端按 id/内容合并（上传方修改为基底 + 云端独有记录追加），不再整包覆盖。
        const conflict = Number.isFinite(baseVersion) && baseVersion >= 0 && baseVersion !== currentRev && !!cloud;
        const finalData = conflict ? mergeWorkbench(cloud, data) : data;
        await env.EF_DATA.put('data:workbench', JSON.stringify(finalData));
        const rev = await bumpRev(env);
        return json({ ok: true, orders: finalData.orders.length, savedAt: Date.now(), rev, merged: conflict });
      } catch (e) {
        return json({ ok: false, error: 'bad payload: ' + e.message }, 400);
      }
    }

    // ---------- 内部运维端点：Basic Auth 或 x-api-secret 均可（GitHub Pages 跨域场景） ----------
    // ---------- WhatsApp 出站回复（Meta Cloud API；token / phone id 走 Secret） ----------
    // 工作台收件箱「回复」按钮调用；发送成功后自动把原消息标记为 replied，并留一条发件审计。
    if (request.method === 'POST' && path === '/api/wa-send') {
      if (!checkInquiryAuth(request, env)) return unauthorized();
      if (!c.WA_TOKEN || !c.WA_PHONE_ID) {
        return json({ ok: false, error: 'WhatsApp 发送未配置（EF_WA_TOKEN / EF_WA_PHONE_ID）' }, 503);
      }
      let body = {};
      try { body = await request.json(); } catch (e) { return json({ ok: false, error: 'bad payload' }, 400); }
      const to = String(body.to || '').replace(/[^\d]/g, '');
      const text = String(body.text || '').trim();
      if (!to || !text) return json({ ok: false, error: '缺少收件号码或消息内容' }, 400);
      if (text.length > 4000) return json({ ok: false, error: '消息过长（上限 4000 字）' }, 400);
      try {
        const api = 'https://graph.facebook.com/' + c.WA_API_VERSION + '/' + c.WA_PHONE_ID + '/messages';
        const up = await fetch(api, {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + c.WA_TOKEN, 'Content-Type': 'application/json' },
          body: JSON.stringify({ messaging_product: 'whatsapp', to, type: 'text', text: { body: text } })
        });
        const resp = await up.json().catch(() => ({}));
        if (!up.ok) return json({ ok: false, error: 'Meta API ' + up.status + ': ' + JSON.stringify(resp).slice(0, 300) }, 502);
        const mid = String(body.mid || '');
        if (mid) {
          const raw = await env.EF_DATA.get('data:wa_inbox', 'text');
          let inbox = raw ? JSON.parse(raw) : [];
          inbox = inbox.map(i => i.mid === mid ? { ...i, status: 'replied', repliedAt: Date.now() } : i);
          await env.EF_DATA.put('data:wa_inbox', JSON.stringify(inbox));
        }
        await appendOutbox(env, { at: Date.now(), to, text, mid, name: String(body.name || ''), provider: 'meta', messageId: (resp.messages && resp.messages[0] && resp.messages[0].id) || '' });
        return json({ ok: true, to, messageId: (resp.messages && resp.messages[0] && resp.messages[0].id) || '', replied: !!mid });
      } catch (e) {
        return json({ ok: false, error: '发送失败：' + String(e.message || e).slice(0, 200) }, 502);
      }
    }

    // ---------- 收款确认单邮件一键外发（SMTP 配置走 Secret） ----------
    if (request.method === 'POST' && path === '/api/send-receipt') {
      if (!checkInquiryAuth(request, env)) return unauthorized();
      let payload = {};
      try { payload = await request.json(); } catch (e) { return json({ ok: false, error: 'bad payload' }, 400); }
      const r = await sendReceiptEmail(env, payload);
      return json(r.ok ? r : { ok: false, error: r.error }, r.ok ? 200 : (r.code || 502));
    }

    // ---------- 已确认订单确认邮件批量补发（可选运维端点） ----------
    if (request.method === 'POST' && path === '/api/send-confirmations') {
      if (!checkInquiryAuth(request, env)) return unauthorized();
      const r = await sendConfirmationEmails(env);
      await bumpRev(env);
      return json(r);
    }

    // ---------- 云端每日快照：列表 / 下载 / 手动触发 ----------
    if (request.method === 'GET' && path === '/api/backups') {
      if (!checkInquiryAuth(request, env)) return unauthorized();
      const raw = await env.EF_DATA.get(BACKUP_INDEX_KEY, 'text');
      return json({ ok: true, backups: raw ? JSON.parse(raw) : [] });
    }
    if (request.method === 'GET' && path.startsWith('/api/backups/')) {
      if (!checkInquiryAuth(request, env)) return unauthorized();
      const key = decodeURIComponent(path.slice('/api/backups/'.length));
      if (!/^backup:\d{4}-\d{2}-\d{2}:data:/.test(key)) return json({ ok: false, error: 'bad backup key' }, 400);
      const raw = await env.EF_DATA.get(key, 'text');
      if (raw == null) return json({ ok: false, error: 'backup not found' }, 404);
      return new Response(raw, { status: 200, headers: { 'Content-Type': 'application/json; charset=utf-8', ...cors } });
    }
    if (request.method === 'POST' && path === '/api/snapshot') {
      if (!checkInquiryAuth(request, env)) return unauthorized();
      return json(await takeSnapshot(env));
    }


    // ---------- 以下全部需要登录 ----------
    if (!checkAuth(request, env)) return unauthorized();

    // ---------- 工作台主页面 ----------
    if (request.method === 'GET' && (path === '/' || path === '/index.html')) {
      const html = await env.EF_DATA.get('page:index.html', 'text');
      if (!html) return json({ ok: false, error: 'index.html 未上传到 KV' }, 404);
      return new Response(html, {
        status: 200,
        headers: { 'Content-Type': 'text/html; charset=utf-8', ...cors }
      });
    }

    // ---------- 读取工作台数据 ----------
    if (request.method === 'GET' && path === '/api/data') {
      const raw = await env.EF_DATA.get('data:workbench', 'text');
      if (!raw) return json({ ok: false, error: '云端暂无数据，请先在工作台点击「上云」' }, 404);
      const rev = await readRev(env);
      return new Response(raw, {
        status: 200,
        headers: { 'Content-Type': 'application/json; charset=utf-8', 'X-EF-Rev': String(rev), ...cors }
      });
    }

    // ---------- 报价智能体的订单下拉数据（需登录；quote-agent 公开页在已登录时自动带上） ----------
    if (request.method === 'GET' && path === '/api/quote-orders') {
      const raw = await env.EF_DATA.get('data:workbench', 'text');
      if (!raw) return json({ ok: false, error: '云端暂无数据' }, 404);
      try {
        const d = JSON.parse(raw);
        const orders = Array.isArray(d.orders) ? d.orders.map(o => ({
          id: o.id, date: o.date, time: o.time, route: o.route,
          vehicle: o.vehicle, pax: o.pax, type: o.type, guest: o.guest,
          quote: o.quote || o.price || 0, cost: o.cost || 0,
          city: o.city || '', status: o.status
        })) : [];
        return json({ ok: true, orders });
      } catch (e) {
        return json({ ok: false, error: '数据解析失败' }, 500);
      }
    }

    return json({ ok: false, error: 'Not Found' }, 404);
  },

  // 每天 UTC 18:00（北京时间次日 02:00）自动把三份业务数据快照到 KV，保留 14 天。
  async scheduled(event, env, ctx) {
    const r = await takeSnapshot(env);
    console.log('[snapshot]', JSON.stringify(r));
  }
};
