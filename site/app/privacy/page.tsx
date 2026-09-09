import Link from 'next/link';
import { documentMetadata } from '@/lib/public-metadata';
export async function generateMetadata() { return documentMetadata('隐私说明 · WuwaTerm', 'WuwaTerm 共享公测池的输入、隐私和数据保留说明。', '/privacy'); }
export default function Privacy() {
  return <main className="document"><Link className="back-link" href="/">← 返回 WuwaTerm</Link><p className="eyebrow">PRIVACY</p><h1>只为这一次查询与翻译。</h1><p className="document-lead">没有账户，也不建立你的使用档案。请勿提交个人信息、密码或其他敏感内容。</p>
    <section><h2>我们处理什么</h2><p>主动查询、翻译或核对时，相应文本会传送到服务端以生成结果。翻译优先匹配词典，需要整句翻译时才交由模型处理。审校只检查官方术语和对应范围，不调用整句模型，也不认证完整句意。</p></section>
    <section><h2>本地稿件文件</h2><p>“保存稿件”将原文、译文、方向、局部选择依据、分段对应及最近两份报告下载为本地文件。“导入稿件”只在当前浏览器解析文件，不会自动上传或发起核对。下次继续编辑时，需要你主动导入文件；本站不建立云端稿件历史。</p><p>文件包含你保存的文字，请自行妥善保管。导入的报告和通过标记不构成当前认证，必须重新取得服务端依据；变化或无法可靠恢复的选择需要重新确认。</p></section>
    <section><h2>共享额度记录什么</h2><p>额度存储只包含全站的时间窗口、已获准请求数和翻译输入字符总数。它不包含输入、译文、请求编号、访客 IP、浏览器标识或个人使用记录。</p><p>WuwaTerm 应用不读取、解析、存储或推断访客 IP，不生成访客代号，不按 IP 或个人分配额度。</p></section>
    <section><h2>历史与技术记录</h2><p>本站不在云端保存查询、翻译或稿件历史，也不将输入写入浏览器存储。刷新或关闭页面会清空页面中的结果；你主动下载的稿件文件由你保存。问题反馈编号只用于排查单次请求，不用于识别访客。</p><p>网站托管、网络传输与翻译服务可能为运行、安全和故障排查处理技术数据。这里的说明不代表所有基础设施均不产生日志，也不承诺模型服务的零保留。</p></section>
    <section><h2>无需登录的共享使用</h2><p>本站当前公开访问，产品不设 WuwaTerm 账户或登录系统。托管平台仍可能实施自身的安全与访问控制；这类平台控制不创建个人额度，也不等于 WuwaTerm 账户。</p><p>关于共享额度、重置与中断，请阅读<a href="/limits">使用与限额说明</a>。</p></section><footer>说明更新：2026-09-10</footer>
  </main>;
}
