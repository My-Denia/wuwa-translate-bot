const MAX_UPSTREAM_BYTES = 65_536;
const MAX_SITE_REQUEST_BYTES = 65_536;
const EXPECTED_API_MOUNT_PATH = '/wuwaterm-api/';

const JSON_HEADERS = Object.freeze({
  'cache-control': 'no-store',
  'content-type': 'application/json; charset=utf-8',
  'x-robots-tag': 'noindex, nofollow, noarchive',
});

const ERROR_STATUSES = Object.freeze({
  site_not_configured: 503,
  site_invalid_request: 400,
  site_request_too_large: 413,
  upstream_timeout: 504,
  upstream_unauthorized: 401,
  upstream_forbidden: 403,
  upstream_not_found: 502,
  upstream_rate_limited: 429,
  upstream_unavailable: 503,
  upstream_redirect: 502,
  upstream_invalid_content_type: 502,
  upstream_response_too_large: 502,
  upstream_invalid_json: 502,
  upstream_schema_mismatch: 502,
  upstream_network_error: 502,
  invalid_request: 400,
  payload_too_large: 413,
  input_too_long: 422,
  llm_unavailable: 503,
  llm_budget_exhausted: 503,
});

const API_ERROR_CODES = new Set([
  'unauthorized', 'forbidden', 'rate_limited', 'payload_too_large',
  'invalid_request', 'input_too_long', 'llm_unavailable',
  'llm_budget_exhausted', 'internal',
]);

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function errorResponse(reason, requestId) {
  const body = { status: 'unavailable', reason };
  if (boundedText(requestId)) body.request_id = requestId;
  return jsonResponse(ERROR_STATUSES[reason] ?? 502, body);
}

function nonEmptyString(value) {
  return typeof value === 'string' && value.trim().length > 0;
}

function boundedText(value, maxLength = 256) {
  return nonEmptyString(value) && value.length <= maxLength && !/[\u0000-\u001f\u007f]/u.test(value);
}

function nullableText(value) {
  return value === null || boundedText(value);
}

function plainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function exactKeys(value, keys) {
  return Object.keys(value).sort().join('\u0000') === [...keys].sort().join('\u0000');
}

function plainOutputText(value) {
  return typeof value === 'string' && value.length <= 60_000
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(value);
}

function validMetaBody(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  return (
    boundedText(value.api_version, 128) &&
    boundedText(value.service_version, 128) &&
    Number.isInteger(value.term_count) &&
    value.term_count >= 0 &&
    boundedText(value.request_id) &&
    typeof value.llm_configured === 'boolean' &&
    nullableText(value.schema_version) &&
    nullableText(value.source_profile) &&
    nullableText(value.source_commit) &&
    exactKeys(value, [
      'api_version', 'llm_configured', 'request_id', 'schema_version',
      'service_version', 'source_commit', 'source_profile', 'term_count',
    ])
  );
}

function validTermMatch(value) {
  return plainObject(value)
    && boundedText(value.zh, 2_000)
    && boundedText(value.en, 2_000)
    && boundedText(value.category, 256)
    && Number.isFinite(value.score)
    && value.score >= 0
    && value.score <= 100
    && boundedText(value.reason, 256)
    && exactKeys(value, ['category', 'en', 'reason', 'score', 'zh']);
}

function validTermsBody(value) {
  return plainObject(value)
    && boundedText(value.query, 4_096)
    && Array.isArray(value.matches)
    && value.matches.length <= 5
    && value.matches.every(validTermMatch)
    && boundedText(value.request_id)
    && exactKeys(value, ['matches', 'query', 'request_id']);
}

function validTranslationBody(value) {
  return plainObject(value)
    && ['noop', 'exact', 'fuzzy', 'llm'].includes(value.kind)
    && plainOutputText(value.text)
    && ['en', 'zh'].includes(value.direction)
    && typeof value.dictionary_miss === 'boolean'
    && boundedText(value.request_id)
    && exactKeys(value, ['dictionary_miss', 'direction', 'kind', 'request_id', 'text']);
}

function validErrorBody(value) {
  return plainObject(value)
    && plainObject(value.error)
    && API_ERROR_CODES.has(value.error.code)
    && boundedText(value.error.message, 2_000)
    && exactKeys(value.error, ['code', 'message'])
    && boundedText(value.request_id)
    && exactKeys(value, ['error', 'request_id']);
}

function validPinnedHostname(hostname) {
  if (!boundedText(hostname, 253) || hostname !== hostname.toLowerCase()) return false;
  if (hostname.includes(':') || /^\d{1,3}(?:\.\d{1,3}){3}$/u.test(hostname)) return false;
  if (
    hostname === 'localhost' ||
    hostname.endsWith('.localhost') ||
    hostname.endsWith('.local') ||
    hostname.endsWith('.internal') ||
    hostname.endsWith('.home.arpa')
  ) {
    return false;
  }
  return hostname.includes('.') && hostname.split('.').every(
    (label) => /^(?!-)[a-z0-9-]{1,63}(?<!-)$/u.test(label),
  );
}

function configuredUpstream(environment) {
  const baseValue = environment?.WUWATERM_API_BASE_URL;
  const allowedHost = environment?.WUWATERM_API_ALLOWED_HOST;
  const token = environment?.WUWATERM_SITE_DEVICE_TOKEN;
  if (!nonEmptyString(baseValue) || !nonEmptyString(allowedHost) || !nonEmptyString(token)) {
    return null;
  }
  if (!validPinnedHostname(allowedHost)) return null;
  if (
    baseValue !== `https://${allowedHost}${EXPECTED_API_MOUNT_PATH}`
    && baseValue !== `https://${allowedHost}${EXPECTED_API_MOUNT_PATH.slice(0, -1)}`
  ) return null;

  let baseUrl;
  try {
    baseUrl = new URL(baseValue);
  } catch {
    return null;
  }
  if (
    baseUrl.protocol !== 'https:' ||
    baseUrl.username ||
    baseUrl.password ||
    baseUrl.port ||
    baseUrl.search ||
    baseUrl.hash ||
    baseUrl.hostname.toLowerCase() !== allowedHost
  ) {
    return null;
  }

  const normalizedPath = baseUrl.pathname.endsWith('/')
    ? baseUrl.pathname
    : `${baseUrl.pathname}/`;
  if (normalizedPath !== EXPECTED_API_MOUNT_PATH) return null;
  baseUrl.pathname = EXPECTED_API_MOUNT_PATH;

  return {
    baseValue,
    baseUrl,
    token,
  };
}

function containsSensitiveValue(value, exactValues, caseInsensitiveValues = []) {
  const projectedStrings = Object.values(value).filter(
    (field) => typeof field === 'string',
  );
  const serialized = JSON.stringify(value);
  const exactMatch = exactValues.some((sensitive) => {
    if (!nonEmptyString(sensitive)) return false;
    const escapedSensitive = JSON.stringify(sensitive).slice(1, -1);
    return projectedStrings.some((field) => field.includes(sensitive))
      || serialized.includes(escapedSensitive);
  });
  if (exactMatch) return true;
  const foldedStrings = projectedStrings.map((field) => field.toLowerCase());
  const foldedSerialized = serialized.toLowerCase();
  return caseInsensitiveValues.some((sensitive) => {
    if (!nonEmptyString(sensitive)) return false;
    const folded = sensitive.toLowerCase();
    const escapedFolded = JSON.stringify(sensitive).slice(1, -1).toLowerCase();
    return foldedStrings.some((field) => field.includes(folded))
      || foldedSerialized.includes(escapedFolded);
  });
}

function strictJsonContentType(value) {
  return value.split(';', 1)[0].trim().toLowerCase() === 'application/json';
}

async function readWithAbort(reader, signal) {
  if (signal.aborted) throw new DOMException('aborted', 'AbortError');
  let onAbort;
  const aborted = new Promise((_resolve, reject) => {
    onAbort = () => reject(new DOMException('aborted', 'AbortError'));
    signal.addEventListener('abort', onAbort, { once: true });
  });
  try {
    return await Promise.race([reader.read(), aborted]);
  } finally {
    signal.removeEventListener('abort', onAbort);
  }
}

function cancelWithoutWaiting(cancelable) {
  if (!cancelable || typeof cancelable.cancel !== 'function') return;
  try {
    const cancellation = cancelable.cancel();
    cancellation?.catch?.(() => {});
  } catch {
    // Cancellation errors are intentionally not exposed or logged.
  }
}

function cancelUpstreamBody(upstream) {
  cancelWithoutWaiting(upstream.body);
}

function rejectUpstream(upstream, reason) {
  cancelUpstreamBody(upstream);
  return errorResponse(reason);
}

async function readBoundedBody(upstream, signal) {
  const contentLength = upstream.headers.get('content-length');
  if (contentLength !== null) {
    const parsed = Number(contentLength);
    if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > MAX_UPSTREAM_BYTES) {
      cancelUpstreamBody(upstream);
      return { ok: false, reason: 'upstream_response_too_large' };
    }
  }
  if (!upstream.body) return { ok: true, text: '' };

  const reader = upstream.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await readWithAbort(reader, signal);
      if (done) break;
      total += value.byteLength;
      if (total > MAX_UPSTREAM_BYTES) {
        cancelWithoutWaiting(reader);
        return { ok: false, reason: 'upstream_response_too_large' };
      }
      chunks.push(value);
    }
  } catch {
    cancelWithoutWaiting(reader);
    return {
      ok: false,
      reason: signal.aborted ? 'upstream_timeout' : 'upstream_network_error',
    };
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // A cancelled reader may already have released its lock.
    }
  }

  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return { ok: true, text: new TextDecoder('utf-8', { fatal: true }).decode(bytes) };
  } catch {
    return { ok: false, reason: 'upstream_invalid_json' };
  }
}

function projectedErrorReason(code, status) {
  if (code === 'unauthorized') return 'upstream_unauthorized';
  if (code === 'forbidden') return 'upstream_forbidden';
  if (code === 'rate_limited') return 'upstream_rate_limited';
  if (code === 'internal') return status === 504 ? 'upstream_timeout' : 'upstream_unavailable';
  return code;
}

function compatibleErrorStatus(code, status) {
  const allowed = {
    400: ['invalid_request'],
    401: ['unauthorized'],
    403: ['forbidden'],
    413: ['payload_too_large'],
    422: ['input_too_long'],
    429: ['rate_limited'],
    500: ['internal'],
    503: ['internal', 'llm_unavailable', 'llm_budget_exhausted'],
    504: ['internal'],
  };
  return allowed[status]?.includes(code) === true;
}

async function projectApiError(upstream, controller, configured, upstreamUrl) {
  const raw = await readBoundedBody(upstream, controller.signal);
  if (!raw.ok) return errorResponse(raw.reason);
  let body;
  try {
    body = JSON.parse(raw.text);
  } catch {
    return errorResponse('upstream_invalid_json');
  }
  if (!validErrorBody(body) || !compatibleErrorStatus(body.error.code, upstream.status)) {
    return errorResponse('upstream_schema_mismatch');
  }
  const projectedReason = projectedErrorReason(body.error.code, upstream.status);
  if (containsSensitiveValue(
    { status: 'unavailable', reason: projectedReason, request_id: body.request_id },
    [configured.token],
    [
      configured.baseValue,
      upstreamUrl.origin,
      upstreamUrl.hostname,
      configured.baseUrl.pathname,
      upstreamUrl.toString(),
    ],
  )) return errorResponse('upstream_schema_mismatch');
  return errorResponse(projectedReason, body.request_id);
}

async function proxyRequest({
  environment,
  endpoint,
  method,
  body,
  validator,
  projector,
  fetchImpl,
  timeoutMs,
  projectErrors = false,
}) {
  const configured = configuredUpstream(environment);
  if (!configured || typeof fetchImpl !== 'function') return errorResponse('site_not_configured');
  const upstreamUrl = new URL(endpoint, configured.baseUrl);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = {
      accept: 'application/json',
      authorization: `Bearer ${configured.token}`,
    };
    const init = {
      method,
      headers,
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'manual',
      signal: controller.signal,
    };
    if (body !== undefined) {
      headers['content-type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    const upstream = await fetchImpl(upstreamUrl, init);

    if (upstream.status >= 300 && upstream.status < 400) {
      return rejectUpstream(upstream, 'upstream_redirect');
    }
    if (projectErrors && [400, 401, 403, 413, 422, 429, 500, 503, 504].includes(upstream.status)) {
      const contentType = upstream.headers.get('content-type') ?? '';
      if (!strictJsonContentType(contentType)) {
        return rejectUpstream(upstream, 'upstream_invalid_content_type');
      }
      return await projectApiError(upstream, controller, configured, upstreamUrl);
    }
    if (upstream.status === 401) return rejectUpstream(upstream, 'upstream_unauthorized');
    if (upstream.status === 403) return rejectUpstream(upstream, 'upstream_forbidden');
    if (upstream.status === 404) return rejectUpstream(upstream, 'upstream_not_found');
    if (upstream.status === 429) return rejectUpstream(upstream, 'upstream_rate_limited');
    if (upstream.status === 504) return rejectUpstream(upstream, 'upstream_timeout');
    if (upstream.status >= 500) return rejectUpstream(upstream, 'upstream_unavailable');
    if (upstream.status !== 200) return rejectUpstream(upstream, 'upstream_network_error');

    const contentType = upstream.headers.get('content-type') ?? '';
    if (!strictJsonContentType(contentType)) {
      return rejectUpstream(upstream, 'upstream_invalid_content_type');
    }
    const raw = await readBoundedBody(upstream, controller.signal);
    if (!raw.ok) return errorResponse(raw.reason);

    let upstreamBody;
    try {
      upstreamBody = JSON.parse(raw.text);
    } catch {
      return errorResponse('upstream_invalid_json');
    }
    if (!validator(upstreamBody)) return errorResponse('upstream_schema_mismatch');
    const clientBody = projector(upstreamBody);
    if (
      containsSensitiveValue(
        clientBody,
        [configured.token],
        [
          configured.baseValue,
          upstreamUrl.origin,
          upstreamUrl.hostname,
          configured.baseUrl.pathname,
          upstreamUrl.toString(),
        ],
      )
    ) {
      return errorResponse('upstream_schema_mismatch');
    }
    return jsonResponse(200, clientBody);
  } catch {
    return errorResponse(controller.signal.aborted ? 'upstream_timeout' : 'upstream_network_error');
  } finally {
    clearTimeout(timer);
  }
}

/**
 * @param {{
 *   environment?: Record<string, unknown>,
 *   fetchImpl?: typeof globalThis.fetch,
 *   timeoutMs?: number,
 * }} [options]
 * @returns {Promise<Response>}
 */
export async function proxyMetaRequest({
  environment,
  fetchImpl = globalThis.fetch,
  timeoutMs = 8_000,
} = {}) {
  return proxyRequest({
    environment,
    endpoint: 'v1/meta',
    method: 'GET',
    validator: validMetaBody,
    projector: (body) => ({
      api_version: body.api_version,
      service_version: body.service_version,
      schema_version: body.schema_version,
      source_profile: body.source_profile,
      source_commit: body.source_commit,
      term_count: body.term_count,
      llm_configured: body.llm_configured,
      request_id: body.request_id,
    }),
    fetchImpl,
    timeoutMs,
  });
}

/**
 * @param {{
 *   environment?: Record<string, unknown>,
 *   query?: string,
 *   fetchImpl?: typeof globalThis.fetch,
 *   timeoutMs?: number,
 * }} [options]
 * @returns {Promise<Response>}
 */
export async function proxyTermsRequest({
  environment,
  query,
  fetchImpl = globalThis.fetch,
  timeoutMs = 8_000,
} = {}) {
  if (!nonEmptyString(query) || query.length > 4_096) return errorResponse('site_invalid_request');
  const search = new URLSearchParams({ q: query });
  return proxyRequest({
    environment,
    endpoint: `v1/terms?${search}`,
    method: 'GET',
    validator: validTermsBody,
    projector: (result) => ({
      query: result.query,
      matches: result.matches.map((match) => ({
        zh: match.zh,
        en: match.en,
        category: match.category,
        score: match.score,
        reason: match.reason,
      })),
      request_id: result.request_id,
    }),
    fetchImpl,
    timeoutMs,
    projectErrors: true,
  });
}

/**
 * @param {{
 *   environment?: Record<string, unknown>,
 *   input?: {text: string, to?: 'en' | 'zh'},
 *   fetchImpl?: typeof globalThis.fetch,
 *   timeoutMs?: number,
 * }} [options]
 * @returns {Promise<Response>}
 */
export async function proxyTranslationRequest({
  environment,
  input,
  fetchImpl = globalThis.fetch,
  timeoutMs = 100_000,
} = {}) {
  if (!validTranslationInput(input)) return errorResponse('site_invalid_request');
  return proxyRequest({
    environment,
    endpoint: 'v1/translations',
    method: 'POST',
    body: input,
    validator: validTranslationBody,
    projector: (result) => ({
      kind: result.kind,
      text: result.text,
      direction: result.direction,
      dictionary_miss: result.dictionary_miss,
      request_id: result.request_id,
    }),
    fetchImpl,
    timeoutMs,
    projectErrors: true,
  });
}

function validTranslationInput(value) {
  if (!plainObject(value) || !exactKeys(value, value.to === undefined ? ['text'] : ['text', 'to'])) {
    return false;
  }
  return nonEmptyString(value.text)
    && value.text.length <= MAX_SITE_REQUEST_BYTES
    && (value.to === undefined || value.to === 'en' || value.to === 'zh');
}

/**
 * @param {Request} request
 * @returns {{ok: true, query: string} | {ok: false, response: Response}}
 */
export function parseTermsRequest(request) {
  let query;
  try {
    query = new URL(request.url).searchParams.get('q');
  } catch {
    query = null;
  }
  if (!nonEmptyString(query) || query.length > 4_096) {
    return { ok: false, response: errorResponse('site_invalid_request') };
  }
  return { ok: true, query };
}

/**
 * @param {Request} request
 * @returns {Promise<
 *   {ok: true, input: {text: string, to?: 'en' | 'zh'}}
 *   | {ok: false, response: Response}
 * >}
 */
export async function parseTranslationRequest(request) {
  if (!strictJsonContentType(request.headers.get('content-type') ?? '')) {
    return { ok: false, response: errorResponse('site_invalid_request') };
  }
  const contentLength = request.headers.get('content-length');
  if (contentLength !== null) {
    const parsed = Number(contentLength);
    if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > MAX_SITE_REQUEST_BYTES) {
      return { ok: false, response: errorResponse('site_request_too_large') };
    }
  }
  const requestBody = await readBoundedRequestBody(request);
  if (!requestBody.ok) return { ok: false, response: errorResponse(requestBody.reason) };
  let value;
  try {
    value = JSON.parse(requestBody.text);
  } catch {
    return { ok: false, response: errorResponse('site_invalid_request') };
  }
  if (plainObject(value) && value.to === null) {
    value = { ...value };
    delete value.to;
  }
  if (!validTranslationInput(value)) {
    return { ok: false, response: errorResponse('site_invalid_request') };
  }
  return { ok: true, input: value };
}

async function readBoundedRequestBody(request) {
  if (!request.body) return { ok: true, text: '' };
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_SITE_REQUEST_BYTES) {
        cancelWithoutWaiting(reader);
        return { ok: false, reason: 'site_request_too_large' };
      }
      chunks.push(value);
    }
  } catch {
    cancelWithoutWaiting(reader);
    return { ok: false, reason: 'site_invalid_request' };
  } finally {
    try { reader.releaseLock(); } catch { /* already released */ }
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return { ok: true, text: new TextDecoder('utf-8', { fatal: true }).decode(bytes) };
  } catch {
    return { ok: false, reason: 'site_invalid_request' };
  }
}

/** @returns {Promise<Record<string, unknown>>} */
export async function runtimeEnvironment() {
  try {
    const { env } = await import('cloudflare:workers');
    return env;
  } catch {
    return process.env;
  }
}
