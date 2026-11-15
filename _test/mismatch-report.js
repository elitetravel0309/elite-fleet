// 生成「车型/价格错配复核清单」：直接复用工作台里的 diagnosePriceMismatch()，
// 保证报表与页面「错配复核」面板口径完全一致。
// 运行：node _test/mismatch-report.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
require('./dom-stub.js');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const scripts = [...html.matchAll(/<script[^>]*>(.*?)<\/script>/gs)].map(m => m[1]);
const data = JSON.parse(fs.readFileSync(path.join(root, 'data', 'elite-fleet-data.json'), 'utf8'));

const sandbox = {
  document: global.document, window: global.window, localStorage: global.localStorage,
  navigator: global.navigator, confirm: global.confirm, alert: global.alert,
  Blob: global.Blob, fetch: global.fetch, console,
  setTimeout: global.setTimeout, clearTimeout: global.clearTimeout,
  setInterval: global.setInterval, clearInterval: global.clearInterval,
  Date, Math, JSON, Object, Array, String, Number, Boolean, RegExp, Error,
  parseInt, parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent,
  location: global.location, btoa: s => Buffer.from(s).toString('base64')
};
sandbox.URL = Object.assign(Object.create(Object.getPrototypeOf(global.URL)), global.URL, {
  createObjectURL(){ return 'blob:x'; }, revokeObjectURL(){}
});
sandbox.global = sandbox; sandbox.self = sandbox;
vm.createContext(sandbox);
sandbox.localStorage.setItem('elite-fleet-v1', JSON.stringify(data));
vm.runInContext(scripts[0], sandbox, { filename: 'index.html.js' });

const { mismatch, unmatched, unpriced } = JSON.parse(JSON.stringify(vm.runInContext('diagnosePriceMismatch()', sandbox)));
const orders = data.orders.length, prices = data.prices.length;
const esc = s => String(s == null ? '' : s).replace(/\|/g, '\\|').replace(/\n/g, ' ');
const table = rows => [
  '| 日期 | 客人 | 订单车型 | 命中价(¥) | 参考价车型 | 线路 | 订单ID |',
  '|---|---|---|---|---|---|---|',
  ...rows.map(r => `| ${esc(r.date)} | ${esc(r.guest)} | ${esc(r.vehicle)} | ${r.value} | ${esc(r.priceVehicles.join(' / ') || '—')} | ${esc(r.priceRoutes.join(' / ') || r.route)} | ${esc(r.id)} |`)
].join('\n');

const lines = [
  '# 车型 / 价格错配复核清单',
  '',
  `- 生成时间：${new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}`,
  `- 数据：${orders} 个订单 / ${prices} 条参考价（data/elite-fleet-data.json）`,
  `- 诊断口径：与工作台「参考价库 → 错配复核」面板完全一致（diagnosePriceMismatch）`,
  '',
  '## 结论',
  '',
  `| 类别 | 数量 | 含义 |`,
  `|---|---|---|`,
  `| ① 车型档位不一致 | ${mismatch.length} | 命中了价格，但价格对应车型与订单车型不是同一档位，需要逐单确认是否套错价 |`,
  `| ② 价格来源不明 | ${unmatched.length} | 命中价格在同城参考价库里找不到对应记录，多为手工价 / 新线路 / 跨城价 |`,
  `| ③ 未命中参考价 | ${unpriced.length} | 定价引擎完全找不到可用价格，订单报价/成本靠人工填写 |`,
  '',
  '## ① 优先复核：车型档位不一致',
  '',
  mismatch.length ? table(mismatch) : '（无）',
  '',
  '## ② 价格来源不明（前 30 条）',
  '',
  unmatched.length ? table(unmatched.slice(0, 30)) : '（无）',
  '',
  '## ③ 未命中参考价（前 30 条）',
  '',
  unpriced.length ? table(unpriced.slice(0, 30)) : '（无）',
  '',
  '## 处理建议',
  '',
  '1. 每个「错配」订单：打开订单核对报价/成本 → 在工作台「参考价库」补一条 城市×线路×车型 的参考价 → 重新核对自动带出的价格。',
  '2. 「价格来源不明」若确为手工成交价，可在参考价库补录成交价（报表中心的「成交沉淀」可批量回填）。',
  '3. 不要直接改定价引擎的兜底逻辑：先逐单复核，避免一次性改动造成 90 单价格级联变化。',
  ''
];

const outDir = path.join(root, '_reports');
fs.mkdirSync(outDir, { recursive: true });
const out = path.join(outDir, '车型价格错配复核清单.md');
fs.writeFileSync(out, lines.join('\n'), 'utf8');
console.log(`订单 ${orders} / 参考价 ${prices}`);
console.log(`① 车型错配 ${mismatch.length} 单，② 来源不明 ${unmatched.length} 单，③ 未命中参考价 ${unpriced.length} 单`);
console.log('报告：' + out);
