import { handleAcceptance } from '@/lib/owner-acceptance.js';
import { runtimeEnvironment } from '@/lib/wuwaterm-proxy.js';
export const dynamic = 'force-dynamic';
export async function POST(request: Request): Promise<Response> {
  return handleAcceptance(request, await runtimeEnvironment());
}
