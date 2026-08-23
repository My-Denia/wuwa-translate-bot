import { proxyMetaRequest } from '@/lib/wuwaterm-meta.js';

export const dynamic = 'force-dynamic';

export async function GET(): Promise<Response> {
  return proxyMetaRequest({ environment: await runtimeEnvironment() });
}

async function runtimeEnvironment(): Promise<Record<string, unknown>> {
  try {
    const { env } = await import('cloudflare:workers');
    return env as Record<string, unknown>;
  } catch {
    return process.env as Record<string, unknown>;
  }
}
