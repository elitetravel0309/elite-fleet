#!/usr/bin/env python3
"""微信聊天导出 → 全渠道询盘中枢（channel=wechat）

支持微信桌面版「导出聊天记录」/ 复制粘贴保存的 txt，以及带表头的 csv。
只读本地文件，不联网、不改动微信数据；生成的两份 JSON 已被 .gitignore 排除。

用法：
  python scripts/wechat_inquiry_import.py --input "导出目录或文件" [--input 更多文件]
  python scripts/wechat_inquiry_import.py --text "张三 2026-11-12 10:23\n需要 12 月 24 日机场接机，3 人"
  python scripts/wechat_inquiry_import.py            # 无输入：生成空清单 + 操作说明
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from email_inquiry_import import guess_city, guess_pax, guess_service_date  # 复用同一套业务抽取规则

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "wechat-inquiries.json"
DEFAULT_PRIORITY = ROOT / "data" / "wechat-priority.json"
DEFAULT_REPORT = ROOT / "_reports" / "微信询盘并入清单.md"
SOURCE_NOTE = "微信聊天导出文本只读解析：按会话文件分组，取客户发言与需求关键词；字段为规则推断，导入后需人工核对"

ME_NAMES = ("alice", "曦驰", "客服", "china wonder", "booking", "我", "李红", "hong")
INQ_KEYWORDS = re.compile(
    r"(包车|接送|接机|送机|用车|报价|多少钱|价格|几人|几位|行程|导游|门票|酒店|机场|"
    r"private car|quote|quotation|price|transfer|pickup|drop.?off|driver|guide|"
    r"airport|hotel|tour|pax|passenger|seater|itinerary|RMB|CNY|USD)",
    re.I,
)
# 常见导出格式
LINE_PATTERNS = [
    # [2026-11-12 10:23:45] 张三: 内容
    re.compile(r"^\[?(\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)\]?\s+([^:：]{1,40})[:：]\s*(.*)$"),
    # 2026-11-12 10:23:45 张三: 内容
    re.compile(r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)\s+([^:：]{1,40})[:：]\s*(.*)$"),
    # 张三(2026-11-12 10:23): 内容
    re.compile(r"^([^:：()]{1,40})\s*[（(](\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2})?)[)）]\s*[:：]?\s*(.*)$"),
    # 张三 2026-11-12 10:23:45 （内容在下一行）
    re.compile(r"^([^:：]{1,40})\s+(\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)\s*$"),
]
CN_DATE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?")
CN_SHORT_DATE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
CN_PAX = re.compile(r"(\d+)\s*(?:个人|人|位|pax)")


def is_me(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(x in n for x in ME_NAMES)


def parse_txt(text: str, chat_name: str) -> list[dict]:
    rows, pending = [], None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        hit = None
        for pat in LINE_PATTERNS:
            m = pat.match(line)
            if m:
                g = m.groups()
                if len(g) == 2:  # 「张三 2026-11-12 10:23」格式，内容在后续行
                    who, when, body = g[0], g[1], ""
                elif g[0][:4].isdigit():
                    when, who, body = g[0], g[1], g[2]
                elif g[1][:4].isdigit():
                    who, when, body = g[0], g[1], g[2]
                else:
                    when, who, body = g[0], g[1], g[2]
                hit = (when, who.strip(), body.strip())
                break
        if hit:
            when, who, body = hit
            rows.append({"at": when, "who": who, "text": body})
            pending = rows[-1]
        elif pending is not None:
            pending["text"] += " " + line.strip()
        else:
            rows.append({"at": "", "who": "", "text": line.strip()})
            pending = rows[-1]
    for r in rows:
        r["chat"] = chat_name
    return rows


def parse_csv(path: Path, chat_name: str) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
        sample = f.read(2048)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        fields = {k.lower(): k for k in (reader.fieldnames or [])}
        def pick(*names):
            for n in names:
                if n in fields:
                    return fields[n]
            return None
        c_at, c_who, c_text = pick("time", "date", "at", "时间", "日期"), \
            pick("sender", "from", "name", "发言人", "发送人", "昵称"), \
            pick("content", "message", "text", "内容", "消息")
        if not c_text:
            return []
        for row in reader:
            rows.append({
                "at": (row.get(c_at) or "").strip() if c_at else "",
                "who": (row.get(c_who) or "").strip() if c_who else "",
                "text": (row.get(c_text) or "").strip(),
                "chat": chat_name,
            })
    return rows


def chat_date(rows: list[dict], today: datetime) -> str:
    for r in reversed(rows):
        m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", r.get("at") or "")
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return today.strftime("%Y-%m-%d")


def build_record(chat: str, rows: list[dict], today: datetime) -> dict | None:
    customers = [r for r in rows if r.get("who") and not is_me(r["who"])]
    if not customers:
        customers = [r for r in rows if r.get("text")]
    if not customers:
        return None
    guest = (customers[0].get("who") or chat or "微信客人").strip()[:60]
    text_all = " ".join(r.get("text") or "" for r in rows)
    cust_text = " ".join(r.get("text") or "" for r in customers)
    if not INQ_KEYWORDS.search(cust_text):
        return None
    date = chat_date(rows, today)
    last = rows[-1] if rows else {}
    last_is_customer = bool(last) and not is_me(last.get("who") or "")
    status = "following" if last_is_customer else "quoted"
    follow_up = (today + timedelta(days=1)).strftime("%Y-%m-%d") + " 09:00" if status == "following" else ""
    svc = guess_service_date(text_all, today.year)
    m = CN_DATE.search(cust_text)
    if not svc and m:
        svc = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if not svc:  # 「12月24日」无年份：按最近的未来日期推断
        m2 = CN_SHORT_DATE.search(cust_text)
        if m2:
            mon, day = int(m2.group(1)), int(m2.group(2))
            year = today.year + (1 if (mon, day) < (today.month, today.day) else 0)
            if 1 <= mon <= 12 and 1 <= day <= 31:
                svc = f"{year:04d}-{mon:02d}-{day:02d}"
    pax = guess_pax(cust_text)
    m3 = CN_PAX.search(cust_text)
    if m3 and (pax <= 1 or int(m3.group(1)) > pax):
        pax = min(int(m3.group(1)), 40)
    rid = "wx_" + hashlib.sha1((chat + "|" + guest).encode("utf-8")).hexdigest()[:12]
    snippet = re.sub(r"\s+", " ", cust_text)[:420]
    return {
        "id": rid,
        "guest": guest,
        "contact": "微信: " + guest,
        "email": "",
        "date": date,
        "serviceDate": svc,
        "city": guess_city(text_all),
        "pax": pax,
        "vehicle": "",
        "status": status,
        "route": re.sub(r"\s+", " ", cust_text)[:120],
        "quote": 0,
        "cost": 0,
        "followUp": follow_up,
        "followUps": [],
        "fleetQuotes": [],
        "note": f"微信会话「{chat}」共 {len(rows)} 条消息；客户原文摘录：{snippet}",
        "source": "wechat-export",
        "channel": "wechat",
        "subject": "",
        "sourceRef": "微信会话: " + chat,
        "guestKey": "g:" + re.sub(r"\s+", "", guest).lower(),
        "_threadId": rid,
        "_msgCount": len(rows),
        "_sourceSnippet": snippet[:300],
        "_serviceDateGuessed": bool(svc),
        "_autoImported": True,
        "_lastModified": int(today.timestamp() * 1000),
    }


def main():
    ap = argparse.ArgumentParser(description="微信聊天导出 → 全渠道询盘中枢 JSON")
    ap.add_argument("--input", action="append", default=[], help="txt/csv 文件或目录，可重复")
    ap.add_argument("--text", default="", help="直接传入一段聊天文本")
    ap.add_argument("--chat-name", default="粘贴的微信聊天", help="--text 模式的会话名")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--priority-out", default=str(DEFAULT_PRIORITY))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    args = ap.parse_args()

    today = datetime.now()
    files, rows_by_chat = [], defaultdict(list)
    for raw in args.input:
        p = Path(raw)
        if p.is_dir():
            files += sorted([x for x in p.iterdir() if x.suffix.lower() in (".txt", ".csv", ".log")])
        elif p.exists():
            files.append(p)
    if args.text:
        rows_by_chat[args.chat_name] = parse_txt(args.text, args.chat_name)
    for f in files:
        chat = f.stem
        try:
            rows = parse_csv(f, chat) if f.suffix.lower() == ".csv" else parse_txt(f.read_text(encoding="utf-8", errors="ignore"), chat)
        except OSError as e:
            print("跳过", f.name, e)
            continue
        rows_by_chat[chat].extend(rows)

    records = []
    for chat, rows in rows_by_chat.items():
        r = build_record(chat, rows, today)
        if r:
            records.append(r)
    records.sort(key=lambda r: r["date"], reverse=True)
    cutoff = (today - timedelta(days=14)).strftime("%Y-%m-%d")
    priority = [r for r in records if r["date"] >= cutoff and r["status"] == "following"]

    base = {
        "ok": True, "type": "elite-fleet/inquiries", "version": 1,
        "generatedAt": today.strftime("%Y-%m-%d %H:%M:%S"),
        "channel": "wechat", "source": "微信聊天导出文本（本地只读）",
        "sourceNote": SOURCE_NOTE,
        "stats": {"files": len(files), "chats": len(rows_by_chat), "inquiries": len(records), "priority": len(priority)},
    }
    for path, rows, note in [
        (Path(args.out), records, "全量微信询盘"),
        (Path(args.priority_out), priority, "优先跟进（最近 14 天客户最后发言、待回复）"),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(base, note=note, inquiries=rows), ensure_ascii=False, indent=2), encoding="utf-8")

    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 微信询盘并入清单", "", "> " + SOURCE_NOTE, "",
        f"- 输入文件：{len(files)} 个 ｜ 会话：{len(rows_by_chat)} 个 ｜ 识别为询盘：{len(records)} 条 ｜ 优先跟进：{len(priority)} 条", "",
        "## 接入方式（三选一）", "",
        "1. **粘贴建档（最快，零配置）**：工作台「智能客服」粘贴微信聊天原文 → 来源渠道选「微信」→ 解析 → 保存到询价管理",
        "2. **导出批量并入**：微信桌面版把目标聊天导出/复制为 txt（或 csv）放到一个目录，运行：",
        "   `python scripts/wechat_inquiry_import.py --input 导出目录`",
        "3. **本地数据库自动抽取（可选，需管理员权限）**：微信 4.x WCDB 离线解密流程见 `~/.workbuddy/skills/wechat-wcdb-local-extract/`，解密出的消息先转成 txt 再走方式 2；本脚本不直接读取微信进程内存", "",
        "## 并入方式", "",
        "- 工作台「数据与同步 → 微信聊天询盘并入」→ 拉取云端清单 / 导入本地 JSON",
        "- 幂等 `id = wx_<会话名+客人哈希>`；同渠道同一客人重复导入只补空字段，不覆盖手改状态/报价/备注", "",
        "## 已识别记录", "",
        "| 客人 | 会话 | 日期 | 状态 | 城市 | 人数 | 用车日 | 需求摘录 |", "|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["guest"].replace("|", "／"), r["sourceRef"].replace("微信会话: ", "").replace("|", "／")[:24],
            r["date"], r["status"], r["city"] or "—", r["pax"], r["serviceDate"] or "—",
            (r["route"] or "").replace("|", "／")[:70]))
    if not records:
        lines.append("| （暂无） | — | — | — | — | — | — | 尚未提供聊天导出文件 |")
    lines += ["", "> 未识别为询盘的会话（闲聊/群通知）不会建档；字段均为规则推断，以人工核对为准。", ""]
    rp.write_text("\n".join(lines), encoding="utf-8")

    print("已生成：", args.out, "｜", args.priority_out)
    print("复核清单：", args.report)
    print("文件 %d 个｜会话 %d 个｜询盘 %d 条｜优先 %d 条" % (
        len(files), len(rows_by_chat), len(records), len(priority)))


if __name__ == "__main__":
    main()
