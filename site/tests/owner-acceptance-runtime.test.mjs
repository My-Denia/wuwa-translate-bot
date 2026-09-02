import test from 'node:test';
import assert from 'node:assert/strict';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

test('real local D1 applies only fixed acceptance UPDATE/DELETE and preserves schema', async () => {
  const now=Math.floor(Date.now()/1000);const origin='https://acceptance-test.chatgpt.site';
  const mf=new Miniflare(convertV4MiniflareOptions({
    modulesRoot:fileURLToPath(new URL('../',import.meta.url)),
    modules:['tests/helpers/acceptance-worker.mjs','lib/owner-acceptance.js','lib/shared-pool.js'].map(path=>({type:'ESModule',path:fileURLToPath(new URL('../'+path,import.meta.url))})),
    compatibilityDate:'2026-08-27',compatibilityFlags:['nodejs_compat'],d1Databases:['DB'],cf:false,
    bindings:{WUWATERM_ACCEPTANCE_MODE:'owner-only',WUWATERM_ACCEPTANCE_START:String(now-5),WUWATERM_ACCEPTANCE_UNTIL:String(now+115),WUWATERM_ACCEPTANCE_ORIGIN:origin},
  }));
  try {
    const db=await mf.getD1Database('DB');await db.prepare(readFileSync(new URL('../drizzle/0000_shared_pool.sql',import.meta.url),'utf8')).run();
    await db.prepare('INSERT INTO shared_pool VALUES(1,0,unixepoch()/60,unixepoch()/86400,2,0,1,0,0,1)').run();
    const post=action=>mf.dispatchFetch(origin+'/api/acceptance',{method:'POST',headers:{'content-type':'application/json',origin},body:JSON.stringify({action})});
    assert.equal((await post('exhaust_translation')).status,200);
    const row=await db.prepare('SELECT * FROM shared_pool').first();
    assert.equal(row.terms_used,1);assert.equal(row.meta_used,1);assert.equal(row.upstream_used,2);
    assert.equal(row.translation_used,30);assert.equal(row.character_used,12000);
    const page=await mf.dispatchFetch(origin+'/acceptance');assert.equal(page.status,200);assert.match(page.headers.get('content-security-policy'),/sha256-/);
    assert.equal((await post('reset')).status,200);
    assert.equal((await db.prepare('SELECT count(*) n FROM shared_pool').first()).n,0);
    assert.equal((await post('reset')).status,409);
    assert.equal((await db.prepare('PRAGMA table_info(shared_pool)').all()).results.length,10);
  } finally {await mf.dispose();}
});
