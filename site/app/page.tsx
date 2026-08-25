import { TranslationWorkbench } from './components/translation-workbench';

export default function Home() {
  return (
    <main className="site-main">
      <div className="ambient ambient-one" aria-hidden="true" />
      <div className="ambient ambient-two" aria-hidden="true" />
      <nav className="topbar" aria-label="站点导航">
        <a className="brand" href="#top" aria-label="WuwaTerm 首页">
          <span className="brand-mark" aria-hidden="true">W</span>
          <span><strong>WuwaTerm</strong><small>鸣潮官方术语</small></span>
        </a>
        <span className="private-badge"><span aria-hidden="true" />私有翻译产品</span>
      </nav>
      <TranslationWorkbench />
      <footer><p>字典优先 · 官方术语 · 请求全程不在浏览器持有设备凭据</p></footer>
    </main>
  );
}
