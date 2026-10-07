# -*- coding: utf-8 -*-
"""飞书台账同步脚本：把 elite-fleet 工作台数据（orders/payments/prices）同步到飞书主库三张台账表。
幂等：按业务键（订单编号 / 记录编号 / 城市+线路+车型）查找已有记录，有则更新、无则新建。
用法:
  python scripts/push_feishu_ledger.py                       # 默认读 data/elite-fleet-data.json
  python scripts/push_feishu_ledger.py --data ledger.json    # 指定导出台账文件
  python scripts/push_feishu_ledger.py --dry-run             # 只打印计划不写
"""
import argparse, json, subprocess, sys, tempfile
from pathlib import Path
from datetime import date

BASE_TOKEN = 'Dfd8bANo9ar33Ns03Iicrxnp6Ec'
TABLES = {
    'orders':   {'table': '订单台账', 'table_id': 'tblCSAQAB0yJ4UI8', 'key': ['订单编号']},
    'payments': {'table': '收款记录', 'table_id': 'tblIcXUXj3CwUki1', 'key': ['记录编号']},
    'prices':   {'table': '参考价库', 'table_id': 'tblCUxwXWBlaBPRK', 'key': ['城市', '线路', '车型']},
}


def row_key(row, key_fields):
    return '|'.join(str(row.get(f) or '').strip() for f in key_fields)
STATUS_MAP = {'inquiry': '询价中', 'quoted': '已报价', 'pending': '待确认',
              'confirmed': '已确认', 'done': '已完成', 'cancelled': '已取消'}
TYPE_MAP = {'arrival': '接机', 'departure': '送机', 'charter': '包车', 'transfer': '市内接送'}
METHOD_MAP = {'Alipay': 'alipay', 'Wechat': 'wechat', 'Cash': 'cash', 'Bank': 'bank',
              'Card': 'card', 'PayPal': 'PayPal', 'Other': 'other'}


def cli(*args):
    r = subprocess.run(['lark-cli', 'base', *args, '--as', 'user'],
                       capture_output=True, text=True, encoding='utf-8')
    if r.returncode != 0:
        print('ERR:', ' '.join(args)[:200], r.stderr[:500] or r.stdout[:500], file=sys.stderr)
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        print('WARN non-json:', r.stdout[:200])
        return None


def cli_json_body(cmd, body):
    """body 为 JSON 对象；写入临时文件后以 @file 传给 --json，避免命令行长度限制。"""
    with tempfile.NamedTemporaryFile('w', suffix='.json', encoding='utf-8', delete=False) as f:
        f.write(json.dumps(body, ensure_ascii=False))
        path = f.name
    try:
        return cli(*cmd, '--json', '@' + path)
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass


def list_all(table_id, key_fields, tmp='_ledger_map.ndjson'):
    """+record-list ndjson 全量导出到临时文件，返回 {业务键(复合): record_id} 与记录数。"""
    from pathlib import Path as _P
    tmp = _P(tmp)
    if tmp.exists():
        tmp.unlink()
    r = cli('+record-list', '--base-token', BASE_TOKEN, '--table-id', table_id,
            '--format', 'ndjson', '--output', str(tmp), '--overwrite')
    if not r or 'record_file' not in r:
        print('  WARN: 无法导出记录清单', file=sys.stderr)
        return {}, 0
    rf = _P(r['record_file'])
    mapping = {}
    total = 0
    if rf.exists():
        for line in rf.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            total += 1
            if row.get('record_id'):
                mapping[row_key(row, key_fields)] = row['record_id']
    return mapping, total


def order_fields(o, payments_by_order):
    paid = o.get('price') or 0
    quote = o.get('quote') or 0
    pay_date = ''
    for p in payments_by_order.get(o.get('id', ''), []):
        if p.get('date'):
            pay_date = p['date'][:10]
    return {
        '订单编号': o.get('id', ''),
        '服务日期': (o.get('date') or '')[:10] + ' 00:00:00' if o.get('date') else None,
        '服务时间': o.get('time') or '',
        '类型': TYPE_MAP.get(o.get('type'), '包车'),
        '城市': o.get('city') or '',
        '路线': o.get('route') or '',
        '客人': o.get('guest') or '',
        '联系方式': o.get('contact') or '',
        '邮箱': o.get('email') or '',
        '车型': o.get('vehicle') or '',
        '人数': int(o['pax']) if o.get('pax') else None,
        '司机': o.get('driver') or '',
        '车牌': o.get('plate') or '',
        '车队': o.get('fleet') or '',
        '报价': quote,
        '成本': o.get('cost') or 0,
        '利润': round(quote - (o.get('cost') or 0), 2),
        '已收金额': paid,
        '待收金额': round(max(quote - paid, 0), 2),
        '全清': bool(quote > 0 and paid >= quote),
        '收款日期': pay_date[:10] + ' 00:00:00' if pay_date else None,
        '状态': STATUS_MAP.get(o.get('status'), '已报价'),
        '备注': o.get('note') or '',
        '最后修改': date.today().strftime('%Y-%m-%d') + ' 00:00:00',
    }


def payment_fields(p):
    return {
        '记录编号': p.get('id', ''),
        '类型': '收款',
        '日期': (p.get('date') or '')[:10] + ' 00:00:00' if p.get('date') else None,
        '金额': p.get('amount') or 0,
        '关联订单': p.get('orderId') or '',
        '客人': p.get('guest') or '',
        '车队': p.get('fleet') or '',
        '方式': METHOD_MAP.get(p.get('method'), 'other'),
        '状态': '完成' if p.get('status') == 'received' else '取消',
        '备注': p.get('note') or '',
    }


def price_fields(p):
    return {
        '城市': p.get('city') or '',
        '线路': p.get('route') or '',
        '车型': p.get('vehicle') or '',
        '时长': p.get('duration') or '',
        '距离': p.get('distance') or '',
        '成本': p.get('cost') or 0,
        '报价': p.get('quote') or 0,
        '备注': p.get('note') or '',
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data/elite-fleet-data.json')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    data = json.loads(Path(args.data).read_text(encoding='utf-8'))

    payments_by_order = {}
    for p in data.get('payments', []):
        payments_by_order.setdefault(p.get('orderId', ''), []).append(p)

    jobs = [
        ('orders',   [order_fields(o, payments_by_order) for o in data.get('orders', [])]),
        ('payments', [payment_fields(p) for p in data.get('payments', [])]),
        ('prices',   [price_fields(p) for p in data.get('prices', [])]),
    ]
    for key, rows in jobs:
        spec = TABLES[key]
        mapping, total = list_all(spec['table_id'], spec['key'])
        print('[%s] 库内已有 %d 条，待同步 %d 条' % (spec['table'], total, len(rows)))
        created = updated = skipped = 0
        # 分批：待新建（无 record_id）与待更新（有 record_id）分别批量提交
        to_create, to_update = [], []
        for row in rows:
            kv = row_key(row, spec['key'])
            if not kv:
                skipped += 1
                continue
            payload = {k: v for k, v in row.items() if v is not None}
            rid = mapping.get(kv)
            if rid:
                to_update.append((rid, payload))
            else:
                to_create.append(payload)
        if args.dry_run:
            print('  CREATE %d / UPDATE %d / SKIP %d' % (len(to_create), len(to_update), skipped))
            continue
        # 批量创建
        for i in range(0, len(to_create), 200):
            chunk = to_create[i:i + 200]
            r = cli_json_body(['+record-batch-create', '--base-token', BASE_TOKEN,
                               '--table-id', spec['table_id']], {'create_records': chunk})
            if r and r.get('ok'):
                created += len(chunk)
            else:
                print('  BATCH CREATE FAIL:', spec['table'], 'chunk', i // 200)
        # 批量更新
        for i in range(0, len(to_update), 200):
            chunk = to_update[i:i + 200]
            r = cli_json_body(['+record-batch-update', '--base-token', BASE_TOKEN,
                               '--table-id', spec['table_id']],
                              {'update_records': {rid: p for rid, p in chunk}})
            if r and r.get('ok'):
                updated += len(chunk)
            else:
                print('  BATCH UPDATE FAIL:', spec['table'], 'chunk', i // 200)
        if not args.dry_run:
            print('  → 新建 %d / 更新 %d / 跳过 %d' % (created, updated, skipped))


if __name__ == '__main__':
    main()
