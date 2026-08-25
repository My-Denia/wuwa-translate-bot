import { parseTermsRequest, proxyTermsRequest, runtimeEnvironment } from '@/lib/wuwaterm-proxy.js';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  const parsed = parseTermsRequest(request);
  if (!parsed.ok) return parsed.response;
  return proxyTermsRequest({ environment: await runtimeEnvironment(), query: parsed.query });
}
