#!/usr/bin/env python3
"""邮件询盘 → 全渠道询盘中枢（channel=email）

两种数据源：
  1) --raw data/_mail_raw.json   （默认；由 WorkBuddy 邮箱工具导出的只读快照）
  2) --imap                      （直连业务邮箱 IMAP，凭据全部读环境变量，不落源码）

输出：
  data/email-inquiries.json      全量
  data/email-priority.json       优先跟进（最近 14 天且未成交/未流失）
  _reports/邮件询盘并入清单.md    逐条复核清单

原则：只读、不发邮件、不改邮箱状态；客户数据文件已被 .gitignore 排除。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW = ROOT / "data" / "_mail_raw.json"
DEFAULT_OUT = ROOT / "data" / "email-inquiries.json"
DEFAULT_PRIORITY = ROOT / "data" / "email-priority.json"
DEFAULT_REPORT = ROOT / "_reports" / "邮件询盘并入清单.md"

SOURCE_NOTE = "邮件渠道只读抽取：按发件人邮箱合并往返邮件为一条询盘，主题轨迹保留在备注；状态/用车日期/城市/人数为规则推断，导入后需人工核对"

# ---------- 过滤规则 ----------
# 转发邮件（Fw:/Fwd:）承载的是客户原始询盘，发件人虽是自家域名，也不能当自发邮件丢弃。
# 历史踩坑：曾把「Fw: 广州→香港 次日用车」当成自发邮件排除，导致最紧急的单子漏进系统。
FW_PAT = re.compile(r"^\s*(fw|fwd|转发)\s*[:：]", re.I)
# 网站表单通知邮件：线上 data:inquiries 只有少量旧记录，新表单并未被同步，
# 因此在这里转成 channel=web 的兜底询盘，避免「网站表单」这一渠道整体漏单。
WEB_FORM_PAT = re.compile(
    r"(New\s+(?:INTERCITY|BUSINESS|AIRPORT|CITY|GENERAL|MULTIDAY|ROUTE|Travel|Booking)\s+"
    r"(?:Enquiry|Booking)|New\s+(?:Route\s+)?Booking\s+Inquiry|SPT-\d+)", re.I)
PAYMENT_PAT = re.compile(r"Payment Received|\bSPT-\d+.*(?:paid|payment received)", re.I)

SELF_ADDRESSES = {
    "elitetravel@qq.com", "elitetravel@foxmail.com", "travel@elitetravel4u.com",
    "booking@chinawondercars.com", "booking@chinawondertours.com",
    "wnxi5637@agent.qq.com", "148407335@qq.com", "13564053414@qq.com",
    "toalice0309@gmail.com", "postmaster@elitetravel4u.com",
}
NOISE_DOMAINS = (
    "noreply", "no-reply", "no_reply", "newsletter", "notifications", "notification",
    "linkedin.com", "cloudflare.com", "github.com", "aliyun.com",
    "exmail.weixin.qq.com", "ctrip.com", "wordfence.com", "google.com", "apple.com",
    "microsoft.com", "singaporeair.com", "qatarairways.com", "traveldaily.cn",
    "getyourguide.com", "trip.com", "emirates", "tasmanholidayparks.com",
    "amandacruise.com.vn",
)
NOISE_SUBJECT = re.compile(
    r"(WhatsApp 询盘晨报|WhatsApp 跟进提醒|DRAFT for review|New (?:INTERCITY|BUSINESS|AIRPORT|CITY|GENERAL) Enquiry|"
    r"New Travel Inquiry|New Booking Inquiry|Payment Received|退信|邮箱容量|账号登录提醒|"
    r"invoice|Vulnerabilit|Black Friday|Terms of Service|电子报销凭证|出票确认|值机提醒|"
    r"Statement|discount|促销|优惠券|给你的礼物|安全态势报告|限时优惠)",
    re.I,
)
SPAM_TEXT = re.compile(
    r"(View in browser|unsubscribe|此邮件由系统自动发出|请勿直接回复|机票电子报销凭证|"
    r"已获得了你的QQ邮箱账号的访问权限|VIEW ON BROWSER)", re.I,
)

# ---------- 业务字段抽取 ----------
# 注意：机场代码必须大小写敏感，否则 CAN / SHA 会误匹配英文 can / shall（历史踩坑）
CITY_RULES = [
    ("上海", r"上海|Shanghai|Shangai|Xangai|Pudong|Hongqiao|The Bund|外滩|徐汇|Xuhui|Lujiazui"),
    ("北京", r"北京|Beijing|Peking|Pequim|Daxing|慕田峪|Mutianyu|金山岭|Jinshanling|八达岭|Badaling|长城|Great Wall"),
    ("广州", r"广州|Guangzhou|Baiyun|Tiyuxi|Fengxian"),
    ("深圳", r"深圳|Shenzhen"),
    ("香港", r"香港|Hong Kong"),
    ("澳门", r"澳门|Macao|Macau|Taipa|Cotai"),
    ("成都", r"成都|Chengdu|Shuangliu|Panda Base"),
    ("重庆", r"重庆|Chongqing|芙蓉镇"),
    ("三亚", r"三亚|Sanya|Nanshan|南山|Oceania Nautica"),
    ("桂林", r"桂林|Guilin|阳朔|Yangshuo|Longji|Shili"),
    ("丽江", r"丽江|Lijiang|玉龙雪山|Jade Dragon|Blue Moon Valley"),
    ("云南", r"云南|Yunnan|大理|Dali|昆明|Kunming|元阳|Yuanyang"),
    ("杭州", r"杭州|Hangzhou|千岛湖"),
    ("苏州", r"苏州|Suzhou"),
    ("西安", r"西安|Xi.?an|兵马俑|Terra.?cotta"),
    ("西藏", r"西藏|Tibet|拉萨|Lhasa"),
    ("新疆", r"新疆|Xinjiang|喀什|乌鲁木齐"),
    ("槟城", r"Penang|槟城|George Town|Chew Jetty|Swettenham Pier"),
    ("哈尔滨", r"哈尔滨|Harbin|延吉|Yanji|rime ice|雾凇|Northeast China"),
    ("张家界", r"张家界|Zhangjiajie|天门山|Tianmen Mountain|芙蓉镇|Furong"),
]
CITY_CODES = [
    ("上海", r"\bPVG\b|\bSHA\b"), ("北京", r"\bPEK\b|\bPKX\b"),
    ("广州", r"\bCAN\b"), ("深圳", r"\bSZX\b"), ("香港", r"\bHKG\b"),
    ("成都", r"\bCTU\b"), ("三亚", r"\bSYX\b"),
]
MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})

DATE_PATTERNS = [
    (re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s*,?\s*(\d{4})"), lambda m: (m.group(1), m.group(2), m.group(3))),
    (re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})"), lambda m: (m.group(2), m.group(1), m.group(3))),
    (re.compile(r"(\d{1,2})[-–](\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s*(\d{4})"), lambda m: (m.group(1), m.group(3), m.group(4))),
    (re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})[-–](\d{1,2})\s*,?\s*(\d{4})"), lambda m: (m.group(2), m.group(1), m.group(4))),
    (re.compile(r"(\d{1,2})[-–/](\d{1,2})[-–/](20\d{2})"), lambda m: (m.group(1), m.group(2), m.group(3))),
]
PAX_PAT = re.compile(
    r"(\d+)\s*(?:adults?|people|persons?|passengers?|guests?|pax|clients?|friends)", re.I)
PAX_EXTRA = re.compile(r"(\d+)\s*(?:child|children|teenagers?|kids?)", re.I)
PAX_GROUP = re.compile(r"group (?:of|size:?)\s*(\d+)|group a (\d+)|\b(\d+)\s*-\s*(\d+)\s*adults", re.I)

WON_PAT = re.compile(
    r"(deposit payment has been completed|final payment|booking confirmation|"
    r"we would like to proceed with the booking|has been receipted|confirmation .* confirmed|"
    # 客人明确「已付定金 / 已付款」也算成交：Armando 已付 50% 定金、David 已付尾款
    r"we have made the .{0,25}deposit payment|made the 50% deposit|"
    r"i have made the payment|payment received via paypal|booking confirmed)", re.I)
NEGOTIATE_PAT = re.compile(
    r"(higher than .{0,40}budget|lower overall price|expecting a lower|thinking of something in the range|"
    r"comparing affordable|Could you please|please (?:confirm|tell me|send)|waiting|one moment|待回复|还价)", re.I)
CANCEL_PAT = re.compile(r"\b(cancel(?:led)? the booking|no longer need|not going ahead)\b", re.I)


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def is_noise(msg: dict) -> str:
    frm = (msg.get("from") or "").lower().strip()
    subj = clean(msg.get("subject"))
    body = clean(msg.get("snippet"))
    if FW_PAT.search(subj) and not WEB_FORM_PAT.search(subj):
        return ""  # 客户询盘转发件，即使发自自家域名也必须纳入
    if frm in SELF_ADDRESSES:
        return "自发邮件/内部草稿"
    if any(d in frm for d in NOISE_DOMAINS) or "10000@exmail.weixin.qq.com" in frm:
        return "系统通知/广告域名"
    if NOISE_SUBJECT.search(subj):
        return "通知/简报/网站表单回执（网站同步已覆盖）"
    if SPAM_TEXT.search(body) and not re.search(r"(private car|quotation|inquiry|driver|transfer|tour)", subj + " " + body, re.I):
        return "营销邮件"
    if not frm or "@" not in frm:
        return "无有效发件人"
    return ""


def norm_subject(s: str, lower: bool = True) -> str:
    s = clean(s)
    for _ in range(4):
        s2 = re.sub(r"^\s*(re|fw|fwd|回复|答复|转发)\s*[:：]\s*", "", s, flags=re.I)
        s2 = re.sub(r"^\s*(回复|答复)\s*[:：]\s*", "", s2)
        if s2 == s:
            break
        s = s2
    return (s.lower() if lower else s).strip()


def guess_city(text: str) -> str:
    """取文本中最先出现的城市，而不是规则表顺序；机场代码单独大小写敏感匹配。"""
    best = None
    for city, pat in CITY_RULES:
        m = re.search(pat, text, re.I)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), city)
    for city, pat in CITY_CODES:
        m = re.search(pat, text)  # 不加 re.I，避免 CAN / SHA 误匹配 can / shall
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), city)
    return best[1] if best else ""


def guess_pax(text: str) -> int:
    # "2 adults, 1 child" / "3 adults, 2 teenagers" 这类组合优先，避免把总人数再累加一次
    combo = re.search(
        r"(\d+)\s*adults?\s*(?:,|\+|and|with|及)?\s*(\d+)\s*(?:child|children|teenagers?|kids?)",
        text, re.I)
    if combo:
        return min(int(combo.group(1)) + int(combo.group(2)), 40)
    nums = [int(x) for x in PAX_PAT.findall(text)]
    g = PAX_GROUP.search(text)
    if g:
        vals = [int(x) for x in g.groups() if x and x.isdigit()]
        if vals:
            nums.append(max(vals))
    total = max(nums) if nums else 0
    if total and PAX_EXTRA.search(text) and not re.search(r"\b(persons?|people|passengers?|pax)\b", text, re.I):
        total += sum(int(x) for x in PAX_EXTRA.findall(text))
    return min(total, 40) if total else 1


def guess_service_date(text: str, fallback_year: int) -> str:
    """收集文本里所有可解析的日期，取最早的行程开始日（多段行程以首日为准）。"""
    found = []
    for pat, get in DATE_PATTERNS:
        for m in pat.finditer(text):
            try:
                a, b, c = get(m)
                if b.isdigit():  # 12/24/2026 形式
                    day, mon, year = int(a), int(b), int(c)
                else:
                    day, mon, year = int(a), MONTHS.get(str(b).lower()[:3], 0), int(c)
                if mon and 1 <= day <= 31 and 2020 <= year <= 2035:
                    found.append(f"{year:04d}-{mon:02d}-{day:02d}")
            except (ValueError, TypeError):
                continue
    return min(found) if found else ""


def thread_key(msg: dict) -> str:
    # 按发件人邮箱合并：同一客人在窗口期内的往返邮件属于同一条询盘，
    # 主题轨迹完整保留在 note 里，避免 "Inquiry" 与 "Re: Inquiry — ..." 被拆成两条。
    frm = (msg.get("from") or "").lower().strip()
    subj = clean(msg.get("subject"))
    if FW_PAT.search(subj):
        # 客人自己转发的：按发件邮箱并入原线程，避免同一客人被拆成两条、还撞出相同 id
        if frm not in SELF_ADDRESSES:
            return frm
        # 自家账号代客转发：真实客户邮箱在正文里
        mm = re.search(r"([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})", clean(msg.get("snippet")))
        if mm and mm.group(1).lower() not in SELF_ADDRESSES:
            return mm.group(1).lower()
        return frm + "|fw:" + norm_subject(subj)
    return frm


def build_record(frm: str, msgs: list, today: datetime) -> dict:
    msgs = sorted(msgs, key=lambda m: m.get("at") or "")
    last = msgs[-1]
    subj = norm_subject(last.get("subject") or "", lower=False) or clean(last.get("subject") or "邮件询盘")
    subj_key = subj.lower()
    def strip_quote(s: str) -> str:
        # 邮件正文里的 "On ... wrote:" / 原始邮件引用块常含旧日期，不能拿来当用车日
        s = clean(s)
        return re.split(r"(On .{0,200}?wrote:|-------- Original message --------|From: China Wonder Tou|Sent from my Galaxy)", s, maxsplit=1, flags=re.I)[0]
    all_text = " ".join(clean(m.get("subject")) + " " + strip_quote(m.get("snippet")) for m in msgs)
    # 转发件：发件人只是自家转发账号，真实客人的邮箱/姓名在正文里，用它做 contact/guestKey/id
    is_fw = FW_PAT.search(clean(last.get("subject"))) is not None
    frm_eff = frm.split("|")[0] if "|fw:" in frm else frm
    fw_name = ""
    if is_fw:
        mm = re.search(r"([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})", all_text)
        if mm and mm.group(1).lower() not in SELF_ADDRESSES:
            frm_eff = mm.group(1).lower()
        mg = re.search(r"(?:from|From:)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})\s+[\w.+-]+@", all_text)
        if mg:
            fw_name = clean(mg.group(1))
    # 转发件若正文里没提到客户姓名，退回信封显示名，别把客人显示成邮箱前缀
    name = fw_name or clean(last.get("name")) or clean(frm_eff.split("@")[0])
    date = (last.get("at") or "")[:10] or today.strftime("%Y-%m-%d")
    svc = guess_service_date(subj + " " + all_text, today.year)
    rid = "eml_" + hashlib.sha1((frm_eff + "|" + subj_key).encode("utf-8")).hexdigest()[:12]
    unread_last = not bool(last.get("read", True))
    status = "new"
    # 所有未处理的新询价也排次日 09:00 跟进，避免邮件客人被遗忘
    follow_up = (today + timedelta(days=1)).strftime("%Y-%m-%d") + " 09:00"
    if CANCEL_PAT.search(all_text):
        status, follow_up = "lost", ""
    elif WON_PAT.search(all_text):
        status, follow_up = "won", ""
    elif unread_last or NEGOTIATE_PAT.search(all_text):
        status = "following"
    elif re.match(r"^\s*(re|回复|答复)\s*[:：]", clean(last.get("subject")), re.I):
        status = "quoted"
    snippet = strip_quote(last.get("snippet"))[:420]
    subjects = " ｜ ".join(dict.fromkeys(clean(m.get("subject")) for m in msgs))
    note = (
        f"邮件会话共 {len(msgs)} 封，最近一封 {date}\n"
        f"主题轨迹：{subjects[:300]}\n"
        f"最近原文摘录：{snippet}"
    )
    return {
        "id": rid,
        "guest": name[:60],
        "contact": frm_eff,
        "email": frm_eff,
        "date": date,
        "serviceDate": svc,
        # 只用正文优先、主题兜底：主题里的 "China and Hong Kong" 不应盖掉正文里的西安/上海
        "city": guess_city(
            " ".join(strip_quote(m.get("snippet")) for m in msgs) + " " + subj),
        "pax": guess_pax(subj + " " + all_text),
        "vehicle": "",
        "status": status,
        "route": (subj[:120] + (" · " + clean(msgs[0].get("snippet"))[:80] if len(msgs[0].get("snippet") or "") else "")),
        "quote": 0,
        "cost": 0,
        "followUp": follow_up,
        "followUps": [],
        "fleetQuotes": [],
        "note": note,
        "source": "email-inbox",
        "channel": "email",
        "subject": clean(subj)[:160],
        "sourceRef": ("转发询盘: " if is_fw else "邮件主题: ") + clean(last.get("subject"))[:120],
        "guestKey": "e:" + frm_eff.lower(),
        "_threadId": rid,
        "_msgCount": len(msgs),
        "_sourceSnippet": snippet[:300],
        "_fromMailbox": clean(last.get("to")),
        "_serviceDateGuessed": bool(svc),
        "_autoImported": True,
        "_lastModified": int(today.timestamp() * 1000),
    }


def build_web_record(m: dict, today: datetime, paid_orders: set) -> dict | None:
    """网站表单通知邮件 → channel=web 兜底询盘。

    线上 data:inquiries 只有少量旧记录，新表单并未被网站同步通道收进来；
    这封通知邮件就是唯一线索，必须转成实体，否则「网站表单」渠道整体漏单。
    quote 一律留 0：通知里的 Est. Price 是估算价，不是对外正式报价。
    """
    subj = clean(m.get("subject"))
    body = clean(m.get("snippet"))
    text = subj + " " + body
    gm = re.search(r"[-–—]\s*([A-Za-z][^—–\-|]{1,40})\s*$", subj)
    guest = clean(gm.group(1)) if gm else ""
    if guest.upper().startswith("SPT-"):
        guest = ""
    em = re.search(r"([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})", body)
    email = em.group(1).lower() if em else ""
    om = re.search(r"\b(SPT-\d{8}-\d+)\b", text)
    order_id = om.group(1) if om else ""
    if not (guest or email or order_id):
        return None
    guest = guest or (email.split("@")[0] if email else order_id)
    key_src = (email or order_id or guest) + "|" + subj
    rid = "web_" + hashlib.sha1(key_src.encode("utf-8")).hexdigest()[:12]
    date = (m.get("at") or "")[:10] or today.strftime("%Y-%m-%d")
    dm = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", body)
    svc = dm.group(1) if dm else ""
    vm = re.search(r"Vehicle\s+([^.]{2,60})", body, re.I)
    vehicle = clean(vm.group(1)) if vm else ""
    paid = bool(order_id and order_id in paid_orders)
    status = "won" if paid else "new"
    follow_up = "" if paid else (today + timedelta(days=1)).strftime("%Y-%m-%d") + " 09:00"
    note = (
        "网站表单通知邮件（兜底建档：线上 data:inquiries 未同步到这批新表单）\n"
        + "来源主题：" + subj[:160] + "\n原文摘录：" + body[:420]
        + ("\n同订单已收到付款回执，标记已成交：" + order_id if paid else "")
    )
    return {
        "id": rid,
        "guest": guest[:60],
        "contact": email,
        "email": email,
        "date": date,
        "serviceDate": svc,
        "city": guess_city(body + " " + subj),
        "pax": guess_pax(text),
        "vehicle": vehicle,
        "status": status,
        "route": body[:180],
        "quote": 0,
        "cost": 0,
        "followUp": follow_up,
        "followUps": [],
        "fleetQuotes": [],
        "note": note,
        "source": "web-form-notice",
        "channel": "web",
        "subject": subj[:160],
        "sourceRef": "网站表单通知邮件: " + subj[:120],
        "guestKey": ("e:" + email) if email else ("g:" + re.sub(r"\s+", "", guest).lower()),
        "_threadId": rid,
        "_orderId": order_id,
        "_sourceSnippet": body[:300],
        "_fromMailbox": clean(m.get("to")),
        "_serviceDateGuessed": bool(svc),
        "_autoImported": True,
        "_lastModified": int(today.timestamp() * 1000),
    }


def build_web_records(notices: list, today: datetime) -> list:
    """先收集付款回执的订单号，再为每封新表单通知建档；按客人/订单号去重。"""
    paid_orders = set()
    for m in notices:
        s = clean(m.get("subject"))
        if re.search(r"Payment Received", s, re.I):
            om = re.search(r"\b(SPT-\d{8}-\d+)\b", s + " " + clean(m.get("snippet")))
            if om:
                paid_orders.add(om.group(1))
    out, seen = [], set()
    for m in sorted(notices, key=lambda x: x.get("at") or "", reverse=True):
        s = clean(m.get("subject"))
        if re.search(r"Payment Received", s, re.I):
            continue
        r = build_web_record(m, today, paid_orders)
        if not r:
            continue
        dk = (r["email"] or r.get("_orderId") or r["guest"]).lower()
        if dk in seen:
            continue
        seen.add(dk)
        out.append(r)
    return out


def load_imap(days: int) -> tuple[list[dict], dict]:
    import imaplib
    import email
    from email.header import decode_header, make_header
    from email.utils import parsedate_to_datetime

    host = os.environ.get("EF_IMAP_HOST")
    user = os.environ.get("EF_IMAP_USER")
    pwd = os.environ.get("EF_IMAP_PASS")
    if not (host and user and pwd):
        raise SystemExit("IMAP 凭据缺失：请设置 EF_IMAP_HOST / EF_IMAP_USER / EF_IMAP_PASS 环境变量")
    port = int(os.environ.get("EF_IMAP_PORT", "993"))
    box = imaplib.IMAP4_SSL(host, port)
    box.login(user, pwd)
    box.select(os.environ.get("EF_IMAP_FOLDER", "INBOX"))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    typ, data = box.search(None, f'(SINCE "{since}")')
    ids = (data[0] or b"").split()
    out = []
    for mid in ids[-400:]:
        typ, raw = box.fetch(mid, "(RFC822)")
        if typ != "OK" or not raw or not raw[0]:
            continue
        msg = email.message_from_bytes(raw[0][1])
        def dec(v):
            try:
                return str(make_header(decode_header(v or "")))
            except Exception:
                return v or ""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                    except Exception:
                        pass
                    break
        else:
            try:
                body = msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "ignore")
            except Exception:
                body = ""
        try:
            at = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            at = ""
        from_addr = email.utils.parseaddr(msg.get("From", ""))[1].lower()
        out.append({
            "id": clean(mid.decode()),
            "name": dec(email.utils.parseaddr(msg.get("From", ""))[0]),
            "from": from_addr,
            "to": dec(msg.get("To", "")),
            "subject": dec(msg.get("Subject", "")),
            "at": at,
            "read": True,
            "snippet": clean(body)[:1500],
        })
    box.close()
    box.logout()
    return out, {"source": f"IMAP {host} / {user}", "scanned": len(out), "days": days}


def main():
    ap = argparse.ArgumentParser(description="邮件询盘 → 全渠道询盘中枢 JSON")
    ap.add_argument("--raw", default=str(DEFAULT_RAW), help="邮箱工具导出的只读 JSON 快照")
    ap.add_argument("--imap", action="store_true", help="改用 IMAP 直连（凭据读环境变量）")
    ap.add_argument("--days", type=int, default=14, help="IMAP 模式拉取最近 N 天，默认 14")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--priority-out", default=str(DEFAULT_PRIORITY))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    args = ap.parse_args()

    today = datetime.now()
    if args.imap:
        messages, meta = load_imap(args.days)
    else:
        raw_path = Path(args.raw)
        if not raw_path.exists():
            raise SystemExit(f"找不到邮件快照：{raw_path}")
        d = json.loads(raw_path.read_text(encoding="utf-8"))
        messages = d.get("messages", [])
        meta = {"source": d.get("source", str(raw_path)), "scanned": d.get("scanned", len(messages))}

    kept, excluded, web_notices = [], defaultdict(int), []
    for m in messages:
        subj = clean(m.get("subject"))
        if WEB_FORM_PAT.search(subj) and not FW_PAT.search(subj):
            web_notices.append(m)
            continue
        why = is_noise(m)
        if why:
            excluded[why] += 1
        else:
            kept.append(m)

    threads = defaultdict(list)
    for m in kept:
        threads[thread_key(m)].append(m)
    records = [build_record(k, v, today) for k, v in threads.items()]
    web_records = build_web_records(web_notices, today)
    records += web_records
    records.sort(key=lambda r: r["date"], reverse=True)

    cutoff = (today - timedelta(days=14)).strftime("%Y-%m-%d")
    priority = [r for r in records if r["date"] >= cutoff and r["status"] not in ("won", "lost")]

    base = {
        "ok": True, "type": "elite-fleet/inquiries", "version": 1,
        "generatedAt": today.strftime("%Y-%m-%d %H:%M:%S"),
        "channel": "email",
        "channels": ["email", "web"],
        "source": meta.get("source", ""),
        "sourceNote": SOURCE_NOTE,
        "stats": {
            "scanned": meta.get("scanned", len(messages)),
            "excluded": sum(excluded.values()),
            "webForms": len(web_records),
            "threads": len(records),
            "following": sum(1 for r in records if r["status"] == "following"),
            "won": sum(1 for r in records if r["status"] == "won"),
            "quoted": sum(1 for r in records if r["status"] == "quoted"),
            "priority": len(priority),
        },
    }
    for path, rows, note in [
        (Path(args.out), records, "全量邮件询盘"),
        (Path(args.priority_out), priority, "优先跟进（最近 14 天且未成交/未流失）"),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(base, note=note, inquiries=rows)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 邮件询盘并入清单", "",
        "> " + SOURCE_NOTE, "",
        f"- 扫描邮件：{meta.get('scanned', len(messages))} 封 ｜ 纳入客户邮件：{len(kept)} 封 ｜ 排除：{sum(excluded.values())} 封",
        f"- 邮件会话 {len(records) - len(web_records)} 个 + 网站表单兜底 {len(web_records)} 个 = {len(records)} 个询盘 ｜ 优先跟进：{len(priority)} 条",
        "", "## 排除明细", "",
    ]
    lines += [f"- {k}：{v} 封" for k, v in sorted(excluded.items())] or ["- 无"]
    lines += [
        "", f"> 网站表单通知邮件 {len(web_notices)} 封：已转成 channel=web 兜底询盘 {len(web_records)} 条。",
        "> 原因：线上 data:inquiries 只有 3 条旧记录，11-15 之后的新表单并未被网站同步通道收进来，不并入会漏单。",
        "> 记录级 channel 为准；若日后网站同步补齐，按同渠道邮箱幂等合并即可，不会重复建档。", "",
        "## 并入方式", "",
        "1. 工作台「数据与同步 → 邮件询盘并入」→ 点「拉取邮件询盘清单」或「拉取全量历史」",
        "2. 也可直接选择 `data/email-inquiries.json` / `data/email-priority.json` 导入",
        "3. 幂等：`id = eml_<发件邮箱+主题哈希>`，同渠道同邮箱重复导入只补空字段，不覆盖手改状态/报价/备注",
        "", "## 全部记录（按最近邮件日期倒序）", "",
        "| 客人 | 邮箱 | 日期 | 状态 | 城市 | 人数 | 用车日 | 邮件主题 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["guest"].replace("|", "／"), r["email"], r["date"], r["status"],
            r["city"] or "—", r["pax"], r["serviceDate"] or "—",
            (r["subject"] or "").replace("|", "／")[:80]))
    lines += [
        "", "## 状态口径", "",
        "- `won`：会话中出现已付定金/尾款/预订确认",
        "- `following`：最后一封客户邮件未读，或出现还价/等待确认/请求补充信息（自动设次日 09:00 跟进）",
        "- `quoted`：主题为 Re: 的历史往返会话；`new`：首封询问且未识别到回复；`lost`：明确取消",
        "- `new` / `quoted` / `following` 统一排次日 09:00 跟进，避免邮件客人被遗忘；`won` / `lost` 不排跟进",
        "- `serviceDate` 由邮件正文推断，标记 `_serviceDateGuessed`，以客人确认为准；`quote` 一律留 0，不把客户预算当报价",
        "", "## 日常更新", "",
        "```bash",
        "# 方式 A：邮箱工具导出快照后重跑（当前采用）",
        "python scripts/email_inquiry_import.py --raw data/_mail_raw.json",
        "# 方式 B：IMAP 直连（凭据走环境变量，不写进源码）",
        "#   set EF_IMAP_HOST=imap.example.com / EF_IMAP_USER=... / EF_IMAP_PASS=...",
        "python scripts/email_inquiry_import.py --imap --days 14",
        "# 上传私有 KV（公开仓库不含客户数据）",
        "wrangler kv key put page:email-priority.json --binding EF_DATA --remote --path data/email-priority.json",
        "wrangler kv key put page:email-inquiries.json --binding EF_DATA --remote --path data/email-inquiries.json",
        "```", "",
    ]
    rp.write_text("\n".join(lines), encoding="utf-8")

    print("已生成：", args.out)
    print("优先清单：", args.priority_out)
    print("复核清单：", args.report)
    print("扫描 %s 封｜纳入 %d 封｜会话 %d 个｜优先 %d 条｜排除 %d 封" % (
        meta.get("scanned", len(messages)), len(kept), len(records), len(priority), sum(excluded.values())))


if __name__ == "__main__":
    main()
