'use client';

import { FormEvent, useEffect, useRef, useState } from 'react';

type Failure = { status: 'unavailable'; reason: string; request_id?: string };
type Meta = {
  api_version: string;
  service_version: string;
  schema_version: string | null;
  source_profile: string | null;
  source_commit: string | null;
  term_count: number;
  llm_configured: boolean;
  request_id: string;
};
type TermMatch = { zh: string; en: string; category: string; reason: string; score: number };
type TermsResult = { query: string; matches: TermMatch[]; request_id: string };
type TranslationResult = {
  kind: 'noop' | 'exact' | 'fuzzy' | 'llm';
  text: string;
  direction: 'en' | 'zh';
  dictionary_miss: boolean;
  request_id: string;
};
type ResultState<T> =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'success'; data: T }
  | { kind: 'error'; error: Failure }
  | { kind: 'cancelled' };

const FALLBACK_FAILURE: Failure = { status: 'unavailable', reason: 'site_response_invalid' };
const FAILURE_MESSAGES: Record<string, string> = {
  site_not_configured: '站点尚未完成服务配置，请联系管理员。',
  site_invalid_request: '输入格式无法提交，请检查后重试。',
  site_request_too_large: '输入内容过大，请缩短后重试。',
  upstream_timeout: '服务响应超时，请稍后重试。',
  upstream_unauthorized: '站点凭据无效，请联系管理员。',
  upstream_forbidden: '站点凭据权限不足，请联系管理员。',
  upstream_rate_limited: '请求过于频繁，请稍后重试。',
  upstream_unavailable: '服务暂时不可用，请稍后重试。',
  upstream_invalid_content_type: '上游响应格式异常，请联系管理员。',
  upstream_response_too_large: '上游响应超出安全限制，请联系管理员。',
  upstream_invalid_json: '上游返回了无法解析的响应，请联系管理员。',
  upstream_schema_mismatch: '上游响应不符合当前 API 合同，请联系管理员。',
  upstream_network_error: '无法连接翻译服务，请稍后重试。',
  invalid_request: '输入不符合翻译服务要求，请修改后重试。',
  payload_too_large: '输入内容过大，请缩短后重试。',
  input_too_long: '文本过长，请分段翻译。',
  llm_unavailable: '整句翻译模型暂时不可用，请稍后重试。',
  llm_budget_exhausted: '整句翻译额度暂时用尽，请稍后重试。',
  site_response_invalid: '本站收到了无法识别的响应，请稍后重试。',
};

function isFailure(value: unknown): value is Failure {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<Failure>;
  return item.status === 'unavailable' && typeof item.reason === 'string';
}

function isMeta(value: unknown): value is Meta {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<Meta>;
  return typeof item.api_version === 'string'
    && typeof item.service_version === 'string'
    && (typeof item.schema_version === 'string' || item.schema_version === null)
    && (typeof item.source_profile === 'string' || item.source_profile === null)
    && (typeof item.source_commit === 'string' || item.source_commit === null)
    && Number.isInteger(item.term_count)
    && typeof item.llm_configured === 'boolean'
    && typeof item.request_id === 'string';
}

function isTermsResult(value: unknown): value is TermsResult {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<TermsResult>;
  return typeof item.query === 'string'
    && typeof item.request_id === 'string'
    && Array.isArray(item.matches)
    && item.matches.every((match) => match && typeof match === 'object'
      && typeof match.zh === 'string'
      && typeof match.en === 'string'
      && typeof match.category === 'string'
      && typeof match.reason === 'string'
      && typeof match.score === 'number');
}

function isTranslationResult(value: unknown): value is TranslationResult {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<TranslationResult>;
  return ['noop', 'exact', 'fuzzy', 'llm'].includes(item.kind ?? '')
    && typeof item.text === 'string'
    && (item.direction === 'en' || item.direction === 'zh')
    && typeof item.dictionary_miss === 'boolean'
    && typeof item.request_id === 'string';
}

async function responsePayload(response: Response): Promise<unknown> {
  try { return await response.json(); } catch { return FALLBACK_FAILURE; }
}

export function TranslationWorkbench() {
  const [meta, setMeta] = useState<ResultState<Meta>>({ kind: 'loading' });
  const [metaAttempt, setMetaAttempt] = useState(0);
  const [query, setQuery] = useState('');
  const [terms, setTerms] = useState<ResultState<TermsResult>>({ kind: 'idle' });
  const [source, setSource] = useState('');
  const [target, setTarget] = useState<'auto' | 'en' | 'zh'>('auto');
  const [translation, setTranslation] = useState<ResultState<TranslationResult>>({ kind: 'idle' });
  const [copied, setCopied] = useState(false);
  const translationController = useRef<AbortController | null>(null);
  const translationInput = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/meta', {
      headers: { accept: 'application/json' },
      cache: 'no-store',
      signal: controller.signal,
    }).then(async (response) => ({ response, payload: await responsePayload(response) }))
      .then(({ response, payload }) => {
        if (response.ok && isMeta(payload)) setMeta({ kind: 'success', data: payload });
        else setMeta({ kind: 'error', error: isFailure(payload) ? payload : FALLBACK_FAILURE });
      })
      .catch(() => {
        if (!controller.signal.aborted) setMeta({ kind: 'error', error: FALLBACK_FAILURE });
      });
    return () => controller.abort();
  }, [metaAttempt]);

  function retryMeta() {
    setMeta({ kind: 'loading' });
    setMetaAttempt((value) => value + 1);
  }

  async function lookup(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setTerms({ kind: 'loading' });
    try {
      const response = await fetch(`/api/terms?q=${encodeURIComponent(query)}`, {
        headers: { accept: 'application/json' },
        cache: 'no-store',
      });
      const payload = await responsePayload(response);
      if (response.ok && isTermsResult(payload)) setTerms({ kind: 'success', data: payload });
      else setTerms({ kind: 'error', error: isFailure(payload) ? payload : FALLBACK_FAILURE });
    } catch {
      setTerms({ kind: 'error', error: FALLBACK_FAILURE });
    }
  }

  async function translate(event: FormEvent) {
    event.preventDefault();
    if (!source.trim()) return;
    translationController.current?.abort();
    const controller = new AbortController();
    translationController.current = controller;
    setCopied(false);
    setTranslation({ kind: 'loading' });
    const body = target === 'auto' ? { text: source } : { text: source, to: target };
    try {
      const response = await fetch('/api/translations', {
        method: 'POST',
        headers: { accept: 'application/json', 'content-type': 'application/json' },
        cache: 'no-store',
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      const payload = await responsePayload(response);
      if (controller.signal.aborted || translationController.current !== controller) return;
      if (response.ok && isTranslationResult(payload)) {
        setTranslation({ kind: 'success', data: payload });
      } else {
        setTranslation({ kind: 'error', error: isFailure(payload) ? payload : FALLBACK_FAILURE });
      }
    } catch {
      if (!controller.signal.aborted && translationController.current === controller) {
        setTranslation({ kind: 'error', error: FALLBACK_FAILURE });
      }
    } finally {
      if (translationController.current === controller) translationController.current = null;
    }
  }

  function cancelTranslation() {
    translationController.current?.abort();
    translationController.current = null;
    setTranslation({ kind: 'cancelled' });
  }

  async function copyTranslation() {
    if (translation.kind !== 'success') return;
    try {
      await navigator.clipboard.writeText(translation.data.text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  function moveQueryToTranslation() {
    translationController.current?.abort();
    translationController.current = null;
    setSource(query);
    setTranslation({ kind: 'idle' });
    translationInput.current?.focus();
  }

  return (
    <div className="workbench">
      <header className="workbench-header">
        <div>
          <p className="eyebrow">OWNER PRIVATE · PRODUCT V1</p>
          <h1>可信术语与整句翻译，<span>一个工作台。</span></h1>
          <p>浏览器只连接本站同源 API；查询、方向、排序与翻译决策均由 WuwaTerm 服务完成。</p>
        </div>
        <span className="private-badge"><span aria-hidden="true" />Owner only</span>
      </header>

      <section className="status-strip" aria-live="polite">
        <StatusContent state={meta} onRetry={retryMeta} />
      </section>

      <div className="workspace-grid">
        <section className="workspace-card terms-card" aria-labelledby="terms-title">
          <div className="card-heading">
            <div><p className="section-kicker">TERM LOOKUP</p><h2 id="terms-title">术语查询</h2></div>
            <span>中文 ⇄ English</span>
          </div>
          <form onSubmit={lookup} className="lookup-form">
            <label htmlFor="term-query">输入中文或英文术语</label>
            <div className="input-row">
              <input id="term-query" value={query} onChange={(event) => {
                setQuery(event.target.value);
                if (terms.kind !== 'loading') setTerms({ kind: 'idle' });
              }} placeholder="例如：今汐 / Suisui" autoComplete="off" disabled={terms.kind === 'loading'} />
              <button type="submit" disabled={!query.trim() || terms.kind === 'loading'}>{terms.kind === 'loading' ? '查询中…' : '查询'}</button>
            </div>
          </form>
          <p className="guidance">需要翻译长句？直接使用整句翻译；本站不会自行猜测或裁剪术语结果。</p>
          <TermsView state={terms} onTranslate={moveQueryToTranslation} />
        </section>

        <section className="workspace-card translation-card" aria-labelledby="translation-title">
          <div className="card-heading">
            <div><p className="section-kicker">SENTENCE TRANSLATION</p><h2 id="translation-title">整句翻译</h2></div>
            <select aria-label="翻译方向" value={target} disabled={translation.kind === 'loading'} onChange={(event) => setTarget(event.target.value as 'auto' | 'en' | 'zh')}>
              <option value="auto">自动方向</option>
              <option value="en">中译英</option>
              <option value="zh">英译中</option>
            </select>
          </div>
          <form onSubmit={translate}>
            <label htmlFor="translation-source">待翻译文本</label>
            <textarea ref={translationInput} id="translation-source" value={source} disabled={translation.kind === 'loading'} onChange={(event) => {
              setSource(event.target.value);
              setTranslation({ kind: 'idle' });
            }} placeholder="输入完整句子…" rows={7} />
            <div className="actions">
              <button type="submit" disabled={!source.trim() || translation.kind === 'loading'}>{translation.kind === 'loading' ? '翻译中…' : '翻译'}</button>
              {translation.kind === 'loading' && <button className="secondary-button" type="button" onClick={cancelTranslation}>取消等待</button>}
            </div>
          </form>
          <TranslationView state={translation} copied={copied} onCopy={copyTranslation} />
        </section>
      </div>
    </div>
  );
}

function StatusContent({ state, onRetry }: { state: ResultState<Meta>; onRetry: () => void }) {
  if (state.kind === 'loading') return <p>正在读取服务状态…</p>;
  if (state.kind === 'error') return <FailureView failure={state.error} action={<button type="button" className="text-button" onClick={onRetry}>重试状态</button>} />;
  if (state.kind !== 'success') return null;
  const data = state.data;
  return (
    <dl>
      <div><dt>API</dt><dd>{data.api_version}</dd></div>
      <div><dt>服务</dt><dd>{data.service_version}</dd></div>
      <div><dt>术语</dt><dd>{data.term_count.toLocaleString('zh-CN')}</dd></div>
      <div><dt>数据 revision</dt><dd title={data.source_commit ?? undefined}>{data.source_commit ?? data.schema_version ?? '未知'}</dd></div>
      <div><dt>LLM</dt><dd>{data.llm_configured ? '已配置' : '不可用'}</dd></div>
    </dl>
  );
}

function TermsView({ state, onTranslate }: { state: ResultState<TermsResult>; onTranslate: () => void }) {
  if (state.kind === 'idle') return <div className="empty-state">查询结果将按服务返回顺序完整显示。</div>;
  if (state.kind === 'loading') return <div className="empty-state" aria-live="polite">正在查询官方术语…</div>;
  if (state.kind === 'error') return <FailureView failure={state.error} action={<button type="button" className="text-button" onClick={onTranslate}>转到整句翻译</button>} />;
  if (state.kind !== 'success') return null;
  if (state.data.matches.length === 0) return <div className="empty-state">未找到术语匹配。<button type="button" className="text-button" onClick={onTranslate}>转到整句翻译</button></div>;
  return (
    <div className="terms-results" aria-live="polite">
      <p className="result-summary">服务返回 {state.data.matches.length} 条匹配</p>
      <div className="term-list">
        {state.data.matches.map((match, index) => (
          <article className="term-result" key={`${state.data.request_id}-${index}`}>
            <div className="term-pair"><strong>{match.zh}</strong><span>{match.en}</span></div>
            <dl><div><dt>category</dt><dd>{match.category}</dd></div><div><dt>reason</dt><dd>{match.reason}</dd></div><div><dt>score</dt><dd>{match.score}</dd></div></dl>
          </article>
        ))}
      </div>
      <RequestId value={state.data.request_id} />
    </div>
  );
}

function TranslationView({ state, copied, onCopy }: { state: ResultState<TranslationResult>; copied: boolean; onCopy: () => void }) {
  if (state.kind === 'idle') return <div className="empty-state">最终译文会显示在这里。</div>;
  if (state.kind === 'loading') return <div className="empty-state" aria-live="polite">正在等待服务返回；可随时取消本页等待。</div>;
  if (state.kind === 'cancelled') return <div className="notice-state" aria-live="polite">已停止本页等待。此操作仅停止本页等待；已开始的服务端 LLM 工作可能继续。</div>;
  if (state.kind === 'error') return <FailureView failure={state.error} />;
  return (
    <div className="translation-result" aria-live="polite">
      <div className="result-meta"><span>kind={state.data.kind}</span><span>目标：{state.data.direction === 'en' ? '英文' : '中文'}</span>{state.data.dictionary_miss && <span>dictionary miss</span>}</div>
      {state.data.dictionary_miss && <p className="guidance">未命中官方术语；此结果来自服务端模型翻译。</p>}
      <p>{state.data.text}</p>
      <div className="result-actions"><button className="secondary-button" type="button" onClick={onCopy}>{copied ? '已复制' : '复制译文'}</button></div>
      <RequestId value={state.data.request_id} />
    </div>
  );
}

function FailureView({ failure, action }: { failure: Failure; action?: React.ReactNode }) {
  return (
    <div className="error-panel" role="status">
      <p>{FAILURE_MESSAGES[failure.reason] ?? '服务返回异常，请稍后重试。'}</p>
      <code>{failure.reason}</code>
      {action}
      {failure.request_id && <RequestId value={failure.request_id} />}
    </div>
  );
}

function RequestId({ value }: { value: string }) {
  return <p className="request-id">request ID <code title={value}>{value}</code></p>;
}
