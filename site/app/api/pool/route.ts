import { poolStatus } from '@/lib/shared-pool.js';
import { runtimeEnvironment } from '@/lib/wuwaterm-proxy.js';
export const dynamic = 'force-dynamic';
export async function GET(): Promise<Response> {
  return poolStatus(await runtimeEnvironment());
}
