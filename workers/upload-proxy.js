export default {
  async fetch(req, env) {
    if (req.method!== 'PUT') return new Response('Use PUT', {status:405})
    const url = new URL(req.url)
    const target = `https://s3.us.archive.org${url.pathname}`
    const headers = new Headers(req.headers)
    headers.set('Authorization', `LOW ${env.IA_ACCESS_KEY}:${env.IA_SECRET_KEY}`)
    headers.set('x-archive-auto-make-bucket', '1')
    return fetch(target, {method:'PUT', headers, body:req.body})
  }
}