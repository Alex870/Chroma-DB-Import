import { createReadStream, existsSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { extname, join, normalize, resolve } from 'node:path'

const contentTypes: Record<string, string> = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
}

export default async function globalSetup() {
  const root = resolve(process.cwd(), 'dist')
  const server = createServer((request, response) => {
    const pathname = decodeURIComponent(new URL(request.url ?? '/', 'http://127.0.0.1').pathname)
    const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '')
    const candidate = normalize(join(root, relative))
    if (!candidate.startsWith(root) || !existsSync(candidate) || !statSync(candidate).isFile()) {
      response.writeHead(404).end()
      return
    }
    response.writeHead(200, { 'Content-Type': contentTypes[extname(candidate)] ?? 'application/octet-stream' })
    createReadStream(candidate).pipe(response)
  })
  await new Promise<void>((resolveListen) => server.listen(4173, '127.0.0.1', resolveListen))
  return async () => await new Promise<void>((resolveClose) => server.close(() => resolveClose()))
}
