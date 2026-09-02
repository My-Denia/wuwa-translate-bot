import test from 'node:test';
import assert from 'node:assert/strict';
import { handleAcceptance, acceptancePage } from '../lib/owner-acceptance.js';
import { createPool, fixtureEnvironment } from './helpers/pool-fixture.mjs';
import { admitRequest } from '../lib/shared-pool.js';

const NOW = 1788307200;
const ORIGIN = 'https://acceptance-test.chatgpt.site';
function armed(db) { return { ...fixtureEnvironment(db), WUWATERM_ACCEPTANCE_MODE:'owner-only', WUWATERM_ACCEPTANCE_START:String(NOW-10), WUWATERM_ACCEPTANCE_UNTIL:String(NOW+100), WUWATERM_ACCEPTANCE_ORIGIN:ORIGIN }; }
function request(action, headers={}) { return new Request(ORIGIN+'/api/acceptance',{method:'POST',headers:{'content-type':'application/json',origin:ORIGIN,...headers},body:JSON.stringify({action})}); }

test('acceptance is default closed and bounded by mode, origin, indexing and time', async () => {
  for (const change of [
    {WUWATERM_ACCEPTANCE_MODE:undefined}, {WUWATERM_ACCEPTANCE_START:String(NOW+1)},
    {WUWATERM_ACCEPTANCE_UNTIL:String(NOW)}, {WUWATERM_ACCEPTANCE_UNTIL:String(NOW+601)},
    {WUWATERM_ACCEPTANCE_START:'1e9'}, {WUWATERM_PUBLIC_ORIGIN:ORIGIN}, {WUWATERM_ACCEPTANCE_ORIGIN:'http://example.invalid'},
  ]) {
    const db=createPool(); const env={...armed(db),...change}; let calls=0; db.prepare=()=>{calls++;throw Error('unexpected');};
    assert.equal((await handleAcceptance(request('reset'),env,NOW)).status,404);
    assert.equal((await acceptancePage(env,NOW)).status,404);
    assert.equal(calls,0);
  }
});

test('simple cross-site requests, malformed bodies and arbitrary actions never touch D1', async () => {
  const db=createPool();const env=armed(db);let calls=0;db.prepare=()=>{calls++;throw Error('unexpected');};
  for (const [req,status] of [
    [request('reset',{'content-type':'text/plain'}),415], [request('reset',{origin:'https://other.invalid'}),403],
    [new Request(ORIGIN+'/api/acceptance',{method:'POST',headers:{'content-type':'application/json'},body:'{"action":"reset"}'}),403],
    [request('reset',{origin:'null'}),403], [request('anything'),400],
    [new Request(ORIGIN+'/api/acceptance',{method:'POST',headers:{'content-type':'application/json',origin:ORIGIN},body:'{"action":"reset","sql":"ignored"}'}),400],
    [new Request(ORIGIN+'/api/acceptance',{method:'POST',headers:{'content-type':'application/json',origin:ORIGIN},body:'x'.repeat(513)}),413],
    [new Request(ORIGIN+'/api/acceptance',{method:'OPTIONS'}),405],
  ]) { const res=await handleAcceptance(req,env,NOW);assert.equal(res.status,status);assert.equal(res.headers.get('access-control-allow-origin'),null); }
  assert.equal(calls,0);
});

test('preset exhaustion preserves lookup counters and reset returns singleton to empty', async () => {
  const db=createPool();const env=armed(db);await admitRequest(env,'terms');const before=db.row();
  assert.equal((await handleAcceptance(request('exhaust_translation'),env,NOW)).status,200);
  assert.equal(db.row().terms_used,before.terms_used);assert.equal(db.row().meta_used,before.meta_used);
  assert.equal(db.row().translation_used,30);assert.equal(db.row().character_used,12000);
  assert.equal((await handleAcceptance(request('reset'),env,NOW)).status,200);assert.equal(db.count(),0);
  assert.equal((await handleAcceptance(request('reset'),env,NOW)).status,409);
});

test('D1 clock crossing the deadline prevents both writes atomically', async () => {
  for (const action of ['reset','exhaust_translation']) {
    const db=createPool();const env=armed(db);await admitRequest(env,'terms');const before=db.row();db.advance(101);
    assert.equal((await handleAcceptance(request(action),env,NOW)).status,409);
    assert.deepEqual(db.row(),before);
  }
});

test('commit-then-error is indeterminate, does not retry and never exposes raw failure', async () => {
  const db=createPool();const env=armed(db);await admitRequest(env,'terms');const original=db.prepare;let calls=0;
  db.prepare=(...args)=>{const stmt=original(...args);const first=stmt.first;stmt.first=async()=>{calls++;await first();throw Error('SYNTHETIC_PRIVATE_ERROR');};return stmt;};
  const res=await handleAcceptance(request('reset'),env,NOW);assert.equal(res.status,503);assert.equal(calls,1);assert.equal(db.count(),0);
  assert.equal((await res.text()).includes('SYNTHETIC_PRIVATE_ERROR'),false);
});

test('temporary operator page contains no secrets and prevents embedding or automatic replay', async () => {
  const env=armed(createPool());const res=await acceptancePage(env,NOW);const html=await res.text();
  assert.equal(res.status,200);assert.equal(res.headers.get('x-frame-options'),'DENY');
  assert.match(res.headers.get('content-security-policy'),/frame-ancestors 'none'/);
  assert.match(html,/application\/json/);assert.match(html,/\/api\/acceptance/);
  for (const forbidden of ['WUWATERM_',env.WUWATERM_SITE_DEVICE_TOKEN,env.WUWATERM_API_ALLOWED_HOST,'setInterval','localStorage']) assert.equal(html.includes(forbidden),false);
});
