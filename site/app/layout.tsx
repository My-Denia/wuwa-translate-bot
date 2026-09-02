import type { Metadata } from 'next';
import { publicOrigin } from '@/lib/public-metadata';
import './globals.css';

export async function generateMetadata(): Promise<Metadata> {
  const origin = await publicOrigin();
  const title = 'WuwaTerm · 鸣潮中英术语与整句翻译';
  const description = '查找鸣潮官方中英术语，字典优先、保留术语的整句翻译。全站共享公测额度，先到先用，不保证个人公平。';
  return {
    title, description,
    ...(origin ? { metadataBase: new URL(origin), alternates: { canonical: '/' } } : {}),
    openGraph: { title, description, locale: 'zh_CN', type: 'website', ...(origin ? { url: origin, images: [{ url: origin + '/og.png', alt: title }] } : {}) },
    twitter: { card: 'summary_large_image', title, description, ...(origin ? { images: [origin + '/og.png'] } : {}) },
    robots: { index: !!origin, follow: !!origin, nocache: true },
  };
}
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
