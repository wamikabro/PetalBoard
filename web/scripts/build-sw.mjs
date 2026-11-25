// Runs after `astro build`: writes dist/sw.js, the service worker that keeps the whole app on the
// device so it opens without internet. The file list and a hash of every file go into the worker,
// so each new build installs a fresh copy and removes the old one.
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

const DIST = new URL('../dist/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const hash = createHash('sha256');
const urls = [];
for (const file of walk(DIST).sort()) {
  const rel = relative(DIST, file).split(sep).join('/');
  if (rel === 'sw.js' || rel === '404.html') continue;
  hash.update(rel).update(readFileSync(file));
  // Pages are served without .html (Cloudflare drops it): /bill, /daily, and / for index.
  if (rel.endsWith('.html')) urls.push(rel === 'index.html' ? '/' : '/' + rel.slice(0, -5));
  else urls.push('/' + rel);
}
const version = hash.digest('hex').slice(0, 12);
const template = readFileSync(new URL('./sw-template.js', import.meta.url), 'utf8');
writeFileSync(join(DIST, 'sw.js'),
  template.replace('__VERSION__', version).replace('__PRECACHE__', JSON.stringify(urls, null, 1)));
console.log(`sw.js: ${urls.length} files, version ${version}`);
