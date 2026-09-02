'use client';

import { FormEvent, useEffect, useRef, useState } from 'react';

type Failure = { status: 'unavailable'; reason: string; request_id?: string; retry_after_seconds?: number };
type Allowance = { used: number; limit: number; remaining: number };
type Pool = { status: 'available'; translation_enabled: boolean; terms: Allowance; translations: Allowance; characters: Allowance; reset_at: string };
type TermMatch = { zh: string; en: string; category: string; reason: string; score: number };
type TermsResult = { query: string; matches: TermMatch[]; request_id: string };
type TranslationResult = { kind: 'noop' | 'exact' | 'fuzzy' | 'llm'; text: string; direction: 'en' | 'zh'; dictionary_miss: boolean; request_id: string };
type State<T> = { kind: 'idle' | 'loading' | 'cancelled' } | { kind: 'success'; data: T } | { kind: 'error'; error: Failure };
const FALLBACK: Failure = { status: 'unavailable', reason: 'site_response_invalid' };
const MESSAGES: Record<string, string> = {
  translation_disabled: '整句翻译暂未开放。你仍然可以查询术语。',
  translation_pool_exhausted: '今日整句翻译共享额度已用完。术语查询有独立额度，可以继续使用。',
  terms_pool_exhausted: '今日术语查询共享额度已用完，请在次日 UTC 00:00 后再来。',
  shared_pool_busy: '共享公测池正在忙碌，请稍后再试。',
  shared_pool_unavailable: '暂时无法确认共享额度，服务已暂停接收请求。请稍后重试。',
  site_invalid_request: '请检查输入：术语不超过 200 字符，整句不超过 2,000 字符。',
  site_request_too_large: '输入内容过大，请缩短后重试。',
  input_too_long: '文本过长，请分段翻译。',
  invalid_request: '输入格式有误，请修改后重试。',
  upstream_timeout: '等待超时。已获准的请求可能仍在处理，本次额度不会返还。',
  upstream_rate_limited: '服务繁忙，请稍后重试。',
  llm_unavailable: '整句翻译暂不可用，术语查询仍可尝试。',
  llm_budget_exhausted: '整句翻译暂时达到服务额度，术语查询仍可尝试。',
};
function failure(value: unknown): value is Failure { return !!value && typeof value === 'object' && (value as Failure).status === 'unavailable' && typeof (value as Failure).reason === 'string'; }
async function payload(r: Response): Promise<unknown> { try { return await r.json(); } catch { return FALLBACK; } }
function isTerms(v: unknown): v is TermsResult { const x = v as TermsResult; return !!x && typeof x.request_id === 'string' && Array.isArray(x.matches) && x.matches.every(m => typeof m.zh === 'string' && typeof m.en === 'string' && typeof m.category === 'string' && typeof m.reason === 'string' && typeof m.score === 'number'); }
function isTranslation(v: unknown): v is TranslationResult { const x = v as TranslationResult; return !!x && typeof x.text === 'string' && typeof x.request_id === 'string' && ['noop', 'exact', 'fuzzy', 'llm'].includes(x.kind) && ['en', 'zh'].includes(x.direction) && typeof x.dictionary_miss === 'boolean'; }
function isPool(v: unknown): v is Pool { const x = v as Pool; return !!x && x.status === 'available' && typeof x.translation_enabled === 'boolean' && [x.terms,x.translations,x.characters].every(a => a && Number.isInteger(a.remaining) && Number.isInteger(a.limit) && a.remaining >= 0 && a.limit >= a.remaining) && typeof x.reset_at === 'string'; }

export function TranslationWorkbench() {
  const [pool, setPool] = useState<State<Pool>>({ kind: 'loading' });
  const [poolAttempt, setPoolAttempt] = useState(0);
  const [query, setQuery] = useState('');
  const [terms, setTerms] = useState<State<TermsResult>>({ kind: 'idle' });
  const [source, setSource] = useState('');
  const [target, setTarget] = useState<'auto' | 'en' | 'zh'>('auto');
  const [translation, setTranslation] = useState<State<TranslationResult>>({ kind: 'idle' });
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const termsController = useRef<AbortController | null>(null);
  const translationController = useRef<AbortController | null>(null);
  const translationInput = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const r = await fetch('/api/pool', { cache: 'no-store', signal: controller.signal });
        const v = await payload(r);
        if (!controller.signal.aborted) setPool(r.ok && isPool(v) ? { kind: 'success', data: v } : { kind: 'error', error: failure(v) ? v : FALLBACK });
      } catch { if (!controller.signal.aborted) setPool({ kind: 'error', error: FALLBACK }); }
    })();
    return () => controller.abort();
  }, [poolAttempt]);
  useEffect(() => () => { termsController.current?.abort(); translationController.current?.abort(); }, []);

  async function lookup(event: FormEvent) {
    event.preventDefault(); if (!query.trim()) return;
    termsController.current?.abort();
    const controller = new AbortController(); termsController.current = controller;
    setTerms({ kind: 'loading' });
    try {
      const r = await fetch('/api/terms?q=' + encodeURIComponent(query.trim()), { cache: 'no-store', signal: controller.signal });
      const v = await payload(r);
      if (controller.signal.aborted || termsController.current !== controller) return;
      setTerms(r.ok && isTerms(v) ? { kind: 'success', data: v } : { kind: 'error', error: failure(v) ? v : FALLBACK });
      setPoolAttempt(n => n + 1);
    } catch { if (!controller.signal.aborted) setTerms({ kind: 'error', error: FALLBACK }); }
    finally { if (termsController.current === controller) termsController.current = null; }
  }
  async function translate(event: FormEvent) {
    event.preventDefault(); if (!source.trim()) return;
    translationController.current?.abort();
    const controller = new AbortController(); translationController.current = controller;
    setCopied(false); setCopyFailed(false); setTranslation({ kind: 'loading' });
    try {
      const r = await fetch('/api/translations', { method: 'POST', headers: { 'content-type': 'application/json' }, cache: 'no-store', body: JSON.stringify(target === 'auto' ? { text: source } : { text: source, to: target }), signal: controller.signal });
      const v = await payload(r);
      if (controller.signal.aborted || translationController.current !== controller) return;
      setTranslation(r.ok && isTranslation(v) ? { kind: 'success', data: v } : { kind: 'error', error: failure(v) ? v : FALLBACK });
      setPoolAttempt(n => n + 1);
    } catch { if (!controller.signal.aborted && translationController.current === controller) setTranslation({ kind: 'error', error: FALLBACK }); }
    finally { if (translationController.current === controller) translationController.current = null; }
  }
  function moveQueryToTranslation() {
    translationController.current?.abort(); translationController.current = null;
    setSource(query); setTranslation({ kind: 'idle' }); setCopied(false);
    translationInput.current?.focus();
  }
  function cancelTranslation() {
    translationController.current?.abort(); translationController.current = null;
    setTranslation({ kind: 'cancelled' }); setPoolAttempt(n => n + 1);
  }
  async function copyTranslation() {
    if (translation.kind !== 'success') return;
    try { await navigator.clipboard.writeText(translation.data.text); setCopied(true); setCopyFailed(false); }
    catch { setCopyFailed(true); }
  }
  const translationClosed = pool.kind === 'success' && (!pool.data.translation_enabled || pool.data.translations.remaining === 0 || pool.data.characters.remaining === 0);
  const sourceLength = Array.from(source).length;
  return <div className="workbench">
    <header className="workbench-header">
      <p className="eyebrow">WUTHERING WAVES · 中文 / ENGLISH</p>
      <h1>让每一个鸣潮术语，<span>准确抵达。</span></h1>
      <p>查找官方中英术语，翻译完整句子。字典优先，整句翻译保留术语。</p>
    </header>
    <aside className="pool-notice" aria-label="共享公测池说明">
      <span className="notice-symbol" aria-hidden="true">↗</span>
      <div><strong>共享公测池 · 先到先用</strong><p>所有人共用额度，一个访客也可能用完。没有个人或 IP 公平限额。</p></div>
      <a href="/limits">了解额度 <span aria-hidden="true">↗</span></a>
    </aside>
    <section className="pool-strip" aria-live="polite" aria-label="共享额度">
      {pool.kind === 'success' ? <>
        <div><span>今日术语查询</span><strong>{pool.data.terms.remaining}<small> / {pool.data.terms.limit} 次</small></strong></div>
        <div><span>今日整句翻译</span><strong>{pool.data.translation_enabled ? pool.data.translations.remaining : '暂未开放'}<small>{pool.data.translation_enabled ? ' / ' + pool.data.translations.limit + ' 次' : ''}</small></strong></div>
        <div><span>翻译字符余量</span><strong>{pool.data.characters.remaining.toLocaleString('zh-CN')}<small> 字符</small></strong></div>
        <p>UTC 00:00 重置（北京时间 08:00）。显示为快照，提交时重新核对。</p>
      </> : <p>{pool.kind === 'loading' ? '正在读取共享额度…' : '额度状态暂不可用，请稍后重试。'}</p>}
      <button className="text-button" type="button" onClick={() => setPoolAttempt(n => n + 1)}>刷新额度</button>
    </section>
    <div className="workspace-grid">
      <section className="workspace-card terms-card" aria-labelledby="terms-title">
        <div className="card-heading"><div><p className="section-kicker">01 / DICTIONARY</p><h2 id="terms-title">术语查询</h2></div><span className="tag">独立查询池</span></div>
        <p className="card-intro">角色、武器、声骸、地点。官方叫法，一查就懂。</p>
        <form onSubmit={lookup}>
          <label htmlFor="term-query">中文或英文术语</label>
          <div className="input-row"><input id="term-query" value={query} onChange={e => { setQuery(e.target.value); setTerms({ kind: 'idle' }); }} placeholder="例如：今汐 / Jinhsi" autoComplete="off" disabled={terms.kind === 'loading'} /><button type="submit" disabled={!query.trim() || Array.from(query.trim()).length > 200 || terms.kind === 'loading'}>{terms.kind === 'loading' ? '查询中…' : '查术语'}</button></div>
          <p className="field-hint">最多 200 字符 · 查询不消耗整句翻译额度</p>
        </form>
        <TermsView state={terms} onTranslate={moveQueryToTranslation} />
      </section>
      <section className="workspace-card translation-card" aria-labelledby="translation-title">
        <div className="card-heading"><div><p className="section-kicker">02 / TRANSLATE</p><h2 id="translation-title">整句翻译</h2></div><span className="tag">术语优先</span></div>
        <p className="card-intro">把想说的话译完整，让专有名词保持一致。</p>
        {translationClosed && <p className="notice-state">{pool.kind === 'success' && !pool.data.translation_enabled ? MESSAGES.translation_disabled : MESSAGES.translation_pool_exhausted}</p>}
        <form onSubmit={translate}>
          <div className="label-row"><label htmlFor="translation-source">待翻译文本</label><select aria-label="翻译方向" value={target} disabled={translation.kind === 'loading'} onChange={e => setTarget(e.target.value as 'auto' | 'en' | 'zh')}><option value="auto">自动方向</option><option value="en">中译英</option><option value="zh">英译中</option></select></div>
          <textarea ref={translationInput} id="translation-source" value={source} disabled={translation.kind === 'loading'} onChange={e => { setSource(e.target.value); setTranslation({ kind: 'idle' }); setCopied(false); }} placeholder="输入鸣潮相关的句子…" rows={5} />
          <div className="field-hint"><span>请勿输入敏感或个人信息</span><span aria-live="polite">{sourceLength.toLocaleString()} / 2,000</span></div>
          <div className="actions"><button type="submit" disabled={!source.trim() || sourceLength > 2000 || translation.kind === 'loading' || translationClosed}>{translation.kind === 'loading' ? '翻译中…' : '翻译整句'}</button>{translation.kind === 'loading' && <button className="secondary-button" type="button" onClick={cancelTranslation}>取消等待</button>}</div>
        </form>
        <TranslationView state={translation} copied={copied} copyFailed={copyFailed} onCopy={copyTranslation} />
      </section>
    </div>
    <div className="product-notes"><p><strong>字典优先</strong><span>优先使用官方中英对照，未命中时才尝试模型翻译。</span></p><p><strong>不保存历史</strong><span>结果仅在当前页面显示，刷新页面即清空。</span></p><p><strong>独立作品</strong><span>基于官方游戏术语，不是游戏官方运营的网站。</span></p></div>
  </div>;
}
function TermsView({ state, onTranslate }: { state: State<TermsResult>; onTranslate: () => void }) {
  if (state.kind === 'idle') return <div className="empty-state"><span aria-hidden="true">文 ⇄ A</span><p>从一个术语开始。</p><small>中英对照与匹配说明会显示在这里。</small></div>;
  if (state.kind === 'loading') return <p className="empty-state" role="status">正在查询术语…</p>;
  if (state.kind === 'error') return <FailureView value={state.error} />;
  if (state.kind !== 'success') return null;
  if (!state.data.matches.length) return <div className="empty-state"><p>暂未找到这个术语。</p><button className="text-button" type="button" onClick={onTranslate}>转到整句翻译</button></div>;
  return <div className="terms-results" aria-live="polite"><p className="result-summary">找到 {state.data.matches.length} 条匹配</p>{state.data.matches.map((m,i) => <article className="term-result" key={i}><div className="term-pair"><strong>{m.zh}</strong><span>{m.en}</span></div><dl><div><dt>类别</dt><dd>{m.category}</dd></div><div><dt>匹配方式</dt><dd>{m.reason}</dd></div><div><dt>匹配分数</dt><dd>{m.score}</dd></div></dl></article>)}<RequestId value={state.data.request_id} /></div>;
}
function TranslationView({ state, copied, copyFailed, onCopy }: { state: State<TranslationResult>; copied: boolean; copyFailed: boolean; onCopy: () => void }) {
  if (state.kind === 'idle') return <p className="translation-placeholder">译文会出现在这里。</p>;
  if (state.kind === 'loading') return <p className="notice-state" role="status">正在翻译，请稍候。取消仅停止本页等待。</p>;
  if (state.kind === 'cancelled') return <p className="notice-state" role="status">已停止本页等待；服务可能继续处理，已扣额度不会返还。</p>;
  if (state.kind === 'error') return <FailureView value={state.error} />;
  if (state.kind !== 'success') return null;
  return <div className="translation-result" aria-live="polite"><div className="result-meta"><span>{({ exact: '官方术语精确匹配', fuzzy: '术语近似匹配', noop: '无需转换', llm: '模型翻译' })[state.data.kind]}</span><span>{state.data.direction === 'en' ? '英文' : '中文'}</span></div>{state.data.dictionary_miss && <p className="field-hint">未命中字典，译文请结合上下文核对。</p>}<p className="translated-text">{state.data.text}</p><button type="button" className="secondary-button" onClick={onCopy}>{copied ? '已复制' : '复制译文'}</button>{copyFailed && <p role="status">复制未成功，请选择译文手动复制。</p>}<RequestId value={state.data.request_id} /></div>;
}
function FailureView({ value }: { value: Failure }) {
  return <div className="error-panel" role="status"><p>{MESSAGES[value.reason] ?? '服务暂时不可用，请稍后重试。'}</p>{value.retry_after_seconds !== undefined && <p>建议 {value.retry_after_seconds >= 3600 ? Math.ceil(value.retry_after_seconds / 3600) + ' 小时' : Math.ceil(value.retry_after_seconds) + ' 秒'}后重试。不会自动重发。</p>}{value.request_id && <RequestId value={value.request_id} />}</div>;
}
function RequestId({ value }: { value: string }) { return <details className="request-id"><summary>问题反馈编号</summary><code>{value}</code></details>; }
