#!/usr/bin/env python3
"""把已识别的 WhatsApp 行程图要点幂等回写到询盘 JSON 与复核清单。

读取 data/wa-media-ocr.json（人工视觉逐字转录），给对应客人的询盘追加备注、
补全线路/城市/用车日（只补空字段），并输出 data/wa-ocr-notes.json 供
scripts/push_inquiries_to_cloud.py --append-notes 同步线上。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
OCR = ROOT / "data" / "wa-media-ocr.json"
TARGETS = [ROOT / "data" / "whatsapp-priority.json", ROOT / "data" / "whatsapp-inquiries.json"]
NOTES_OUT = ROOT / "data" / "wa-ocr-notes.json"
REPORT = ROOT / "_reports" / "WhatsApp媒体附件清单.md"
MARK = "[行程图识别]"


def note_text(o: dict) -> str:
    s = o["summary"]
    return (
        MARK + " 客户发来 3 张西语行程截图，要点（图片原文逐字转录见 data/wa-media-ocr.json）：\n"
        + "· 类型：" + s["type"] + "\n"
        + "· 线路：" + s["route"] + "\n"
        + "· 酒店：" + "；".join(s["hotels"]) + "\n"
        + "· 日期：" + s["dates"] + "\n"
        + "· 待确认：" + "；".join(s["openQuestions"])
    )


def main():
    if not OCR.exists():
        raise SystemExit(f"找不到识别结果：{OCR}")
    o = json.loads(OCR.read_text(encoding="utf-8"))
    guest, jid = o.get("guest", ""), o.get("chat_jid", "")
    jid_prefix = jid.split("@")[0]
    text, touched = note_text(o), {}
    for path in TARGETS:
        if not path.exists():
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        n = 0
        for r in d.get("inquiries", []):
            if guest and r.get("guest") != guest:
                if not (jid_prefix and jid_prefix in str(r.get("_waJid") or "")):
                    continue
            if MARK not in str(r.get("note") or ""):
                r["note"] = (str(r.get("note") or "").rstrip() + "\n" + text).strip()
            for k, v in (("route", o["summary"]["route"]), ("city", "北京"),
                         ("serviceDate", o["summary"].get("serviceDateGuessed", ""))):
                if not r.get(k):
                    r[k] = v
            r["_serviceDateGuessed"] = True
            touched[str(r["id"])] = text
            n += 1
        path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"回写 {path.name}：{n} 条")
    NOTES_OUT.write_text(json.dumps(touched, ensure_ascii=False, indent=2), encoding="utf-8")

    if REPORT.exists():
        md = REPORT.read_text(encoding="utf-8")
        if MARK not in md:
            s = o["summary"]
            md += (
                "\n## 行程图识别结果（本机已下载的 3 张）\n\n"
                f"- 客户：**{guest}** ｜ 类型：{s['type']}\n"
                f"- 线路：{s['route']}\n"
                f"- 酒店：{'；'.join(s['hotels'])}\n"
                f"- 日期：{s['dates']}\n"
                f"- 待确认：{'；'.join(s['openQuestions'])}\n\n"
                "> 三张图的西语原文已逐字转录在 `data/wa-media-ocr.json`；"
                "识别要点已幂等回写到 `data/whatsapp-*.json` 的对应询盘备注，"
                "并可用 `push_inquiries_to_cloud.py --append-notes data/wa-ocr-notes.json` 同步线上。\n"
            )
            REPORT.write_text(md, encoding="utf-8")
            print("复核清单已追加识别章节")
    print("生成线上追加备注文件：", NOTES_OUT, "（", len(touched), " 条）")


if __name__ == "__main__":
    main()
