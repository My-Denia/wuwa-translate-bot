import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'WuwaTerm 私有翻译工作台',
  description: '通过私有服务端路由连接 WuwaTerm 官方术语服务。',
  openGraph: {
    title: 'WuwaTerm 私有翻译工作台',
    description: '鸣潮官方术语 · 安全连接',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'WuwaTerm 私有翻译工作台',
    description: '鸣潮官方术语 · 安全连接',
  },
  robots: {
    index: false,
    follow: false,
    nocache: true,
    googleBot: { index: false, follow: false, noarchive: true },
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
