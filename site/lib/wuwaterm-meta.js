const MAX_UPSTREAM_BYTES = 65_536;

const JSON_HEADERS = Object.freeze({
  'cache-control': 'no-store',
  'content-type': 'application/json; charset=utf-8',
  'x-robots-tag': 'noindex, nofollow, noarchive',
});

const ERROR_STATUSES = Object.freeze({
  site_not_configured: 503,
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
});

function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function errorResponse(reason) {
  return jsonResponse(ERROR_STATUSES[reason], { status: 'unavailable', reason });
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
    nullableText(value.source_commit)
  );
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
    baseUrl.pathname !== '/' ||
    baseUrl.search ||
    baseUrl.hash ||
    baseUrl.hostname.toLowerCase() !== allowedHost
  ) {
    return null;
  }

  return {
    baseValue,
    metaUrl: new URL('/v1/meta', baseUrl.origin),
    token,
  };
}

function containsSensitiveValue(value, ...sensitiveValues) {
  const serialized = JSON.stringify(value);
  return sensitiveValues.some(
    (sensitive) => nonEmptyString(sensitive) && serialized.includes(sensitive),
  );
}

function strictJsonContentType(value) {
  return value.split(';', 1)[0].trim().toLowerCase() === 'application/json';
}

async function readBoundedBody(upstream) {
  const contentLength = upstream.headers.get('content-length');
  if (contentLength !== null) {
    const parsed = Number(contentLength);
    if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > MAX_UPSTREAM_BYTES) {
      return { ok: false, reason: 'upstream_response_too_large' };
    }
  }
  if (!upstream.body) return { ok: true, text: '' };

  const reader = upstream.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_UPSTREAM_BYTES) {
        await reader.cancel();
        return { ok: false, reason: 'upstream_response_too_large' };
      }
      chunks.push(value);
    }
  } catch {
    return { ok: false, reason: 'upstream_network_error' };
  } finally {
    reader.releaseLock();
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

/**
 * @param {{
 *   environment?: Record<string, unknown>,
 *   fetchImpl?: (input: URL, init?: RequestInit) => Promise<Response>,
 *   timeoutMs?: number,
 * }} [options]
 */
export async function proxyMetaRequest({
  environment,
  fetchImpl = globalThis.fetch,
  timeoutMs = 8_000,
} = {}) {
  const configured = configuredUpstream(environment);
  if (!configured || typeof fetchImpl !== 'function') return errorResponse('site_not_configured');

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const upstream = await fetchImpl(configured.metaUrl, {
      method: 'GET',
      headers: {
        accept: 'application/json',
        authorization: `Bearer ${configured.token}`,
      },
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'manual',
      signal: controller.signal,
    });

    if (upstream.status >= 300 && upstream.status < 400) {
      return errorResponse('upstream_redirect');
    }
    if (upstream.status === 401) return errorResponse('upstream_unauthorized');
    if (upstream.status === 403) return errorResponse('upstream_forbidden');
    if (upstream.status === 404) return errorResponse('upstream_not_found');
    if (upstream.status === 429) return errorResponse('upstream_rate_limited');
    if (upstream.status === 504) return errorResponse('upstream_timeout');
    if (upstream.status >= 500) return errorResponse('upstream_unavailable');
    if (upstream.status !== 200) return errorResponse('upstream_network_error');

    const contentType = upstream.headers.get('content-type') ?? '';
    if (!strictJsonContentType(contentType)) {
      return errorResponse('upstream_invalid_content_type');
    }
    const raw = await readBoundedBody(upstream);
    if (!raw.ok) return errorResponse(raw.reason);

    let body;
    try {
      body = JSON.parse(raw.text);
    } catch {
      return errorResponse('upstream_invalid_json');
    }
    if (!validMetaBody(body)) return errorResponse('upstream_schema_mismatch');

    const clientBody = {
      api_version: body.api_version,
      service_version: body.service_version,
      term_count: body.term_count,
      request_id: body.request_id,
    };
    if (
      containsSensitiveValue(
        clientBody,
        configured.token,
        configured.baseValue,
        configured.metaUrl.toString(),
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
