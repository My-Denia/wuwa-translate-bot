import { proxyMetaRequest, runtimeEnvironment } from '@/lib/wuwaterm-proxy.js';

export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  return proxyMetaRequest({ environment: await runtimeEnvironment() });
}
