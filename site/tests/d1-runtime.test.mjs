import test from 'node:test';
import assert from 'node:assert/strict';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { fixtureEnvironment } from './helpers/pool-fixture.mjs';

test('real local Cloudflare D1 serializes concurrent Worker admissions and preserves only aggregate state', async () => {
  const bindings = fixtureEnvironment(null);
  delete bindings.DB;
  const workerOptions = { modulesRoot: fileURLToPath(new URL('../', import.meta.url)), modules: ['tests/helpers/runtime-worker.mjs', 'lib/wuwaterm-proxy.js', 'lib/shared-pool.js'].map(path => ({type: 'ESModule', path: fileURLToPath(new URL('../' + path, import.meta.url))})), compatibilityDate: '2026-08-27', compatibilityFlags: ['nodejs_compat'], d1Databases: { DB: 'shared-beta' }, bindings };
  const mf = new Miniflare(convertV4MiniflareOptions({ workers: [{ ...workerOptions, name: 'first' }, { ...workerOptions, name: 'second' }], cf: false }));
  try {
    const db = await mf.getD1Database('DB', 'first');
    const sql = readFileSync(new URL('../drizzle/0000_shared_pool.sql', import.meta.url), 'utf8');
    await db.prepare(sql).run();
    const other = await mf.getWorker('second');
    const results = await Promise.all(Array.from({ length: 40 }, (_, i) => (i % 2 ? other.fetch.bind(other) : mf.dispatchFetch)('http://site.test/api/' + (i % 2 ? 'terms?q=今汐' : 'meta'))));
    const admitted = results.filter(r => r.status === 200).length;
    assert.ok(admitted >= 1 && admitted <= 6);
    assert.ok(results.every(r => [200, 429].includes(r.status)));
    const row = await db.prepare('SELECT * FROM shared_pool').first();
    assert.equal(row.upstream_used, admitted);
    assert.equal(row.terms_used + row.meta_used, admitted);
    assert.equal(row.translation_used, 0);
    const table = await db.prepare('PRAGMA table_info(shared_pool)').all();
    assert.deepEqual(table.results.map(r => r.name), ['id','second_key','minute_key','day_key','upstream_used','translation_minute_used','terms_used','translation_used','character_used','meta_used']);
    assert.equal((await db.prepare('SELECT count(*) n FROM shared_pool').first()).n, 1);
    for (const r of results) {
      const body = await r.text();
      for (const secret of ['SYNTHETIC_PRODUCT_TOKEN_61E8','api.wuwaterm-test.net','Authorization','Bearer ']) assert.equal(body.includes(secret), false);
      if (r.status === 200) assert.equal(JSON.parse(body).request_id, 'synthetic-correlation-1');
    }
    // Test-only aggregate fixture: exhausted translation daily pool, open lookup.
    await db.prepare('UPDATE shared_pool SET second_key=0,minute_key=0,day_key=unixepoch()/86400,translation_used=30,character_used=12000').run();
    const denied = await mf.dispatchFetch('http://site.test/api/translations', { method: 'POST', body: JSON.stringify({ text: '今汐' }) });
    assert.equal(denied.status, 429);
    assert.equal((await denied.json()).reason, 'translation_pool_exhausted');
    const lookup = await mf.dispatchFetch('http://site.test/api/terms?q=今汐');
    assert.equal(lookup.status, 200);
    const status = await mf.dispatchFetch('http://site.test/api/pool');
    assert.equal((await status.json()).translations.remaining, 0);
  } finally { await mf.dispose(); }
});
