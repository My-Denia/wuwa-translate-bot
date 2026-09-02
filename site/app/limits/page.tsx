import Link from 'next/link';
import { POOL_LIMITS as limits } from '@/lib/shared-pool.js';
import { documentMetadata } from '@/lib/public-metadata';
export async function generateMetadata() { return documentMetadata('共享额度 · WuwaTerm', '全站共用、先到先用：了解 WuwaTerm 公测额度、重置和故障处理。', '/limits'); }
export default function Limits() {
  return <main className="document"><Link className="back-link" href="/">← 返回 WuwaTerm</Link><p className="eyebrow">SHARED BETA POOL</p><h1>大家共用一份额度。</h1><p className="document-lead">一个访客可能用完整个共享池。我们限制总使用量，不保证每个人都能分到额度。</p>
    <section><h2>当前候选额度</h2><table><thead><tr><th>功能</th><th>全站上限</th></tr></thead><tbody><tr><td>术语查询</td><td>{limits.termsPerDay} 次 / UTC 日</td></tr><tr><td>整句翻译</td><td>{limits.translationsPerDay} 次，且输入合计 {limits.charactersPerDay.toLocaleString('zh-CN')} 字符 / UTC 日</td></tr><tr><td>所有服务请求</td><td>每自然分钟 {limits.upstreamPerMinute} 次，每秒最多 1 次</td></tr><tr><td>整句翻译短窗口</td><td>每自然分钟 {limits.translationsPerMinute} 次</td></tr></tbody></table><p>每日额度于 UTC 00:00（北京时间 08:00）重置。术语输入最多 200 字符，整句最多 2,000 字符。整句翻译可能暂未开放，以首页状态为准。</p></section>
    <section><h2>翻译用完了，仍可查术语</h2><p>查询池与翻译池相互独立。翻译关闭或日额度用尽，不会扣减或关闭查询池。查询仍受自身日额度、全站短窗口和服务可用性限制。</p></section>
    <section><h2>请求获准时计数</h2><p>不是只计算成功结果：请求获准即消耗额度，字典命中也计作一次翻译请求。失败、超时、取消等待或连接中断可能已消耗额度，不返还，也不会自动重发。整句字数按提交的 Unicode 字符计数，不是模型 token。</p><p>遇到繁忙提示，请等待建议时间后手动重试。无法确认额度时暂停服务，恢复后继续使用剩余额度。</p></section>
    <section><h2>这些限制能做到什么</h2><p>它们限制本站向翻译服务发出的请求次数及整句输入总量，帮助约束服务负载与模型调用成本。费用还取决于服务端处理和定价，这不是固定金额的账单保证。</p><p>它们不能保证访客公平、随时可用或抵御所有滥用，也不能限制到达网站的请求量、托管及额度存储成本。没有单访客、单 IP 或个人公平限额。</p></section>
    <footer><a href="/privacy">隐私说明</a> · 候选额度更新：2026-09-02</footer>
  </main>;
}
