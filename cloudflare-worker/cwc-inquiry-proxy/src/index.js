// cwc-inquiry-proxy — chinawondercars.com 询盘接口 CORS 代理（遗留链路）
//
// 说明（2026-11-15）：
//  - 当前 elite-fleet 工作台走同源 /api/inquiries（elite-fleet-api Worker），不再依赖本代理。
//  - 本代理保留给 chinawondercars.com 站点侧旧链路使用；上游地址与密钥全部走 Worker Secret，
//    源码里不再出现任何真实凭据。部署前执行 `wrangler secret put`（变量清单见 elite-fleet-api/.dev.vars.example）。
//  - 工作区里那份「未提交的历史改动」已并入本文件：UPSTREAM 改为可配置，默认 /api/workbench，
//    并支持两套上游鉴权头（x-api-secret / x-admin-key）。
//  - ⚠️ 上游真实路径未在当前环境验证，故本版本只提交代码、不主动部署，避免打断线上询盘。
const DEFAULT_UPSTREAM = 'https://www.chinawondercars.com/api/workbench'

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, PUT, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, x-api-secret'
}

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders }
  })

function pcfg(env) {
  return {
    upstream: (env.CWC_UPSTREAM || DEFAULT_UPSTREAM).replace(/\/$/, ''),
    secret: env.EF_INQ_SECRET || '',
    adminKey: env.CWC_ADMIN_KEY || ''
  }
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response('', { headers: corsHeaders })
    }
    const cfg = pcfg(env)
    if (!cfg.secret) return json({ ok: false, error: 'EF_INQ_SECRET 未配置' }, 503)

    // Gate: the workbench sends x-api-secret.
    if (request.headers.get('x-api-secret') !== cfg.secret) {
      return json({ ok: false, error: 'Forbidden' }, 403)
    }

    const url = new URL(request.url)
    const upstreamHeaders = {
      'x-api-secret': cfg.secret,
      'User-Agent': 'cwc-inquiry-proxy/1.1',
      'Accept': 'application/json'
    }
    // 旧版 /api/inquiries 上游要求 x-admin-key（仅当显式配置时才发送）
    if (cfg.adminKey) upstreamHeaders['x-admin-key'] = cfg.adminKey

    try {
      // GET /?limit=500&status=new -> list inquiries
      if (request.method === 'GET') {
        const q = new URLSearchParams(url.search)
        // 上游是 /api/inquiries 时透传 key 参数；/api/workbench 用 header 鉴权
        if (cfg.adminKey) q.set('key', cfg.adminKey)
        const up = await fetch(cfg.upstream + '?' + q.toString(), {
          headers: upstreamHeaders,
          redirect: 'follow'
        })
        const body = await up.text()
        return new Response(body, {
          status: up.status,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        })
      }

      // PUT /:id -> update status/notes
      if (request.method === 'PUT') {
        const parts = url.pathname.split('/')
        const id = parts[parts.length - 1]
        if (!id || !/^\d+$/.test(id)) {
          return json({ ok: false, error: 'Invalid ID' }, 400)
        }
        const bodyText = await request.text()
        const up = await fetch(cfg.upstream + '/' + id, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', ...upstreamHeaders },
          body: bodyText,
          redirect: 'follow'
        })
        const body = await up.text()
        return new Response(body, {
          status: up.status,
          headers: { 'Content-Type': 'application/json', ...corsHeaders }
        })
      }

      return json({ ok: false, error: 'Method not allowed' }, 405)
    } catch (e) {
      return json({ ok: false, error: 'upstream: ' + (e.message || e) }, 502)
    }
  }
}
