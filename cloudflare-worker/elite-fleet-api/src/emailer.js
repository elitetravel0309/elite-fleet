// emailer.js — Elite Fleet 自动确认邮件模块
// SMTP 客户端（TLS）+ 确认邮件模板 + 已确认订单扫描发送
// 配置（wrangler.toml vars / 环境变量）：
//   SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM, MAIL_FROM_NAME

// ---------- 最小 SMTP over TLS 客户端（Cloudflare Workers sockets） ----------
async function smtpSend(env, to, subject, html) {
  const { connect } = require ? {} : {}; // placeholder (不会走到)
}

// 上面的 import 写法在 Workers 用 ESM import，单独文件由 index.js import：
export { sendConfirmationEmails, buildConfirmationEmail, smtpClient };

// 实际实现
const { connect } = await import('cloudflare:sockets'); // 顶层 await 不适用，改函数内

async function createSmtp(env) {
  const socket = connect({
    hostname: env.SMTP_HOST,
    port: Number(env.SMTP_PORT || 465),
    tls: true,
    allowHalfOpen: true
  });
  const reader = socket.readable.getReader();
  const writer = socket.writable.getWriter();
  const decoder = new TextDecoder();
  let buf = '';

  async function readLine() {
    while (!buf.includes('\n')) {
      const { value, done } = await reader.read();
      if (done) throw new Error('SMTP connection closed');
      buf += decoder.decode(value, { stream: true });
      if (buf.length > 65536) throw new Error('SMTP buffer overflow');
    }
    const idx = buf.indexOf('\n');
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    return line;
  }

  async function expect(code) {
    const resp = await readLine();
    if (!resp.startsWith(String(code))) throw new Error('SMTP expect ' + code + ' got: ' + resp);
    return resp;
  }

  async function cmd(line, code) {
    await writer.write(new TextEncoder().encode(line + '\r\n'));
    return expect(code);
  }

  return { reader, writer, cmd, close: () => { try { writer.close(); } catch (e) {} } };
}

async function smtpClient(env, to, subject, html) {
  const s = await createSmtp(env);
  try {
    await s.cmd('EHLO elite-fleet', 250);
    await s.cmd('AUTH LOGIN', 334);
    await s.cmd(btoa(env.SMTP_USER), 334);
    await s.cmd(btoa(env.SMTP_PASS), 235);
    await s.cmd('MAIL FROM:<' + env.MAIL_FROM + '>', 250);
    await s.cmd('RCPT TO:<' + to + '>', 250);
    await s.cmd('DATA', 354);
    const fromName = env.MAIL_FROM_NAME || 'China Wonder Tours';
    const msg = [
      'From: =?UTF-8?B?' + btoa(unescape(encodeURIComponent(fromName))) + '?= <' + env.MAIL_FROM + '>',
      'To: <' + to + '>',
      'Subject: =?UTF-8?B?' + btoa(unescape(encodeURIComponent(subject))) + '?=',
      'MIME-Version: 1.0',
      'Content-Type: text/html; charset=UTF-8',
      'Content-Transfer-Encoding: base64',
      '',
      btoa(unescape(encodeURIComponent(html))),
      '.'
    ].join('\r\n');
    await s.writer.write(new TextEncoder().encode(msg + '\r\n'));
    await s.expect(250);
    await s.cmd('QUIT', 221);
    return true;
  } finally {
    s.close();
  }
}

// ---------- 确认邮件模板 ----------
const WEEK = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];

function weekday(dateStr) {
  const d = new Date(dateStr + 'T00:00:00');
  return isNaN(d) ? '' : WEEK[d.getDay()];
}

function serviceLabel(o) {
  if (o.type === 'arrival') return 'Private Airport Transfer';
  if (o.type === 'departure') return 'Private Airport Transfer (Departure)';
  return 'Private Car & Driver (Full Day)';
}

function buildConfirmationEmail(o) {
  const date = o.date || '';
  const wd = weekday(date);
  const dateLine = date + (wd ? ' (' + wd + ')' : '');
  const guest = o.guest || 'Guest';
  const firstName = guest.split(' ')[0] || guest;

  const rows = [
    ['Service', serviceLabel(o)],
    ['Date', dateLine],
  ];
  if (o.time) rows.push(['Pick-up / Arrival time', o.time]);
  if (o.airport) rows.push(['Airport', o.airport]);
  if (o.flight) rows.push(['Flight', o.flight]);
  rows.push(['Pick-up', o.route ? o.route.split('→')[0].trim() : '']);
  rows.push(['Itinerary / Drop-off', o.route || '']);
  if (o.vehicle) rows.push(['Vehicle', o.vehicle]);
  if (o.pax) rows.push(['Passengers', String(o.pax)]);
  rows.push(['Status', 'CONFIRMED']);

  const price = Number(o.price) || 0;
  const collect = Number(o.collectAmount) || 0;
  const balance = price - collect;
  const priceRows = [
    ['Total price', price ? 'RMB ' + price.toLocaleString() : 'As quoted'],
  ];
  if (collect > 0) priceRows.push(['Paid (deposit/full)', 'RMB ' + collect.toLocaleString() + ' — PAID ✓']);
  if (balance > 0) priceRows.push(['Balance', 'RMB ' + balance.toLocaleString() + ' — payable on service day']);

  const table = (title, rowsArr) =>
    '<h3 style="margin:22px 0 10px;font-size:15px;color:#14263F">' + title + '</h3>' +
    '<table style="border-collapse:collapse;width:100%;max-width:620px;font-size:13.5px;color:#273043">' +
    rowsArr.map(r => '<tr><td style="padding:7px 12px;border:1px solid #E2E6EE;background:#F3F4F7;font-weight:600;width:38%;color:#14263F">' + (r[0]||'') + '</td><td style="padding:7px 12px;border:1px solid #E2E6EE">' + (r[1]||'') + '</td></tr>').join('') +
    '</table>';

  const included = ['Private vehicle and professional driver', 'Fuel, highway tolls, parking',
    'Waiting time as per itinerary (airport transfers include 90 min free waiting from actual flight landing)',
    'Fixed price — no additional charges'].map(x => '<li>' + x + '</li>').join('');

  const html =
    '<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:0 auto;color:#273043;font-size:14px;line-height:1.6">' +
    '<div style="background:linear-gradient(135deg,#0C1F3A,#9C2B2B);color:#fff;padding:26px 30px;border-radius:12px 12px 0 0">' +
    '<div style="font-size:21px;font-weight:700">China Wonder Tours</div>' +
    '<div style="font-size:12.5px;opacity:.85;letter-spacing:1px;margin-top:4px">BOOKING CONFIRMATION</div></div>' +
    '<div style="border:1px solid #E2E6EE;border-top:none;padding:28px 30px;border-radius:0 0 12px 12px">' +
    '<p>Dear ' + firstName + ',</p>' +
    '<p>Thank you for your booking! We are pleased to confirm your private service as follows:</p>' +
    table('Service Details', rows) +
    table('Price & Payment', priceRows) +
    '<h3 style="margin:22px 0 10px;font-size:15px;color:#14263F">Included in the price</h3>' +
    '<ul style="margin:0;padding-left:20px">' + included + '</ul>' +
    (o.note ? '<h3 style="margin:22px 0 10px;font-size:15px;color:#14263F">Good to know</h3><p style="margin:0;white-space:pre-wrap">' + o.note + '</p>' : '') +
    '<p style="margin:22px 0 0">If you need any help on the day, please reach us on WhatsApp +86 15347723823.</p>' +
    '<p style="margin:18px 0 0">Thank you for choosing China Wonder Tours!</p>' +
    '<p style="margin:22px 0 0;border-top:1px solid #E2E6EE;padding-top:16px">Kind regards,<br><b>Alice Li</b><br>China Wonder Cars / China Wonder Tours<br>E-mail: booking@chinawondertours.com<br>WhatsApp: +86 15347723823<br><a href="https://www.chinawondercars.com" style="color:#3E6FB0">www.chinawondercars.com</a> · <a href="https://www.chinawondertours.com" style="color:#3E6FB0">www.chinawondertours.com</a></p>' +
    '</div></div>';

  return { to: o.email || o.contact || '', subject: 'Booking Confirmation — ' + serviceLabel(o) + ' · ' + dateLine, html, firstName };
}

// ---------- 扫描已确认订单并发送 ----------
async function sendConfirmationEmails(env) {
  const raw = await env.EF_DATA.get('data:workbench', 'text');
  if (!raw) return { ok: false, error: 'no data' };
  const data = JSON.parse(raw);
  const orders = data.orders || [];
  const pending = orders.filter(o =>
    o.status === 'confirmed' && !o.emailSent && (o.email || o.contact) && /@/.test(o.email || o.contact || '')
  );
  const results = [];
  for (const o of pending) {
    try {
      const mail = buildConfirmationEmail(o);
      await smtpClient(env, mail.to, mail.subject, mail.html);
      o.emailSent = true;
      o.emailSentAt = new Date().toISOString();
      o.emailTo = mail.to;
      results.push({ id: o.id, to: mail.to, ok: true });
    } catch (e) {
      results.push({ id: o.id, to: o.email || o.contact, ok: false, error: String(e.message || e).slice(0, 200) });
    }
  }
  if (pending.length) {
    data._lastModified = Date.now();
    await env.EF_DATA.put('data:workbench', JSON.stringify(data));
  }
  return { ok: true, scanned: orders.length, pending: pending.length, sent: results.filter(r => r.ok).length, failed: results.filter(r => !r.ok).length, results };
}
