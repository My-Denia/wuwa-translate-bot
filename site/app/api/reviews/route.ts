import {
  parseReviewRequest,
  proxyReviewRequest,
  runtimeEnvironment,
} from '@/lib/wuwaterm-proxy.js';

export const dynamic = 'force-dynamic';

export async function POST(request: Request): Promise<Response> {
  const parsed = await parseReviewRequest(request);
  if (!parsed.ok) return parsed.response;
  return proxyReviewRequest({ environment: await runtimeEnvironment(), input: parsed.input });
}
