export default {
  async fetch(req) {
    const url = new URL(req.url)
    return fetch(`https://archive.org${url.pathname}`, {cf:{cacheTtl:86400}})
  }
}