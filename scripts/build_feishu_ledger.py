# -*- coding: utf-8 -*-
"""飞书台账建表脚本：在主库创建 订单台账/收款记录/参考价库 三张表（幂等：表已存在则跳过）。
用法: python scripts/build_feishu_ledger.py
"""
import json, subprocess, sys
from pathlib import Path

BASE_TOKEN = 'Dfd8bANo9ar33Ns03Iicrxnp6Ec'   # 邮件询盘工作台（统一后台主库）
SCRIPT_DIR = Path(__file__).resolve().parent
TABLES = [
    ('订单台账', 'orders'),
    ('收款记录', 'payments'),
    ('参考价库', 'prices'),
]

def cli(*args):
    r = subprocess.run(['lark-cli', 'base', *args, '--as', 'user'],
                       capture_output=True, text=True, encoding='utf-8')
    if r.returncode != 0:
        print('ERR:', ' '.join(args), r.stderr[:800] or r.stdout[:800], file=sys.stderr)
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        print('WARN non-json:', r.stdout[:300])
        return r.stdout

def main():
    blocks = cli('+base-block-list', '--base-token', BASE_TOKEN)
    existing = set()
    if blocks and blocks.get('ok'):
        for b in blocks['data']['blocks']:
            if b.get('type') == 'table':
                existing.add(b['name'])
    for name, key in TABLES:
        if name in existing:
            print('跳过（已存在）:', name)
            continue
        fields_path = SCRIPT_DIR.parent / ('_tbl_%s.json' % key)
        fields = json.loads(fields_path.read_text(encoding='utf-8'))
        r = cli('+table-create', '--base-token', BASE_TOKEN, '--name', name,
                '--fields', json.dumps(fields, ensure_ascii=False))
        if r and r.get('ok'):
            print('已创建:', name, r.get('data', {}).get('table_id') or '')
        else:
            print('创建失败:', name)

if __name__ == '__main__':
    main()
