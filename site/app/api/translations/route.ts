import {
  parseTranslationRequest,
  proxyTranslationRequest,
  runtimeEnvironment,
} from '@/lib/wuwaterm-proxy.js';

export const dynamic = 'force-dynamic';

export async function POST(request: Request): Promise<Response> {
  const parsed = await parseTranslationRequest(request);
  if (!parsed.ok) return parsed.response;
  return proxyTranslationRequest({ environment: await runtimeEnvironment(), input: parsed.input });
}
