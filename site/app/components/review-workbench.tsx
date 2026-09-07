'use client';

import { FormEvent, useEffect, useRef, useState } from 'react';
import {
  exportReview,
  highlightSegments,
  isCurrentVerified,
  revisionOf,
  summarizeReview,
} from '../../lib/review-report.js';

type Failure = { status: 'unavailable'; reason: string; request_id?: string; retry_after_seconds?: number };
type ReviewSpan = { start: number; end: number; text: string };
type ReviewFinding = {
  id: string;
  verdict: string;
  rule_id: string;
  source_span: ReviewSpan | null;
  target_span: ReviewSpan | null;
  candidates: { zh: string; en: string; category: string; sources: { source_file: string; source_id: string }[] }[];
  candidates_truncated: boolean;
};
type ReviewReport = {
  request_id: string;
  source_revision: string;
  target_revision: string;
  rule_version: string;
  dictionary: { schema_version: string | null; source_commit: string | null; term_count: number };
  coverage: { evaluated: number; not_evaluated: number; rules: string[] };
  findings: ReviewFinding[];
  truncated: boolean;
};
type State =
  | { kind: 'idle' | 'loading' | 'cancelled' }
  | { kind: 'success'; data: ReviewReport; generation: number; sourceRevision: string; targetRevision: string }
  | { kind: 'error'; error: Failure };

const FALLBACK: Failure = { status: 'unavailable', reason: 'site_response_invalid' };
const MESSAGES: Record<string, string> = {
  reviews_pool_exhausted: '今日审校共享额度已用完，请在次日 UTC 00:00 后再来。',
  shared_pool_busy: '共享公测池正在忙碌，请稍后再试。',
  shared_pool_unavailable: '暂时无法确认共享额度，服务已暂停接收请求。请稍后重试。',
  site_invalid_request: '请检查输入：原文和译文均不超过 2,000 字符。',
  site_request_too_large: '输入内容过大，请缩短后重试。',
  input_too_long: '文本过长，请分段审校。',
  invalid_request: '输入格式有误，请修改后重试。',
  upstream_timeout: '等待超时。已获准的请求可能仍在处理，本次额度不会返还。',
  upstream_rate_limited: '服务繁忙，请稍后重试。',
};

function failure(value: unknown): value is Failure {
  return !!value && typeof value === 'object' && (value as Failure).status === 'unavailable' && typeof (value as Failure).reason === 'string';
}
async function payload(r: Response): Promise<unknown> {
  try { return await r.json(); } catch { return FALLBACK; }
}
function isReport(value: unknown): value is ReviewReport {
  const x = value as ReviewReport;
  return !!x && typeof x.request_id === 'string' && Array.isArray(x.findings) && typeof x.truncated === 'boolean'
    && x.coverage && typeof x.coverage.not_evaluated === 'number';
}

export function ReviewWorkbench() {
  const [source, setSource] = useState('');
  const [target, setTarget] = useState('');
  const [direction, setDirection] = useState<'en' | 'zh'>('en');
  const [state, setState] = useState<State>({ kind: 'idle' });
  const [generation, setGeneration] = useState(0);
  const generationRef = useRef(0);
  const [resolutions, setResolutions] = useState<{ mention_id: string; choice: 'official_pair' | 'not_a_term'; zh?: string; en?: string }[]>([]);
  const [history, setHistory] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    function onDraft(event: Event) {
      const detail = (event as CustomEvent<{ source?: string; target?: string; direction?: 'en' | 'zh' }>).detail;
      if (!detail) return;
      if (typeof detail.source === 'string') setSource(detail.source);
      if (typeof detail.target === 'string') setTarget(detail.target);
      if (detail.direction === 'en' || detail.direction === 'zh') setDirection(detail.direction);
      setState({ kind: 'idle' });
      setResolutions([]);
    }
    window.addEventListener('wuwaterm-send-review', onDraft);
    return () => window.removeEventListener('wuwaterm-send-review', onDraft);
  }, []);
  useEffect(() => () => { controller.current?.abort(); }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!source.trim() || !target.trim()) return;
    controller.current?.abort();
    generationRef.current += 1;
    const mine = generationRef.current;
    setGeneration(mine);
    const abort = new AbortController();
    controller.current = abort;
    setCopied(false);
    setState({ kind: 'loading' });
    const body = resolutions.length
      ? { source, target, direction, resolutions }
      : { source, target, direction };
    try {
      const r = await fetch('/api/reviews', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        cache: 'no-store',
        body: JSON.stringify(body),
        signal: abort.signal,
      });
      const v = await payload(r);
      if (abort.signal.aborted || controller.current !== abort) return;
      if (generationRef.current !== mine) return;
      if (r.ok && isReport(v)) {
        const sourceRevision = await revisionOf(source);
        const targetRevision = await revisionOf(target);
        if (generationRef.current !== mine) return;
        setState({ kind: 'success', data: v, generation: mine, sourceRevision, targetRevision });
      } else if (generationRef.current === mine) {
        setState({ kind: 'error', error: failure(v) ? v : FALLBACK });
      }
    } catch {
      if (!abort.signal.aborted && controller.current === abort && generationRef.current === mine) {
        setState({ kind: 'error', error: FALLBACK });
      }
    } finally {
      if (controller.current === abort) controller.current = null;
    }
  }

  function cancel() {
    controller.current?.abort();
    controller.current = null;
    setState({ kind: 'cancelled' });
  }

  function choosePair(finding: ReviewFinding, zh: string, en: string) {
    setResolutions((current) => {
      const rest = current.filter((item) => item.mention_id !== finding.id);
      return [...rest, { mention_id: finding.id, choice: 'official_pair', zh, en }];
    });
    if (finding.target_span) {
      const segments = highlightSegments(target, finding.target_span);
      if (segments) {
        setHistory((stack) => [...stack, target]);
        setTarget(segments.before + (direction === 'en' ? en : zh) + segments.after);
        setState({ kind: 'idle' });
      }
    }
  }

  function markNotTerm(finding: ReviewFinding) {
    setResolutions((current) => {
      const rest = current.filter((item) => item.mention_id !== finding.id);
      return [...rest, { mention_id: finding.id, choice: 'not_a_term' }];
    });
  }

  function undo() {
    setHistory((stack) => {
      if (!stack.length) return stack;
      const previous = stack[stack.length - 1];
      setTarget(previous);
      setState({ kind: 'idle' });
      return stack.slice(0, -1);
    });
  }

  async function download() {
    if (state.kind !== 'success') return;
    const sourceRevision = await revisionOf(source);
    const targetRevision = await revisionOf(target);
    const exported = exportReview({
      report: state.data,
      source,
      target,
      sourceRevision,
      targetRevision,
      reportGeneration: state.generation,
      currentGeneration: generation,
    });
    const blob = new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = window.document.createElement('a');
    link.href = url;
    link.download = 'wuwaterm-review.json';
    link.click();
    URL.revokeObjectURL(url);
    setCopied(true);
  }

  const sourceLength = Array.from(source).length;
  const targetLength = Array.from(target).length;
  const summary = state.kind === 'success' ? summarizeReview(state.data) : null;
  const currentVerified = state.kind === 'success' && isCurrentVerified({
    report: state.data,
    sourceRevision: state.sourceRevision,
    targetRevision: state.targetRevision,
    reportGeneration: state.generation,
    currentGeneration: generation,
  });

  return <section className="workspace-card review-card" aria-labelledby="review-title">
    <div className="card-heading"><div><p className="section-kicker">03 / REVIEW</p><h2 id="review-title">译文审校</h2></div><span className="tag">术语依据</span></div>
    <p className="card-intro">粘贴原文和已有译文，核对词典依据。不调用整句翻译模型，也不消耗翻译额度。</p>
    <form onSubmit={submit}>
      <div className="label-row"><label htmlFor="review-source">原文</label><select aria-label="译文语言" value={direction} disabled={state.kind === 'loading'} onChange={e => setDirection(e.target.value as 'en' | 'zh')}><option value="en">译文为英文</option><option value="zh">译文为中文</option></select></div>
      <textarea id="review-source" value={source} disabled={state.kind === 'loading'} onChange={e => { setSource(e.target.value); setState({ kind: 'idle' }); }} rows={4} placeholder="粘贴需要核对的原文…" />
      <label htmlFor="review-target">已有译文</label>
      <textarea id="review-target" value={target} disabled={state.kind === 'loading'} onChange={e => { setTarget(e.target.value); setState({ kind: 'idle' }); }} rows={4} placeholder="粘贴已有译文…" />
      <div className="field-hint"><span>请勿输入敏感或个人信息</span><span>{sourceLength.toLocaleString()} + {targetLength.toLocaleString()} / 2,000</span></div>
      <div className="actions">
        <button type="submit" disabled={!source.trim() || !target.trim() || sourceLength > 2000 || targetLength > 2000 || state.kind === 'loading'}>{state.kind === 'loading' ? '核对中…' : '核对术语'}</button>
        {state.kind === 'loading' && <button className="secondary-button" type="button" onClick={cancel}>取消等待</button>}
        <button className="secondary-button" type="button" onClick={undo} disabled={!history.length}>撤销修订</button>
      </div>
    </form>
    {summary && <div className="review-summary" aria-live="polite">
      <p><strong>{summary.headline}</strong></p>
      <p>{summary.detail}</p>
      {currentVerified ? <p className="review-current">当前报告与两框文本一致。</p> : <p className="review-stale">报告已过期或不是当前世代，不能当作当前已核。</p>}
    </div>}
    {state.kind === 'idle' && <p className="translation-placeholder">审校发现会显示在这里。空结果不会显示全部通过。</p>}
    {state.kind === 'loading' && <p className="notice-state" role="status">正在核对。取消仅停止本页等待；已扣审校额度不会返还。</p>}
    {state.kind === 'cancelled' && <p className="notice-state" role="status">已停止本页等待；迟到的结果不会标为当前已核。</p>}
    {state.kind === 'error' && <div className="error-panel" role="status"><p>{MESSAGES[state.error.reason] ?? '服务暂时不可用，请稍后重试。'}</p></div>}
    {state.kind === 'success' && <FindingsView report={state.data} source={source} target={target} onChoose={choosePair} onSkip={markNotTerm} />}
    {state.kind === 'success' && <div className="actions"><button className="secondary-button" type="button" onClick={() => void download()}>{copied ? '已导出' : '导出当前结果'}</button></div>}
  </section>;
}

function FindingsView({
  report, source, target, onChoose, onSkip,
}: {
  report: ReviewReport;
  source: string;
  target: string;
  onChoose: (finding: ReviewFinding, zh: string, en: string) => void;
  onSkip: (finding: ReviewFinding) => void;
}) {
  if (!report.findings.length) {
    return <div className="empty-state"><p>没有术语发现。句意仍未评估，不能视为全部通过。</p></div>;
  }
  return <div className="review-findings">
    {report.findings.map((finding) => {
      const sourceHit = finding.source_span ? highlightSegments(source, finding.source_span) : null;
      const targetHit = finding.target_span ? highlightSegments(target, finding.target_span) : null;
      return <article className="term-result" key={finding.id}>
        <p className="result-summary">{finding.verdict} · {finding.rule_id}{finding.candidates_truncated ? ' · 候选已截断' : ''}</p>
        <p className="review-excerpt">{sourceHit ? <>{sourceHit.before}<mark>{sourceHit.hit}</mark>{sourceHit.after}</> : finding.source_span?.text ?? '原文位置无法可靠对齐'}</p>
        <p className="review-excerpt">{targetHit ? <>{targetHit.before}<mark>{targetHit.hit}</mark>{targetHit.after}</> : '译文位置未对齐，不高亮'}</p>
        {finding.candidates.map((candidate) => (
          <div className="term-pair" key={`${candidate.zh}:${candidate.en}:${candidate.category}`}>
            <strong>{candidate.zh}</strong>
            <span>{candidate.en}</span>
            <button className="text-button" type="button" onClick={() => onChoose(finding, candidate.zh, candidate.en)}>采用此官方词对</button>
          </div>
        ))}
        <button className="text-button" type="button" onClick={() => onSkip(finding)}>这里不是术语</button>
      </article>;
    })}
  </div>;
}
