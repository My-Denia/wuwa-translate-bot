import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { extname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const envNames = [
  'WUWATERM_API_BASE_URL',
  'WUWATERM_API_ALLOWED_HOST',
  'WUWATERM_SITE_DEVICE_TOKEN',
];
const canaries = ['SYNTHETIC_DEVICE_SENTINEL_74A1C9', 'not-a-real-service.invalid'];
const clientSources = [
  'app/components/meta-panel.tsx',
  'app/page.tsx',
  'app/layout.tsx',
  'app/robots.ts',
  'app/globals.css',
];
const forbiddenClientPatterns = [
  /localStorage/u,
  /sessionStorage/u,
  /indexedDB/u,
  /document\.cookie/u,
  /\bcaches\s*\./u,
  /\bgtag\s*\(/u,
  /\banalytics\b/iu,
];

for (const source of clientSources) {
  const text = readFileSync(join(root, source), 'utf8');
  for (const name of envNames) assert.equal(text.includes(name), false, `${source} references ${name}`);
  for (const pattern of forbiddenClientPatterns) assert.equal(pattern.test(text), false, `${source} matches ${pattern}`);
  assert.equal(/https?:\/\//u.test(text), false, `${source} contains an absolute network target`);
}

const clientComponent = readFileSync(join(root, 'app/components/meta-panel.tsx'), 'utf8');
assert.equal((clientComponent.match(/fetch\s*\(/gu) ?? []).length, 1, 'client must contain one fetch call');
assert.equal(clientComponent.includes("fetch('/api/meta'"), true, 'client fetch must be same-origin /api/meta');

const hostingText = readFileSync(join(root, '.openai/hosting.json'), 'utf8');
for (const name of envNames) assert.equal(hostingText.includes(name), false, `hosting.json contains ${name}`);
assert.deepEqual(Object.keys(JSON.parse(hostingText)).sort(), ['d1', 'project_id', 'r2']);

const scanExtensions = new Set(['.html', '.js', '.css', '.map', '.json']);
const emittedRoots = ['dist/client', 'dist/assets'];
for (const emittedRoot of emittedRoots) {
  const absolute = join(root, emittedRoot);
  if (!existsSync(absolute)) continue;
  for (const file of walk(absolute)) {
    if (!scanExtensions.has(extname(file))) continue;
    const text = readFileSync(file, 'utf8');
    for (const needle of [...envNames, ...canaries]) {
      assert.equal(text.includes(needle), false, `${relative(root, file)} exposes ${needle}`);
    }
  }
}

const rootHtml = join(root, 'dist/index.html');
if (existsSync(rootHtml)) {
  const text = readFileSync(rootHtml, 'utf8');
  for (const needle of [...envNames, ...canaries]) assert.equal(text.includes(needle), false);
}

for (const emittedRoot of emittedRoots) {
  const absolute = join(root, emittedRoot);
  if (!existsSync(absolute)) continue;
  for (const file of walk(absolute)) {
    assert.notEqual(extname(file), '.map', `${relative(root, file)} is an unexpected client source map`);
  }
}

console.log('verify:no-client-secret PASS');

function* walk(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) yield* walk(path);
    else if (entry.isFile() && !path.split(sep).includes('server')) yield path;
  }
}
