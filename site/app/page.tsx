import { MetaPanel } from './components/meta-panel';

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
        <span className="private-badge"><span aria-hidden="true" />私有可行性预览</span>
      </nav>
      <section className="hero" id="top">
        <div className="hero-copy">
          <p className="eyebrow">PRIVATE FEASIBILITY PREVIEW</p>
          <h1>翻译工作台，<span>从可信服务开始。</span></h1>
          <p className="hero-description">
            此页面只验证 WuwaTerm 私有站点能否通过同源服务端路由安全读取版本与术语规模。翻译和术语检索将在验证通过后启用。
          </p>
          <div className="architecture" aria-label="请求架构">
            <span>浏览器</span><i aria-hidden="true">→</i>
            <span>本站私有路由</span><i aria-hidden="true">→</i>
            <span>WuwaTerm API</span>
          </div>
        </div>
        <MetaPanel />
      </section>
      <footer><p>字典优先 · 官方术语 · 请求全程不在浏览器持有设备凭据</p></footer>
    </main>
  );
}
