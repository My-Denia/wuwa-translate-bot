import type { MetadataRoute } from 'next';
import { publicOrigin } from '@/lib/public-metadata';
export default async function robots(): Promise<MetadataRoute.Robots> {
  const origin = await publicOrigin();
  return origin ? { rules: { userAgent: '*', allow: '/', disallow: '/api/' }, sitemap: origin + '/sitemap.xml' } : { rules: { userAgent: '*', disallow: '/' } };
}
