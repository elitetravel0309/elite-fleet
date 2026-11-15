#!/usr/bin/env python3
"""把 WhatsApp 快捷回复库 / 报价模板库 / 成交线路报价台账整理成可检索知识条目。

只读三个源文档，逐节保留原文（不压缩、不改写），输出 data/wa-kb.json：
  - shortcut  快捷回复指令（/payment、/included…）
  - template  报价模板（T1-T7、通用骨架、附件段）
  - table     定价速查表 / 成交明细 / 线路排行 / 价格口径
  - rule      使用纪律
  - overview  台账总览
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = Path(r"E:/WorkBuddy/WorkBuddy/2026-11-12-23-11-44")
OUT = ROOT / "data" / "wa-kb.json"
SOURCES = [
    ("WhatsApp快捷回复库.md", "快捷回复库"),
    ("报价模板库.md", "报价模板库"),
    ("WhatsApp成交线路报价台账.md", "成交线路报价台账"),
]
TAG_RULES = [
    ("收款", r"payment|定金|付款|收款|deposit|paypal|alipay"),
    ("包含项", r"included|包含|including"),
    ("接机", r"meetgreet|接机|airport|机场|flight|航班|layover|中转"),
    ("长城", r"长城|great wall|mutianyu|慕田峪|金山岭|badaling"),
    ("包车", r"charter|包车|全天|per day|多日"),
    ("车型", r"cars|车型|seater|商务|van|sedan|车辆"),
    ("报价单", r"quote|quotation|模板|骨架|summary|报价"),
    ("成交", r"成交|付款信号|确认成交|\bwon\b"),
    ("价格", r"价格|定价|\bRMB\b|区间|报价区间"),
    ("线路", r"线路|排行|\bcity\b|\broute\b|城际"),
    ("纪律", r"纪律|丢单|教训|注意"),
    ("大团", r"大团|中巴|大巴|group"),
]


def guess_tags(text: str) -> list[str]:
    return [tag for tag, pat in TAG_RULES if re.search(pat, text, re.I)]


def kind_of(source: str, title: str) -> str:
    t = title.lower()
    if source == "快捷回复库":
        return "shortcut"
    if source == "成交线路报价台账":
        if "纪律" in title:
            return "rule"
        if "明细" in title or "抽样" in title or "排行" in title or "口径" in title or "分布" in title or "总览" in title:
            return "table"
        return "overview"
    if re.match(r"^T\d", title) or "骨架" in title or "附件段" in title or "模板" in title:
        return "template"
    if "速查" in title or "定价" in title:
        return "table"
    if "纪律" in title or "注意" in title:
        return "rule"
    return "template"


def split_sections(md: str) -> list[tuple[str, str]]:
    out, cur_title, buf = [], "（文档开头）", []
    for line in md.splitlines():
        if line.startswith("## "):
            if buf:
                out.append((cur_title, "\n".join(buf).strip()))
            cur_title, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if buf:
        out.append((cur_title, "\n".join(buf).strip()))
    return [(t, b) for t, b in out if b.strip()]


def main():
    items, missing = [], []
    for fname, source in SOURCES:
        p = SRC_DIR / fname
        if not p.exists():
            missing.append(fname)
            continue
        md = p.read_text(encoding="utf-8", errors="ignore")
        for idx, (title, body) in enumerate(split_sections(md)):
            if title == "（文档开头）":
                title = source + " · 文档说明与使用方式"
            full = body  # 原文完整保留，不做压缩
            iid = "kb_" + hashlib.sha1((fname + "|" + title).encode("utf-8")).hexdigest()[:10]
            items.append({
                "id": iid,
                "source": source,
                "file": fname,
                "type": kind_of(source, title),
                "title": title,
                "tags": guess_tags(title + " " + full[:250]),
                "body": full,
                "order": idx,
            })
    payload = {
        "ok": True,
        "type": "elite-fleet/wa-kb",
        "version": 1,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sourceDir": str(SRC_DIR),
        "note": "逐节保留源文档原文；工作台「智能客服 → 话术与模板速查」可检索并一键插入回复",
        "stats": {
            "items": len(items),
            "shortcut": sum(1 for x in items if x["type"] == "shortcut"),
            "template": sum(1 for x in items if x["type"] == "template"),
            "table": sum(1 for x in items if x["type"] == "table"),
            "missing": missing,
        },
        "items": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已生成：", OUT)
    print("条目：", len(items), payload["stats"])
    if missing:
        print("缺失源文档：", ", ".join(missing))


if __name__ == "__main__":
    main()
