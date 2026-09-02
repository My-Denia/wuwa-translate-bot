import { runtimeEnvironment } from './wuwaterm-proxy.js';
import type { Metadata } from 'next';

// This value is set only after the separately authorized public access gate.
// Never derive an SEO origin from visitor-controlled request headers.
export async function publicOrigin(): Promise<string | null> {
  const env = await runtimeEnvironment();
  const value = env.WUWATERM_PUBLIC_ORIGIN;
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || !url.hostname.endsWith('.chatgpt.site') || url.username || url.password || url.port || url.search || url.hash || url.pathname !== '/' || value !== url.origin) return null;
    return url.origin;
  } catch { return null; }
}

export async function documentMetadata(title: string, description: string, path: '/privacy' | '/limits'): Promise<Metadata> {
  const origin = await publicOrigin();
  return { title, description, ...(origin ? { alternates: { canonical: origin + path }, openGraph: { title, description, url: origin + path } } : {}) };
}
