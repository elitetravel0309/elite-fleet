#!/usr/bin/env python3
"""把本地生成的各渠道询盘 JSON 幂等并入线上 data:workbench。

安全措施：
  1) 先调用 /api/snapshot 做一次云端快照（可回滚）
  2) GET /api/data 取 X-EF-Rev 版本号，POST /api/upload 带上 baseVersion 走乐观锁
  3) 只补空字段、按 id + 同渠道联系方式去重，绝不覆盖工作台里手改的状态/报价/备注
  4) 所有凭据从环境变量读取，不写进源码、不打印
  5) 默认 --dry-run，需显式加 --apply 才会写线上

用法：
  python scripts/push_inquiries_to_cloud.py --dry-run
  python scripts/push_inquiries_to_cloud.py --apply
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILES = [
    ROOT / "data" / "whatsapp-priority.json",
    ROOT / "data" / "email-inquiries.json",
    ROOT / "data" / "wechat-inquiries.json",
]
CHANNELS = {"web", "whatsapp", "wechat", "email", "feishu", "manual"}


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def basic_header() -> str:
    user = env("EF_AUTH_USER", "elite")
    pwd = env("EF_AUTH_PASS")
    if not pwd:
        raise SystemExit("缺少环境变量 EF_AUTH_PASS")
    return "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()


def call(method: str, url: str, body=None, headers=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    # Cloudflare 对 python-urllib 默认 UA 会返回 403，显式声明客户端
    req.add_header("User-Agent", "EliteFleetSync/1.0 (+workbench)")
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("Authorization", basic_header())
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, dict(resp.headers), resp.read().decode("utf-8")


def channel_of(rec: dict) -> str:
    c = str(rec.get("channel") or "").lower()
    if c in CHANNELS:
        return c
    rid, src = str(rec.get("id") or "").lower(), str(rec.get("source") or "").lower()
    if rid.startswith("web_") or "chinawondercars" in src:
        return "web"
    if rid.startswith(("wa_", "waq_")) or rec.get("_waJid"):
        return "whatsapp"
    if rid.startswith(("wx_", "wechat_")):
        return "wechat"
    if rid.startswith(("eml_", "mail_", "email_")):
        return "email"
    if rec.get("_fsRecordId") or rid.startswith("fs_") or "feishu" in src:
        return "feishu"
    return "manual"


def contact_key(rec: dict) -> str:
    c = channel_of(rec)
    phone = re.sub(r"\D", "", str(rec.get("contact") or ""))[-11:]
    mail = str(rec.get("email") or "").strip().lower()
    ident = phone if len(phone) >= 7 else (mail if "@" in mail else "")
    return c + ":" + ident if ident else ""


FILL_ONLY = [
    "guest", "contact", "email", "date", "serviceDate", "city", "pax", "vehicle",
    "route", "note", "source", "sourceRef", "subject", "guestKey",
    "_waJid", "_threadId", "_sourceSnippet", "_fromMailbox",
]
KEEP_FIELDS = [
    "id", "guest", "contact", "email", "date", "serviceDate", "city", "pax", "vehicle",
    "status", "route", "quote", "cost", "followUp", "followUps", "fleetQuotes", "note",
    "source", "channel", "sourceRef", "subject", "guestKey",
    "_waJid", "_waLastQuoteAt", "_ledgerDate", "_autoImported", "_fromLedger",
    "_serviceDateGuessed", "_threadId", "_msgCount", "_sourceSnippet", "_fromMailbox",
]


def merge(cloud: dict, batches: list[dict]) -> tuple[dict, dict]:
    inquiries = list(cloud.get("inquiries") or [])
    by_id = {str(x.get("id")): x for x in inquiries if isinstance(x, dict) and x.get("id")}
    by_contact = {}
    for x in inquiries:
        k = contact_key(x)
        if k and k not in by_contact:
            by_contact[k] = x
    stats = {"added": 0, "updated": 0, "skipped": 0, "byChannel": {}}
    for rec in batches:
        if not isinstance(rec, dict) or not rec.get("id") or not rec.get("guest"):
            stats["skipped"] += 1
            continue
        rid = str(rec["id"])
        ch = channel_of(rec)
        stats["byChannel"][ch] = stats["byChannel"].get(ch, 0) + 1
        if rid in by_id:
            cur = by_id[rid]
            changed = False
            for k in FILL_ONLY:
                v = rec.get(k)
                if v in (None, "", 0):
                    continue
                old = cur.get(k)
                if old in (None, "", 0) and v != old:
                    cur[k] = v
                    changed = True
            stats["updated" if changed else "skipped"] += 1
            continue
        rec = dict(rec)
        rec["channel"] = ch
        probe_key = contact_key(rec)
        if probe_key and probe_key in by_contact:
            stats["skipped"] += 1
            continue
        clean = {k: v for k, v in rec.items() if k in KEEP_FIELDS or k.startswith("_")}
        clean["_lastModified"] = int(__import__("time").time() * 1000)
        inquiries.append(clean)
        by_id[rid] = clean
        if probe_key:
            by_contact[probe_key] = clean
        stats["added"] += 1
    out = dict(cloud)
    out["inquiries"] = inquiries
    return out, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=[str(x) for x in DEFAULT_FILES])
    ap.add_argument("--url", default=env("EF_API_URL", "https://fleet.chinawondercars.com"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-snapshot", action="store_true")
    ap.add_argument("--append-notes", default="", help="{id: 备注文本} JSON，幂等追加到线上询盘备注")
    args = ap.parse_args()
    if not (args.dry_run or args.apply):
        raise SystemExit("请显式指定 --dry-run（预览）或 --apply（写入线上）")
    key = env("EF_UPLOAD_KEY")
    if args.apply and not key:
        raise SystemExit("缺少环境变量 EF_UPLOAD_KEY")
    if not env("EF_AUTH_PASS"):
        raise SystemExit("缺少环境变量 EF_AUTH_PASS")

    batches = []
    for f in args.files:
        p = Path(f)
        if not p.exists():
            print("跳过（文件不存在）：", p)
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        rows = d.get("inquiries", []) if isinstance(d, dict) else d
        print(f"读取 {p.name}: {len(rows)} 条")
        batches.extend(rows)

    status, headers, raw = call("GET", args.url.rstrip("/") + "/api/data")
    cloud = json.loads(raw)
    rev = next((v for k, v in headers.items() if k.lower() == "x-ef-rev"), "0")
    print(f"线上工作台：{len(cloud.get('inquiries') or [])} 条询盘，rev={rev}，HTTP {status}")

    notes_changed = 0
    if args.append_notes:
        notes = json.loads(Path(args.append_notes).read_text(encoding="utf-8"))
        for rec in cloud.get("inquiries") or []:
            t = notes.get(str(rec.get("id")))
            if not t:
                continue
            if t[:20] in str(rec.get("note") or ""):
                continue
            rec["note"] = (str(rec.get("note") or "").rstrip() + "\n" + t).strip()
            notes_changed += 1
        print("追加备注：", notes_changed, "条")

    merged, stats = merge(cloud, batches)
    print("合并结果：新增 %(added)d 条 / 补全 %(updated)d 条 / 跳过 %(skipped)d 条" % stats)
    print("按渠道：", stats["byChannel"])
    if args.dry_run or (notes_changed == 0 and stats["added"] == 0 and stats["updated"] == 0):
        print("未写入线上（--dry-run 或无变更）")
        return

    if not args.no_snapshot:
        try:
            s, _, sraw = call("POST", args.url.rstrip("/") + "/api/snapshot")
            print("云端快照：", s, sraw[:160])
        except urllib.error.HTTPError as e:
            print("快照失败（继续写入）：", e.code, e.read().decode("utf-8", "ignore")[:160])

    s, _, sraw = call(
        "POST", args.url.rstrip("/") + "/api/upload?key=" + urllib.parse.quote(key),
        body={"data": merged, "baseVersion": int(rev)},
    )
    result = json.loads(sraw)
    print("写入结果：", s, json.dumps(result, ensure_ascii=False)[:220])
    if not result.get("ok"):
        raise SystemExit("写入失败，请用 /api/backups 下载快照回滚")
    s2, _, raw2 = call("GET", args.url.rstrip("/") + "/api/data")
    after = json.loads(raw2)
    print("校验：线上询盘 %d 条，orders %d 条，HTTP %d" % (
        len(after.get("inquiries") or []), len(after.get("orders") or []), s2))


if __name__ == "__main__":
    main()
