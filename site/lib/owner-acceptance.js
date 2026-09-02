// Temporary owner-only Hosted acceptance support. Remove before final handoff.
// Authentication belongs to the verified Hosted ACL, not a visitor identifier.
import { POOL_LIMITS } from './shared-pool.js';

const HEADERS = { 'cache-control':'no-store', 'x-robots-tag':'noindex, nofollow, noarchive', 'x-frame-options':'DENY', 'referrer-policy':'no-referrer' };
function result(status, reason) { return Response.json({status:status===200?'ok':'unavailable',reason},{status,headers:HEADERS}); }
function windowConfig(env, now) {
  if (env?.WUWATERM_ACCEPTANCE_MODE !== 'owner-only' || env?.WUWATERM_PUBLIC_ORIGIN) return null;
  const startText=env.WUWATERM_ACCEPTANCE_START; const untilText=env.WUWATERM_ACCEPTANCE_UNTIL;
  if (typeof startText!=='string' || typeof untilText!=='string' || !/^\d{1,10}$/.test(startText) || !/^\d{1,10}$/.test(untilText)) return null;
  const start=Number(startText);const until=Number(untilText);
  if (start<=0 || until<=start || until-start>600 || now<start || now>=until) return null;
  try {
    const origin=new URL(env.WUWATERM_ACCEPTANCE_ORIGIN);
    if (origin.protocol!=='https:' || !origin.hostname.endsWith('.chatgpt.site') || origin.origin!==env.WUWATERM_ACCEPTANCE_ORIGIN) return null;
    return {start,until,origin:origin.origin};
  } catch { return null; }
}
async function readAction(request) {
  if (!request.body) return {status:400};
  const reader=request.body.getReader(); let timer; let total=0; const chunks=[];
  try {
    const raw=await Promise.race([
      (async()=>{
        while (true) { const {done,value}=await reader.read();if(done)break;total+=value.byteLength;if(total>512)throw Error('size');chunks.push(value); }
        const bytes=new Uint8Array(total);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
        return new TextDecoder('utf-8',{fatal:true}).decode(bytes);
      })(),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('body-timeout')),1000);}),
    ]);
    const value=JSON.parse(raw);
    if (!value || typeof value!=='object' || Array.isArray(value) || Object.keys(value).join()!=='action' || !['exhaust_translation','reset'].includes(value.action)) return {status:400};
    return {action:value.action};
  } catch { return {status:total>512?413:400}; }
  finally {clearTimeout(timer);try{void reader.cancel().catch(()=>{});reader.releaseLock();}catch{/* body already released */}}
}

export async function handleAcceptance(request, environment, now=Math.floor(Date.now()/1000)) {
  const armed=windowConfig(environment,now);if(!armed)return result(404,'not_found');
  if(request.method!=='POST')return result(405,'method_not_allowed');
  if((request.headers.get('content-type')??'').split(';')[0].trim().toLowerCase()!=='application/json')return result(415,'json_required');
  if(request.headers.get('origin')!==armed.origin)return result(403,'origin_rejected');
  const parsed=await readAction(request);if(!parsed.action)return result(parsed.status,'invalid_request');
  if(typeof environment.DB?.prepare!=='function')return result(503,'readback_required');
  try {
    // The DB clock is authoritative at execution, even after a slow body/query.
    // No automatic retry: an exception may follow a committed write.
    const row=parsed.action==='exhaust_translation'
      ? await environment.DB.prepare('UPDATE shared_pool SET translation_used=?1, character_used=?2 WHERE id=1 AND day_key=unixepoch()/86400 AND unixepoch()>=?3 AND unixepoch()<?4 RETURNING id').bind(POOL_LIMITS.translationsPerDay,POOL_LIMITS.charactersPerDay,armed.start,armed.until).first()
      : await environment.DB.prepare('DELETE FROM shared_pool WHERE id=1 AND unixepoch()>=?1 AND unixepoch()<?2 RETURNING id').bind(armed.start,armed.until).first();
    if(row===null)return result(409,'readback_required');
    if(row?.id!==1 || Object.keys(row).join()!=='id')return result(503,'readback_required');
    return result(200,'readback_required');
  } catch {return result(503,'readback_required');}
}

const SCRIPT = `const buttons=[...document.querySelectorAll('button')];for(const button of buttons)button.addEventListener('click',async()=>{for(const item of buttons)item.disabled=true;const message=document.querySelector('[role=status]');message.textContent='操作中，请勿重试。';try{const response=await fetch('/api/acceptance',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action:button.value}),cache:'no-store',credentials:'same-origin',redirect:'error'});const body=await response.json();if(!response.ok||body.status!=='ok')throw Error();message.textContent='操作已返回。先读回计数，再决定下一步；不要直接重试。';}catch{message.textContent='结果不确定：停止所有操作和产品请求，先读回计数。不要重试。';}});`;

export async function acceptancePage(environment, now=Math.floor(Date.now()/1000)) {
  if(!windowConfig(environment,now))return result(404,'not_found');
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(SCRIPT));
  const hash=btoa(String.fromCharCode(...new Uint8Array(digest)));
  return new Response(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WuwaTerm 验收操作</title><main><h1>私有验收操作</h1><p>仅供已确认 owner-only 的短时验收。操作前暂停其他产品请求；每次操作后先读回计数。</p><button type="button" value="exhaust_translation">置满翻译验收计数</button><button type="button" value="reset">清空验收计数</button><p role="status">请先确认访问范围与验收窗口。</p></main><script>${SCRIPT}</script></html>`,{headers:{...HEADERS,'content-type':'text/html; charset=utf-8','content-security-policy':`default-src 'none'; script-src 'sha256-${hash}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`}});
}
