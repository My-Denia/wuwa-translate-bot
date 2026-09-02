import type { MetadataRoute } from 'next';
import { publicOrigin } from '@/lib/public-metadata';
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const origin = await publicOrigin();
  return origin ? ['', '/privacy', '/limits'].map(path => ({ url: origin + path })) : [];
}
