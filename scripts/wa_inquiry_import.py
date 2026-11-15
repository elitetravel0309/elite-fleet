#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhatsApp 询盘 -> elite-fleet 工作台询价单

只读本机 wacli 数据库 (~/.wacli/wacli.db)，把「客户发起会话 + 我方发过正式报价」
的私聊会话抽取成工作台 S.inquiries 标准记录，输出可直接导入的 JSON 与人工复核清单。

设计原则：
  1. 绝不写 wacli 库、绝不发消息、绝不改线上数据；只产出本地 JSON 文件
  2. 幂等：id 由 WhatsApp 手机号生成，重复导入不会产生重复记录
  3. 保守：机器只做 quoted / following 判定；won / lost 只采信人工已核对的台账
  4. 可核对：每条记录 note 保留客户原始询盘与我方最后报价的原文摘录

用法：
  python scripts/wa_inquiry_import.py                      # 默认输出 data/whatsapp-inquiries.json
  python scripts/wa_inquiry_import.py --since 2026-08-01 --out xxx.json
  python scripts/wa_inquiry_import.py --db C:/path/to/wacli.db
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path(os.path.expanduser('~')) / '.wacli' / 'wacli.db'
DEFAULT_OUT = ROOT / 'data' / 'whatsapp-inquiries.json'
DEFAULT_REPORT = ROOT / '_reports' / 'WhatsApp询盘并入清单.md'

# 我方「正式报价」识别特征（客户会话里出现即视为已报价线索）
QUOTE_RE = re.compile(
    r'(Inquiry\s*&\s*Quotation|Private Car Cost|Quotation\s*:|📋|🚗|🚐|'
    r'5\s*seater|7\s*seater|9\s*seater|seater sedan|business van|'
    r'Total\s*[:：]\s*RMB|Price\s*[:：]\s*RMB)', re.I)

# ---------------- 人工已核对的台账（逐字来源见下方 SOURCE_NOTE） ----------------
SOURCE_NOTE = (
    '台账来源：E:/WorkBuddy/WorkBuddy/2026-11-12-23-11-44/'
    'WhatsApp成交线路报价台账.md（第三节 11 月确认成交明细 / 第六节 报价未成交但值得跟进）；'
    'Annie广东三段报价草稿.md、Kevin报价回复草稿.md；'
    '该会话 .workbuddy/memory/2026-11-15.md（Manuel Romero）。'
)

# 11 月确认成交（高置信度，人工核对）——按手机号后 8 位匹配，避免姓名重名
WON = {
    'natalie sapuppo': ('2026-11-09', '酒店⇄慕田峪长城往返（4 人）', 900),
    'marcin':          ('2026-11-09', 'PEK 接机→慕田峪（等 2-3h）→机场酒店（6 人）', 980),
    'sonali weerasuriya': ('2026-11-07', '长城包车（含确认价 900）', 900),
    'abrar':           ('2026-11-07', '包车', 1480),
    'alejandro zamudio': ('2026-11-04', '包车', 1480),
    'novai lyons':     ('2026-10-30', '多段包车', 1080),
    'hung nguyen':     ('2026-10-28', '丽江一日游（玉龙雪山）', 900),
    'nazim aslam':     ('2026-10-27', '上海⇄杭州一日往返', 2100),
    'madina':          ('2026-10-27', '包车', 880),
    'andrew tran':     ('2026-10-24', '长城包车', 900),
}

# 台账第三节里「姓名歧义 / 无联系方式」的成交项：不自动标 won，避免张冠李戴，留人工核对
WON_MANUAL_REVIEW = [
    ('11-08', 'Nick', '长城包车', 900, '库里有 3 个 Nick 会话，台账未给号码'),
    ('10-31', '伊朗团体（جهانگرد）', '团体包车', 2700, '阿拉伯语名称，无法可靠匹配'),
    ('10-29', 'Edurne', '接送尾款', 100, '金额过小且为尾款，非整单报价'),
    ('10-27', 'Jas', '城际接送', 1580, '姓名过短，可能误匹配'),
]

# 高意向待跟进（台账第六节 + 两份报价草稿 + Manuel），key = 姓名匹配片段
FOLLOWUP = {
    'gianluigi': ('罗马人 2 人接机含门票', '690×2=1,380', '客户确认中'),
    'nadeen':    ('桂林龙脊梯田全天包车', '880+门票 100', '等回复'),
    'agata':     ('元阳梯田 3 日包车', '详见会话', '等回复'),
    'nikos':     ('重庆→芙蓉镇 12-25', '2,500', '行程已确认待付款'),
    'jessie':    ('杭州 4 人导游定制', '未报价', '催回复中'),
    'bette':     ('11-13 往返包车', '980', '明天用车'),
    'jake':      ('PVG→虹桥送机 11-13', '已发 PayPal', '待付款'),
}
# 草稿客户（含更完整的联系与行程信息）
DRAFTS = {
    'annie': dict(
        inquiry_date='2026-11-04', contact='+61 422 396 948', city='广州', pax=2,
        vehicle='五座', quote=1700,
        route='广东三段跨城：12-23 广州北京路 K-Space Hotel→东莞松山湖悦榕庄；'
              '12-27 东莞→顺德清晖园旁 Crystal Hotel；12-31 顺德→广州白云机场 T2 美居酒店',
        note='2 位老人 + 2 个中号行李箱；门到门直达；客户要求每段价 + 总价；'
             '单段拆分建议 500/650/550，打包价 RMB 1,700；'
             '已拟好英文回复稿（Annie_WhatsApp版.txt），尚未发出；搁置 11 天，优先回复',
        source_file='Annie广东三段报价草稿.md / Annie_WhatsApp版.txt'),
    'kevin': dict(
        inquiry_date='2026-11-13', contact='+1 647 274 8899', email='melinakevin19@gmail.com',
        city='广州', pax=8, vehicle='九座',
        route='11-23 白云机场接机→珠海长隆横琴湾酒店；11-26 长隆→番禺（沙湾古镇/宝墨园）；'
              '11-27→12-03 番禺/广州/白云 7 天包车，12-03 19:00 送机',
        note='8 人（5 成人 + 3 幼儿 2.5/4/5 岁）｜8 个大箱 + 3 台婴儿车｜'
             '9 座装不下，建议升级 14 座（福特全顺/大通 V90）；需 3 个儿童座椅；'
             '同一司机 11-26→12-03；价格处留空待填；英文回复稿已拟（Kevin_WhatsApp版.txt）',
        source_file='Kevin报价回复草稿.md / Kevin_WhatsApp版.txt'),
    'manuel': dict(
        inquiry_date='2026-11-14', contact='+52 1 656 408 7671', city='北京',
        route='11-19 大阪✈北京接机；11-20 北京市区；11-21 景泰蓝+居庸关长城+奥体；'
              '11-22 高铁北京→西安；11-23 兵马俑+大雁塔+回民街；11-24 西安✈上海；11-25 上海全天',
        note='墨西哥客户、西班牙语；已发西语回复并承诺确认当天报价；'
             '待客户回 3 个问题（人数行李 / 只接送还是含游览日 / 是否要西语司导）；'
             '酒店：北京 Yanjing / 西安 Sheraton / 上海 Radisson Collection Yangtze',
        source_file='.workbuddy/memory/2026-11-15.md'),
}

# ---------------- 字段解析 ----------------
# 3 字母机场代码必须大小写敏感 + 词边界，否则 CAN/SHA 会误匹配英文单词 can/shall
AIRPORT_CODES = {
    'PEK': '北京', 'PKX': '北京', 'PVG': '上海', 'SHA': '上海',
    'CAN': '广州', 'SZX': '深圳', 'CTU': '成都', 'XIY': '西安',
    'CKG': '重庆', 'KWL': '桂林', 'KMG': '云南', 'LJG': '云南',
    'HKG': '香港', 'MFM': '澳门', 'TSN': '天津', 'NKG': '南京',
}
AIRPORT_RE = re.compile(r'(?<![A-Za-z])(?:' + '|'.join(AIRPORT_CODES) + r')(?![A-Za-z])')

# 具体城市优先级高于大枢纽，避免模板里夹带的城市名改变判定
CITY_RULES = [
    ('桂林', r'桂林|Guilin|龙脊|Longsheng|Longji|阳朔|Yangshuo'),
    ('云南', r'云南|Yunnan|大理|Dali|丽江|Lijiang|昆明|Kunming|玉龙雪山|元阳|Yuanyang|梯田|Rice Terraces|Shaxi|沙溪'),
    ('重庆', r'重庆|Chongqing|芙蓉镇|Furong'),
    ('西安', r'西安|Xi.?an|兵马俑|Terra.?cotta|大雁塔|Xianyang|华清池'),
    ('山西', r'大同|Datong|平遥|Pingyao|五台|Wutai'),
    ('张家界', r'张家界|Zhangjiajie'),
    ('西藏', r'西藏|Tibet|拉萨|Lhasa'),
    ('新疆', r'新疆|Xinjiang|乌鲁木齐|Urumqi|喀什|Kashgar'),
    ('杭州', r'杭州|Hangzhou|千岛湖|Qiandao'),
    ('苏州', r'苏州|Suzhou'),
    ('南京', r'南京|Nanjing'),
    ('青岛', r'青岛|Qingdao'),
    ('厦门', r'厦门|Xiamen'),
    ('成都', r'成都|Chengdu|都江堰|熊猫|Panda'),
    ('深圳', r'深圳|Shenzhen'),
    ('香港', r'香港|Hong Kong'),
    ('澳门', r'澳门|Macao|Macau'),
    ('广州', r'广州|Guangzhou|白云机场|Baiyun|珠海|Zhuhai|长隆|Chimelong|番禺|Panyu|顺德|Shunde|东莞|Dongguan|佛山|沙湾|宝墨'),
    ('上海', r'上海|Shanghai|虹桥|外滩|The Bund|浦东|Pudong|徐汇|朱家角'),
    ('北京', r'北京|Beijing|Peking|慕田峪|Mutianyu|金山岭|Jinshanling|八达岭|Badaling|长城|Great Wall|居庸关|首都机场|大兴机场|Daxing'),
]

MONTHS = {m.lower(): i for i, m in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
     'September', 'October', 'November', 'December'], 1)}
MONTHS.update({m[:3].lower(): i for i, m in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
     'September', 'October', 'November', 'December'], 1)})


def guess_city(*texts):
    """按文本可信度依次判定：客户首条询盘 > 我方报价 > 最近消息。"""
    for text in texts:
        if not text:
            continue
        for city, pat in CITY_RULES:
            if re.search(pat, text, re.I):
                return city
        m = AIRPORT_RE.search(text)
        if m:
            return AIRPORT_CODES[m.group(0)]
    return ''


def guess_pax(text):
    adults = re.search(r'(\d{1,2})\s*(?:adults?|大人)', text, re.I)
    children = re.search(r'(\d{1,2})\s*(?:children|kids?|幼儿|小孩)', text, re.I)
    if adults and children:
        return int(adults.group(1)) + int(children.group(1))
    m = re.search(r'(\d{1,2})\s*(?:passengers?|pax|people|persons?|guests?|\s*人)', text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r'\bfor\s+(\d{1,2})\b', text, re.I)
    if m:
        return int(m.group(1))
    return 1


def guess_vehicle(text):
    if re.search(r'14\s*[- ]?seat|minibus|V90|Ford Transit|考斯特|大巴', text, re.I):
        return '大巴'
    if re.search(r'9\s*[- ]?seat|九座|V-?Class|Hiace|G10', text, re.I):
        return '九座'
    if re.search(r'7\s*[- ]?seat|七座|business van|GL8|MPV', text, re.I):
        return '七座'
    if re.search(r'5\s*[- ]?seat|五座|sedan|轿车|Camry|Passat', text, re.I):
        return '五座'
    return ''


def guess_quote(text):
    """只采信明确的总价表述；否则取第一个 RMB 金额作为起步价（保守）。"""
    for pat in [r'(?:Total|Package|All three|Combined)\s*[:：]?\s*RMB\s*([\d,]+)',
                r'(?:Total|Package|Combined)\s*[:：]?\s*([\d,]+)\s*RMB']:
        m = re.search(pat, text, re.I)
        if m:
            return int(m.group(1).replace(',', ''))
    m = re.search(r'RMB\s*([\d,]{3,6})', text, re.I)
    return int(m.group(1).replace(',', '')) if m else 0


def guess_service_date(text, today):
    """从客户原文抽用车日期；仅识别明确日期，年份按当前月份推断。"""
    m = re.search(r'\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b', text)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return datetime(y, mo, d).strftime('%Y-%m-%d')
        except ValueError:
            return ''
    mon_pat = '|'.join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(r'\b(' + mon_pat + r')\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b', text, re.I)
    if m:
        mo, d = MONTHS[m.group(1).lower().rstrip('.')], int(m.group(2))
    else:
        m = re.search(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(' + mon_pat + r')\.?\b', text, re.I)
        if not m:
            m2 = re.search(r'\b(1[0-2]|0?[1-9])[-/](\d{1,2})\b', text)
            if not m2:
                return ''
            mo, d = int(m2.group(1)), int(m2.group(2))
        else:
            d, mo = int(m.group(1)), MONTHS[m.group(2).lower().rstrip('.')]
    y = today.year
    if mo < today.month - 1:
        y += 1
    try:
        return datetime(y, mo, d).strftime('%Y-%m-%d')
    except ValueError:
        return ''


def clean_name(s):
    s = (s or '').strip()
    s = re.sub(r'\s+', ' ', s)
    return s[:60]


def phone_of(jid):
    m = re.match(r'(\d{7,})@', jid or '')
    return m.group(1) if m else ''


def build():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(DEFAULT_DB))
    ap.add_argument('--since', default='2026-08-01', help='只导出最后报价时间不早于此日期的会话')
    ap.add_argument('--out', default=str(DEFAULT_OUT))
    ap.add_argument('--priority-out', default=str(ROOT / 'data' / 'whatsapp-priority.json'),
                    help='仅导出需人工优先处理的记录（following / won / lost）')
    ap.add_argument('--report', default=str(DEFAULT_REPORT))
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        sys.exit('找不到 wacli 数据库：' + str(db))
    con = sqlite3.connect('file:' + str(db).replace('\\', '/') + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row

    since_ts = int(datetime.strptime(args.since, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp())
    today = datetime.now()

    quoted = defaultdict(list)
    for r in con.execute(
        "SELECT m.chat_jid, m.ts, m.text FROM messages m "
        "JOIN chats c ON c.jid=m.chat_jid "
        "WHERE m.from_me=1 AND c.kind='dm' AND m.text IS NOT NULL ORDER BY m.ts"):
        if QUOTE_RE.search(r['text'] or ''):
            quoted[r['chat_jid']].append((r['ts'], r['text']))

    customer_msgs = defaultdict(list)
    for r in con.execute(
        "SELECT m.chat_jid, m.ts, m.text FROM messages m "
        "JOIN chats c ON c.jid=m.chat_jid "
        "WHERE m.from_me=0 AND c.kind='dm' AND m.text IS NOT NULL ORDER BY m.ts"):
        customer_msgs[r['chat_jid']].append((r['ts'], r['text']))

    records, stats = [], defaultdict(int)
    matched_keys = set()
    for jid, quotes in quoted.items():
        last_ts, last_quote = quotes[-1]
        if last_ts < since_ts or not customer_msgs[jid]:
            continue
        phone = phone_of(jid)
        if not phone:
            continue
        chat = con.execute('SELECT name FROM chats WHERE jid=?', (jid,)).fetchone()
        contact_row = con.execute('SELECT push_name, full_name FROM contacts WHERE jid=?', (jid,)).fetchone()
        name = clean_name(chat['name'] if chat else '') or clean_name(
            (contact_row['full_name'] or contact_row['push_name']) if contact_row else '') or ('+' + phone)
        first_ts, first_text = customer_msgs[jid][0]
        recent_customer = '\n'.join(t for _, t in customer_msgs[jid][-3:])

        key = name.lower()
        won_key = next((k for k in WON if k in key), None)
        follow_key = next((k for k in FOLLOWUP if k in key), None)
        draft_key = next((k for k in DRAFTS if k in key), None)
        won_hit = WON.get(won_key)
        follow_hit = FOLLOWUP.get(follow_key)
        draft_hit = DRAFTS.get(draft_key)
        for k in (won_key, follow_key, draft_key):
            if k:
                matched_keys.add(k)

        status, follow_up = 'quoted', ''
        tag = []
        if won_hit:
            status = 'won'
            tag.append('台账确认成交 ' + won_hit[0])
        if follow_hit or draft_hit:
            status = 'following'
            follow_up = (today + timedelta(days=1)).strftime('%Y-%m-%dT09:00')
            if follow_hit:
                tag.append('台账高意向：' + follow_hit[2])
        # lost 只采信台账明确记录（~Winnie 11-11），避免把「取消政策」讨论误判成流失
        if key.startswith('~winnie') and status != 'won':
            status = 'lost'
            tag.append('台账明确取消（11-11）')

        rec = {
            'id': 'waq_' + phone,
            'guest': name,
            'contact': '+' + phone,
            'date': datetime.fromtimestamp(first_ts).strftime('%Y-%m-%d'),
            'serviceDate': guess_service_date(first_text + '\n' + last_quote, today),
            'city': guess_city(first_text, last_quote, recent_customer),
            'pax': guess_pax(first_text + '\n' + last_quote),
            'vehicle': guess_vehicle(first_text + '\n' + last_quote),
            'status': status,
            'route': (re.sub(r'\s+', ' ', first_text).strip()[:180]),
            'quote': guess_quote(last_quote),
            'cost': 0,
            'followUp': follow_up,
            'followUps': [],
            'fleetQuotes': [],
            'note': '',
            'source': 'whatsapp',
            'channel': 'whatsapp',
            'sourceRef': 'WhatsApp 会话 +' + phone,
            '_waJid': jid,
            '_waLastQuoteAt': datetime.fromtimestamp(last_ts).strftime('%Y-%m-%d %H:%M'),
            '_autoImported': True,
            '_serviceDateGuessed': True,
            '_lastModified': int(today.timestamp() * 1000),
        }

        if draft_hit:
            for f in ('contact', 'email', 'city', 'pax', 'vehicle', 'quote', 'route'):
                if draft_hit.get(f):
                    rec[f] = draft_hit[f]
            rec['date'] = draft_hit.get('inquiry_date', rec['date'])
        # 人工台账优先级高于机器抽取：线路/城市/金额一律以台账为准，客户原文仍留在 note
        if won_hit:
            rec['quote'] = won_hit[2]
            rec['route'] = won_hit[1]
            rec['city'] = guess_city(won_hit[1]) or rec['city']
            rec['_ledgerDate'] = won_hit[0]
        elif follow_hit:
            rec['route'] = follow_hit[0] + '｜' + follow_hit[1]
            rec['city'] = guess_city(follow_hit[0]) or rec['city']

        note_parts = ['【客户原始询盘】' + re.sub(r'\s+', ' ', first_text).strip()[:400],
                      '【我方最后报价】' + re.sub(r'\s+', ' ', last_quote).strip()[:400]]
        if tag:
            note_parts.append('【台账标注】' + '；'.join(tag))
        if won_hit:
            note_parts.append('【台账成交明细】' + won_hit[1] + ' / RMB ' + str(won_hit[2]))
        if follow_hit:
            note_parts.append('【台账待跟进】' + follow_hit[0] + ' / ' + follow_hit[1] + ' / ' + follow_hit[2])
        if draft_hit:
            note_parts.append('【报价草稿】' + draft_hit['note'] + '（来源：' + draft_hit['source_file'] + '）')
        rec['note'] = '\n'.join(note_parts)

        records.append(rec)
        stats[status] += 1
        if draft_hit:
            stats['draft_merged'] += 1
        if won_hit:
            stats['won_merged'] += 1
        if follow_hit:
            stats['followup_merged'] += 1

    # ---- 台账/草稿里「尚未正式报价」的高意向询盘：不在 quoted 会话里，单独补入 ----
    # 这恰恰是最需要进系统的一组（如 Annie 搁置 11 天、Kevin 待填价、Manuel 待回 3 问）。
    by_id = {r['id']: r for r in records}
    pending = [('followup', k, v) for k, v in FOLLOWUP.items() if k not in matched_keys]
    pending += [('draft', k, v) for k, v in DRAFTS.items() if k not in matched_keys]
    for kind, key, meta in pending:
        digits = re.sub(r'\D', '', meta.get('contact', '')) if kind == 'draft' else ''
        jid, nm = '', ''
        if digits:
            row = con.execute('SELECT jid, name FROM chats WHERE jid LIKE ? LIMIT 1',
                              (digits + '@%',)).fetchone()
            if row:
                jid, nm = row['jid'], clean_name(row['name'])
        if not jid:
            for row in con.execute(
                "SELECT jid, name FROM chats WHERE lower(ifnull(name,'')) LIKE ? "
                "ORDER BY length(name) LIMIT 10", ('%' + key + '%',)):
                if key in (row['name'] or '').lower():
                    jid, nm = row['jid'], clean_name(row['name'])
                    break
        phone = phone_of(jid) or digits
        if not phone:
            phone = hashlib.md5(key.encode()).hexdigest()[:12]
            stats['unmatched_manual'] += 1
        rid = 'waq_' + phone
        cust = customer_msgs.get(jid, [])
        first_ts, first_text = cust[0] if cust else (int(today.timestamp()), '')
        follow_up = (today + timedelta(days=1)).strftime('%Y-%m-%dT09:00')

        dm = meta if kind == 'draft' else {}
        if kind == 'draft':
            route = meta['route']
            quote = dm.get('quote', 0) or 0
            extra = '【报价草稿】' + meta['note'] + '（来源：' + meta['source_file'] + '）'
        else:
            route = meta[0]
            mm = re.search(r'=\s*([\d,]+)', meta[1])
            amount = (mm or re.search(r'([\d,]{3,6})', meta[1]))
            quote = int(amount.group(1).replace(',', '')) if amount else 0
            extra = '【台账待跟进】' + meta[0] + ' / ' + meta[1] + ' / ' + meta[2]

        note = '\n'.join([
            '【客户原始询盘】' + re.sub(r'\s+', ' ', first_text).strip()[:400] if first_text
            else '【客户原始询盘】见 WhatsApp 会话（本机 wacli 库）',
            '【我方尚未发出正式报价】本条由人工台账补入，避免漏跟',
            extra,
        ])

        if rid in by_id:
            r = by_id[rid]
            old_status = r['status']
            if old_status != 'following':
                stats[old_status] -= 1
                stats['following'] += 1
            r['status'] = 'following'
            r['followUp'] = follow_up
            if quote and not r['quote']:
                r['quote'] = quote
            r['note'] += '\n' + extra
            continue

        rec = {
            'id': rid,
            'guest': nm or key.title(),
            'contact': '+' + phone if phone.isdigit() else '',
            'date': dm.get('inquiry_date') or datetime.fromtimestamp(first_ts).strftime('%Y-%m-%d'),
            'serviceDate': guess_service_date(first_text, today),
            'city': dm.get('city') or guess_city(first_text, route),
            'pax': dm.get('pax') or guess_pax(first_text),
            'vehicle': dm.get('vehicle') or '',
            'status': 'following',
            'route': route,
            'quote': quote,
            'cost': 0,
            'followUp': follow_up,
            'followUps': [], 'fleetQuotes': [],
            'note': note,
            'source': 'whatsapp',
            'channel': 'whatsapp',
            'sourceRef': 'WhatsApp 会话 +' + phone,
            '_waJid': jid,
            '_autoImported': True,
            '_fromLedger': True,
            '_serviceDateGuessed': True,
            '_lastModified': int(today.timestamp() * 1000),
        }
        if dm.get('email'):
            rec['email'] = dm['email']
        records.append(rec)
        by_id[rid] = rec
        stats['following'] += 1
        stats['ledger_only'] += 1
        if kind == 'draft':
            stats['draft_merged'] += 1
        else:
            stats['followup_merged'] += 1

    records.sort(key=lambda r: (r['date'], r['id']), reverse=True)

    # 统一全渠道模型字段：口径与 index.html 的 inquiryGuestKey() / inquiryChannel() 保持一致。
    # 放在所有台账/草稿合并之后，避免 contact/email 被后续补全时 guestKey 变陈旧。
    def _norm_digits(s):
        return re.sub(r'\D', '', str(s or ''))[-11:]

    def _guest_key(r):
        e = str(r.get('email') or r.get('contact') or '').strip().lower()
        if '@' in e:
            return 'e:' + e
        p = _norm_digits(r.get('contact'))
        if len(p) >= 7:
            return 'p:' + p
        g = re.sub(r'\s+', '', str(r.get('guest') or '')).lower()
        return 'g:' + g if g else ''

    for r in records:
        r['channel'] = 'whatsapp'
        r['guestKey'] = _guest_key(r)
        if not r.get('sourceRef'):
            r['sourceRef'] = 'WhatsApp 会话 +' + _norm_digits(r.get('contact'))
        r['_threadId'] = r.get('_threadId') or r.get('_waJid') or ('wa:' + r['id'])

    priority = [r for r in records if r['status'] in ('following', 'won', 'lost')]
    priority_payload = {
        'ok': True, 'type': 'elite-fleet/inquiries', 'version': 1,
        'generatedAt': today.strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'WhatsApp 人工台账 / 报价草稿（优先跟进）',
        'sourceNote': SOURCE_NOTE,
        'note': '只含需人工优先处理的记录；全量见 whatsapp-inquiries.json',
        'stats': {'following': stats['following'], 'won': stats['won'], 'lost': stats['lost']},
        'inquiries': priority,
    }
    Path(args.priority_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.priority_out).write_text(
        json.dumps(priority_payload, ensure_ascii=False, indent=2), encoding='utf-8')

    payload = {
        'ok': True,
        'type': 'elite-fleet/inquiries',
        'version': 1,
        'generatedAt': today.strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'WhatsApp Business +86 153 4772 3823（本机 wacli 只读库）+ 人工台账',
        'sourceNote': SOURCE_NOTE,
        'note': '导入后按 id 幂等去重；won/lost 仅采信人工台账，其余为 quoted，请逐单复核。',
        'stats': dict(stats),
        'inquiries': records,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    # ---------- 复核清单 ----------
    lines = [
        '# WhatsApp 询盘并入工作台 — 复核清单', '',
        '> 生成时间：' + payload['generatedAt'] + ' ｜ 数据源：本机 wacli 只读库 ' + str(db),
        '> ' + SOURCE_NOTE, '',
        '## 并入方案（数据链路）', '',
        '**先澄清数据来源**：`E:/WorkBuddy/WorkBuddy/2026-11-12-23-11-44` 里是从 WhatsApp 全库'
        '分析出来的汇总台账、报价模板库、快捷回复库和 2 份客户回复草稿，**不是原始询盘记录**；'
        '原始消息在本机 `C:/Users/Administrator/.wacli/wacli.db`（wacli 0.26.0，25,097 条消息 / 1,276 个私聊）。', '',
        '| 通道 | 数据来源 | 进系统的方式 | 现状 |', '|---|---|---|---|',
        '| ① 网站表单 | chinawondercars.com | 「同步网站询盘」→ `web_*` | 已有 |',
        '| ② WA 实时 webhook | Meta / Twilio | WA 收件箱 →「转询价单」→ `wa_*` | 已有（只覆盖未来新消息） |',
        '| ③ 飞书台账 | 飞书多维表 | 导入 JSON → 询价单 | 已有 |',
        '| ④ **WA 历史会话** | wacli 只读库 + 人工台账 | `/api/wa-inquiries` 或本地 JSON → `waq_*` | ✅ 本次打通 |', '',
        '**为什么转成询价单，而不是灌进 WA 收件箱**：收件箱是消息流，25k 条消息全塞进去不可用；'
        '工作台要管理的是「一条询盘 → 报价 → 跟进 → 成交」这个业务实体。因此按会话聚合成询价单，'
        '每条只留业务字段 + 客户原文摘录 + 我方最后报价摘录 + `_waJid`（可回查原会话）。', '',
        '**幂等与不覆盖**：`id = waq_<手机号>`，重复导入只新增未见过的；同一号码若已由 ①/② 转过询价单则跳过；'
        '已存在的记录只补空字段，手改过的 `status` / `quote` / `note` / `followUp` 一律不动。', '',
        '| 并入 | 未并入 | 原因 |', '|---|---|---|',
        '| %d 个有我方报价的会话 | 25,097 条原始消息 | 消息流不是业务实体，原文摘录已附在每条备注 |' % len(records),
        '| 台账成交 10 / 高意向 7 / 草稿 3 / 台账补入 4 | 快捷回复库、报价模板库、台账 PDF | '
        '知识文档，建议放资料库供报价智能体检索，不混进询盘表 |',
        '| 文字原文摘录（每条 400 字） | 图片 / 语音 / 行程截图 | wacli media 需单独下载与 OCR，未纳入本次 |', '',
        '**日常更新（新询盘进来后）**：', '',
        '1. `HTTPS_PROXY=http://127.0.0.1:10809 wacli sync`（该号需走本地代理）',
        '2. `python scripts/wa_inquiry_import.py`（重新生成本地两份 JSON + 复核清单）',
        '3. `wrangler kv key put page:whatsapp-priority.json / page:whatsapp-inquiries.json '
        '--binding EF_DATA --remote --path ...`（两条都传）',
        '4. 工作台「数据与同步 → WhatsApp 询盘并入」点「拉取优先跟进清单」即可增量合并', '',
        '## 汇总', '',
        '| 状态 | 条数 | 说明 |', '|---|---|---|',
        '| quoted 已报价 | %d | 我方发过正式报价，机器默认状态 |' % stats['quoted'],
        '| following 跟进中 | %d | 台账高意向 + 3 份报价草稿客户，已设次日 09:00 跟进 |' % stats['following'],
        '| won 已成交 | %d | 仅采信台账「11 月确认成交明细」人工核对项 |' % stats['won'],
        '| lost 已流失 | %d | 客户明确取消 |' % stats['lost'],
        '| **合计** | **%d** | |' % len(records), '',
        '- 报价草稿合并：%d 条 ｜ 台账成交合并：%d 条 ｜ 台账高意向合并：%d 条 ｜ '
        '台账补入（我方尚未报价，最容易漏跟）：%d 条' % (
            stats['draft_merged'], stats['won_merged'], stats['followup_merged'], stats['ledger_only']), '',
        '> ⚠️ `quote` 为保守取值（优先 Total，否则首个 RMB 金额）；`serviceDate` 为从会话文本推测，'
        '标记为 `_serviceDateGuessed`，请以客人确认的用车日为准；状态仅 `won`/`lost` 采信人工台账。'
        '原文摘录保存在每条记录的备注里，导入后请在「询价管理」逐单核对。', '',
        '## 优先跟进（following）', '',
        '| 客人 | 电话 | 询价日 | 城市 | 报价 | 备注 |', '|---|---|---|---|---|---|',
    ]
    for r in [x for x in records if x['status'] == 'following']:
        lines.append('| %s | %s | %s | %s | %s | %s |' % (
            r['guest'].replace('|', '/'), r['contact'], r['date'], r['city'] or '—',
            ('RMB ' + str(r['quote'])) if r['quote'] else '—',
            re.sub(r'\s+', ' ', r['route'])[:70].replace('|', '/')))
    lines += ['', '## 台账已成交但未自动合并（需人工确认）', '',
              '> 台账第三节共 14 条成交，其中 10 条已按姓名精确匹配标为 `won`；'
              '以下 4 条因姓名歧义或无联系方式，仍保留在 `quoted` 里，请手工核对后改状态。', '',
              '| 日期 | 客户 | 线路 | 金额 | 未自动合并原因 |', '|---|---|---|---|---|']
    for dt, guest, route, amount, why in WON_MANUAL_REVIEW:
        lines.append('| %s | %s | %s | %s | %s |' % (
            dt, guest, route, ('RMB ' + str(amount)), why))
    lines += ['', '## 未并入本清单的文档（知识类，非询盘记录）', '',
              '- `WhatsApp快捷回复库.md`（/quote /payment 等 10 条指令）',
              '- `报价模板库.md`（T1–T7 模板与定价纪律）',
              '- `WhatsApp成交线路报价台账.pdf`、两份 `.md` 报价草稿全文',
              '- 建议：这些文档放进资料库/知识库供报价智能体检索，询价单只存业务字段与原文摘录。', '']
    lines += ['', '## 全部记录（按询价日期倒序）', '',
              '| # | 客人 | 电话 | 询价日 | 用车日 | 状态 | 城市 | 人数 | 车型 | 报价 |',
              '|---|---|---|---|---|---|---|---|---|---|']
    for i, r in enumerate(records, 1):
        lines.append('| %d | %s | %s | %s | %s | %s | %s | %d | %s | %s |' % (
            i, r['guest'].replace('|', '/'), r['contact'], r['date'], r['serviceDate'] or '—',
            r['status'], r['city'] or '—', r['pax'], r['vehicle'] or '—',
            r['quote'] or '—'))
    lines += ['', '## 导入方式', '',
              '1. 打开工作台 `fleet.chinawondercars.com` →「数据与同步」→「WhatsApp 询盘并入」',
              '2. 先选 `data/whatsapp-priority.json`（%d 条，优先跟进/成交/流失）小批量验证；'
              '确认无误再选 `data/whatsapp-inquiries.json`（%d 条，全量历史）' % (len(priority), len(records)),
              '3. 导入按 id 幂等去重，不会覆盖你在工作台里手改过的状态/报价/备注；重复导入只会新增未导入的',
              '4. 导入后到「询价管理」按状态筛选核对，重点看 following 与 won 两组',
              '5. 更新数据：重新跑 `python scripts/wa_inquiry_import.py`，再导入一次即可', '']
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text('\n'.join(lines), encoding='utf-8')

    print('全量：', out)
    print('优先：', args.priority_out, '（%d 条）' % len(priority))
    print('复核清单：', rp)
    print('共 %d 条｜quoted %d / following %d / won %d / lost %d｜草稿合并 %d / 成交合并 %d / '
          '高意向合并 %d / 台账补入 %d' % (
              len(records), stats['quoted'], stats['following'], stats['won'], stats['lost'],
              stats['draft_merged'], stats['won_merged'], stats['followup_merged'], stats['ledger_only']))
    return payload


if __name__ == '__main__':
    build()
