// cwc-inquiry-proxy
// Hides chinawondercars.com inquiries admin key from the frontend.
// The workbench (elite-fleet) calls this proxy; the proxy holds the admin key
// and forwards to the site's D1 API.
const UPSTREAM = 'https://www.chinawondercars.com/api/inquiries'
const ADMIN_KEY = 'cwc-admin-2026'
const API_SECRET = 'ef-inq-2026'

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

export default {
  async fetch(request) {
    if (request.method === 'OPTIONS') {
      return new Response('', { headers: corsHeaders })
    }

    // Simple gate: the workbench sends x-api-secret. The real admin key never leaves this worker.
    if (request.headers.get('x-api-secret') !== API_SECRET) {
      return json({ ok: false, error: 'Forbidden' }, 403)
    }

    const url = new URL(request.url)

    try {
      // GET / -> list inquiries (limit/status passthrough)
      if (request.method === 'GET') {
        const limit = url.searchParams.get('limit') || '500'
        const status = url.searchParams.get('status')
        const q = new URLSearchParams({ key: ADMIN_KEY, limit })
        if (status) q.set('status', status)

        const up = await fetch(UPSTREAM + '?' + q.toString())
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
        console.log('[proxy] PUT id=' + id + ' bodyLen=' + bodyText.length)
        try {
          const up = await fetch(UPSTREAM + '/' + id, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'x-admin-key': ADMIN_KEY },
            body: bodyText
          })
          console.log('[proxy] PUT upstream status=' + up.status)
          const body = await up.text()
          return new Response(body, {
            status: up.status,
            headers: { 'Content-Type': 'application/json', ...corsHeaders }
          })
        } catch (e2) {
          console.log('[proxy] PUT upstream error: ' + e2.name + ' ' + e2.message)
          return json({ ok: false, error: 'upstream: ' + e2.name + ': ' + e2.message }, 502)
        }
      }

      return json({ ok: false, error: 'Method not allowed' }, 405)
    } catch (e) {
      return json({ ok: false, error: e.message }, 500)
    }
  }
}
