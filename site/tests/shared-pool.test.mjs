import assert from 'node:assert/strict';
import test from 'node:test';
import { admitRequest, poolStatus, POOL_LIMITS } from '../lib/shared-pool.js';
import { createPool, fixtureEnvironment } from './helpers/pool-fixture.mjs';
import { proxyTermsRequest, proxyTranslationRequest, parseTermsRequest, parseTranslationRequest } from '../lib/wuwaterm-proxy.js';

test('one caller can exhaust translations; lookup retains its own allowance', async () => {
  const db = createPool(); const env = fixtureEnvironment(db);
  for (let i = 0; i < POOL_LIMITS.translationsPerDay; i++) {
    db.advance(61);
    assert.equal((await admitRequest(env, 'translations', 3)).ok, true);
  }
  db.advance(61);
  const refused = await admitRequest(env, 'translations', 3);
  assert.equal(refused.ok, false);
  assert.equal((await refused.response.json()).reason, 'translation_pool_exhausted');
  assert.equal((await admitRequest(env, 'terms')).ok, true);
  const status = await (await poolStatus(env)).json();
  assert.equal(status.terms.remaining, POOL_LIMITS.termsPerDay - 1);
  assert.equal(status.translations.remaining, 0);
});

test('shared counters atomically limit concurrent mixed requests with no partial debit', async () => {
  const db = createPool(); const env = fixtureEnvironment(db);
  const results = await Promise.all(Array.from({ length: 100 }, (_, i) => admitRequest(env, i % 2 ? 'terms' : 'meta')));
  assert.equal(results.filter(x => x.ok).length, 1);
  for (let i = 1; i < POOL_LIMITS.upstreamPerMinute; i++) { db.advance(1); assert.equal((await admitRequest(env, 'terms')).ok, true); }
  db.advance(1);
  assert.equal((await admitRequest(env, 'terms')).ok, false);
  assert.equal(db.row().upstream_used, POOL_LIMITS.upstreamPerMinute);
  assert.equal(db.row().terms_used + db.row().meta_used, POOL_LIMITS.upstreamPerMinute);
});

test('independent UTC rollover and character boundary do not leave counter history', async () => {
  const db = createPool(); const env = fixtureEnvironment(db);
  for (let i = 0; i < POOL_LIMITS.charactersPerDay / 2000; i++) { db.advance(61); assert.equal((await admitRequest(env, 'translations', 2000)).ok, true); }
  db.advance(61);
  const denied = await admitRequest(env, 'translations', 1);
  assert.equal((await denied.response.json()).reason, 'translation_pool_exhausted');
  assert.equal((await admitRequest(env, 'terms')).ok, true);
  db.advance(86400);
  assert.equal((await admitRequest(env, 'translations', 2000)).ok, true);
  assert.equal(db.row().translation_used, 1);
  assert.equal(db.row().terms_used, 0);
  assert.equal(db.count(), 1);
});

test('missing DB, disabled translation, invalid policy, corrupt and ambiguous DB fail closed', async () => {
  let calls = 0;
  for (const DB of [undefined, { prepare() { throw Error('SYNTHETIC_PRIVATE_DETAIL'); } }, { prepare() { return { bind() { return this; }, first: async () => ({ upstream_used: -1 }) }; } }]) {
    const env = { ...fixtureEnvironment(createPool()), DB };
    const r = await proxyTermsRequest({ environment: env, query: '今汐', fetchImpl: async () => { calls++; } });
    assert.equal(r.status, 503); assert.equal((await r.text()).includes('SYNTHETIC_PRIVATE_DETAIL'), false);
  }
  const db = createPool(); const env = fixtureEnvironment(db);
  env.WUWATERM_TRANSLATION_ENABLED = 'false';
  assert.equal((await admitRequest(env, 'translations', 3)).response.status, 503);
  assert.equal(db.count(), 0);
  assert.equal((await admitRequest(env, 'terms')).ok, true);
  env.WUWATERM_SHARED_POOL_ENABLED = 'yes';
  assert.equal((await admitRequest(env, 'terms')).ok, false);
  assert.equal(calls, 0);
});

test('upstream failure and commit-then-error consume a reservation without retry/refund', async () => {
  const db = createPool(); const env = fixtureEnvironment(db); let calls = 0;
  const r = await proxyTranslationRequest({ environment: env, input: { text: '今汐' }, fetchImpl: async () => { calls++; throw Error('network'); } });
  assert.equal(r.status, 502); assert.equal(calls, 1); assert.equal(db.row().translation_used, 1);
  db.advance(61);
  const original = db.prepare;
  db.prepare = (...args) => { const stmt = original(...args); const first = stmt.first; stmt.first = async () => { await first(); throw Error('uncertain'); }; return stmt; };
  const r2 = await proxyTranslationRequest({ environment: env, input: { text: '今汐' }, fetchImpl: async () => { calls++; } });
  assert.equal(r2.status, 503); assert.equal(calls, 1); assert.equal(db.row().translation_used, 2);
});

test('slow D1 never dispatches even if the reservation later commits', async () => {
  const db = createPool(); const env = fixtureEnvironment(db); const original = db.prepare; let calls = 0;
  db.prepare = (...args) => { const stmt = original(...args); const first = stmt.first; stmt.first = async () => { await new Promise(r => setTimeout(r, 1100)); return first(); }; return stmt; };
  const r = await proxyTermsRequest({ environment: env, query: '今汐', fetchImpl: async () => { calls++; } });
  assert.equal(r.status, 503);
  await new Promise(r => setTimeout(r, 200));
  assert.equal(calls, 0); assert.equal(db.row().terms_used, 1);
});

test('Unicode scalar and body ceilings reject malformed or oversized input before admission', async () => {
  const request = text => new Request('http://site.test/api/translations', { method: 'POST', headers: { 'content-type':'application/json' }, body: JSON.stringify({text}) });
  assert.equal((await parseTranslationRequest(request('😀'.repeat(2000)))).ok, true);
  for (const text of ['😀'.repeat(2001), '\ud800', '\udc00']) assert.equal((await parseTranslationRequest(request(text))).ok, false);
  assert.equal(parseTermsRequest(new Request('http://site.test/api/terms?q='+encodeURIComponent('😀'.repeat(200)))).ok, true);
  assert.equal(parseTermsRequest(new Request('http://site.test/api/terms?q='+encodeURIComponent('😀'.repeat(201)))).ok, false);
  const tooLarge = await parseTranslationRequest(new Request('http://site.test/api/translations',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:'x'.repeat(32769)})}));
  assert.equal(tooLarge.response.status, 413);
});

test('one caller can exhaust lookup independently without closing translation', async () => {
  const db = createPool(); const env = fixtureEnvironment(db);
  for (let i=0;i<POOL_LIMITS.termsPerDay;i++) { db.advance(61); assert.equal((await admitRequest(env,'terms')).ok,true); }
  db.advance(61);
  const denied=await admitRequest(env,'terms');
  assert.equal((await denied.response.json()).reason,'terms_pool_exhausted');
  assert.equal((await admitRequest(env,'translations',2)).ok,true);
});

test('independent callers share the same store and fixed window rollover has bounded headroom', async () => {
  const db=createPool(); const first=fixtureEnvironment(db); const second=fixtureEnvironment(db);
  for (let i=0;i<6;i++) { assert.equal((await admitRequest(i%2?first:second,'terms')).ok,true); db.advance(1); }
  assert.equal((await admitRequest(first,'meta')).ok,false);
  db.advance(54);
  assert.equal((await admitRequest(second,'meta')).ok,true);
  assert.equal(db.row().upstream_used,1);
  assert.ok(3*POOL_LIMITS.upstreamPerMinute < 30);
});
