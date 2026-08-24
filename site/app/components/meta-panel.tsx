'use client';

import { useEffect, useState } from 'react';

type MetaSuccess = {
  api_version: string;
  service_version: string;
  term_count: number;
  request_id: string;
};
type MetaFailure = {
  status: 'unavailable';
  reason: string;
};
type ViewState =
  | { kind: 'loading' }
  | { kind: 'success'; data: MetaSuccess }
  | { kind: 'error'; data: MetaFailure };

const UNKNOWN_FAILURE: MetaFailure = {
  status: 'unavailable',
  reason: 'site_response_invalid',
};

const FAILURE_MESSAGES: Record<string, string> = {
  site_not_configured: '站点尚未完成服务配置，请联系管理员。',
  upstream_timeout: '服务响应超时，请稍后重试。',
  upstream_unauthorized: '服务凭据无效，请联系管理员。',
  upstream_forbidden: '服务凭据权限不足，请联系管理员。',
  upstream_rate_limited: '请求过于频繁，请稍后重试。',
  upstream_unavailable: '服务暂时不可用，请稍后重试。',
  site_response_invalid: '站点返回了无法识别的响应，请稍后重试。',
};

function isMetaSuccess(value: unknown): value is MetaSuccess {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<MetaSuccess>;
  return (
    typeof item.api_version === 'string' &&
    typeof item.service_version === 'string' &&
    Number.isInteger(item.term_count) &&
    typeof item.request_id === 'string'
  );
}

function isMetaFailure(value: unknown): value is MetaFailure {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<MetaFailure>;
  return (
    item.status === 'unavailable' &&
    typeof item.reason === 'string' &&
    item.reason.length > 0
  );
}

export function MetaPanel() {
  const [state, setState] = useState<ViewState>({ kind: 'loading' });
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/meta', {
      method: 'GET',
      headers: { accept: 'application/json' },
      cache: 'no-store',
      signal: controller.signal,
    })
      .then(async (response) => ({ response, payload: (await response.json()) as unknown }))
      .then(({ response, payload }) => {
        if (response.ok && isMetaSuccess(payload)) {
          setState({ kind: 'success', data: payload });
          return;
        }
        setState({ kind: 'error', data: isMetaFailure(payload) ? payload : UNKNOWN_FAILURE });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ kind: 'error', data: UNKNOWN_FAILURE });
      });
    return () => controller.abort();
  }, [reloadKey]);

  if (state.kind === 'loading') {
    return (
      <section className="meta-shell" aria-busy="true" aria-live="polite">
        <MetaHeading title="正在连接术语服务" status="检查中" statusClass="status-loading" />
        <div className="loading-grid" aria-hidden="true"><span /><span /><span /></div>
      </section>
    );
  }
  if (state.kind === 'error') {
    return (
      <section className="meta-shell" aria-live="polite" aria-atomic="true">
        <MetaHeading title="服务信息暂不可用" status="未通过" statusClass="status-error" />
        <div className="error-panel">
          <p>{FAILURE_MESSAGES[state.data.reason] ?? '服务返回异常，请稍后重试。'}</p>
          <code>{state.data.reason}</code>
        </div>
        <button
          className="secondary-button"
          type="button"
          onClick={() => {
            setState({ kind: 'loading' });
            setReloadKey((value) => value + 1);
          }}
        >
          重新检查
        </button>
      </section>
    );
  }

  const cards = [
    ['API 版本', state.data.api_version],
    ['服务版本', state.data.service_version],
    ['官方术语', state.data.term_count.toLocaleString('zh-CN')],
  ];
  return (
    <section className="meta-shell" aria-live="polite" aria-atomic="true">
      <MetaHeading title="术语服务已连接" status="可用" statusClass="status-ready" />
      <dl className="meta-grid">
        {cards.map(([label, value]) => (
          <div className="meta-card" key={label}><dt>{label}</dt><dd>{value}</dd></div>
        ))}
      </dl>
      <p className="request-id">请求 ID <code>{state.data.request_id}</code></p>
    </section>
  );
}

function MetaHeading({ title, status, statusClass }: { title: string; status: string; statusClass: string }) {
  return (
    <div className="meta-heading">
      <div><p className="section-kicker">可行性检查</p><h2>{title}</h2></div>
      <span className={`status-chip ${statusClass}`}>{status}</span>
    </div>
  );
}
