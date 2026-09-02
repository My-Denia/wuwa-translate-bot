import { acceptancePage } from '@/lib/owner-acceptance.js';
import { runtimeEnvironment } from '@/lib/wuwaterm-proxy.js';
export const dynamic = 'force-dynamic';
export async function GET(): Promise<Response> {
  return acceptancePage(await runtimeEnvironment());
}
