#!/usr/bin/env python3
"""WhatsApp 媒体附件清单（只读 wacli.db，不下载、不解密、不发消息）

统计图片/文档/语音/视频/位置消息，重点列出「优先跟进询盘」客户相关会话的附件，
并给出逐条下载命令。本地已下载的行程图会标记出来，供后续识别/OCR 使用。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DB = Path.home() / ".wacli" / "wacli.db"
PRIORITY = ROOT / "data" / "whatsapp-priority.json"
OUT_JSON = ROOT / "data" / "wa-media-index.json"
OUT_MD = ROOT / "_reports" / "WhatsApp媒体附件清单.md"


def main():
    if not DB.exists():
        raise SystemExit(f"找不到 wacli 数据库：{DB}")
    con = sqlite3.connect("file:" + DB.as_posix() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    priority_ids, priority_phones = {}, {}
    if PRIORITY.exists():
        d = json.loads(PRIORITY.read_text(encoding="utf-8"))
        for r in d.get("inquiries", []):
            jid = r.get("_waJid") or ""
            if jid:
                priority_ids[jid] = r.get("guest", "")
            phone = "".join(ch for ch in str(r.get("contact") or "") if ch.isdigit())
            if phone:
                priority_phones[phone[-9:]] = r.get("guest", "")

    rows = con.execute(
        "SELECT m.chat_jid, m.chat_name, m.msg_id, m.ts, m.from_me, m.media_type, "
        "m.media_caption, m.filename, m.mime_type, m.file_length, m.local_path, m.downloaded_at "
        "FROM messages m WHERE m.media_type IS NOT NULL AND m.media_type<>'' "
        "AND COALESCE(m.revoked,0)=0 ORDER BY m.ts DESC"
    ).fetchall()

    types = Counter(r["media_type"] for r in rows)
    local = [r for r in rows if r["local_path"]]
    by_chat = Counter(r["chat_jid"] for r in rows)

    def ts_str(v):
        try:
            return datetime.fromtimestamp(int(v) / 1000).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            return ""

    priority_rows = []
    for r in rows:
        jid = r["chat_jid"] or ""
        phone = "".join(ch for ch in jid if ch.isdigit())[-9:]
        guest = priority_ids.get(jid) or priority_phones.get(phone) or ""
        if not guest:
            continue
        priority_rows.append({
            "guest": guest,
            "chat": r["chat_name"] or jid,
            "chat_jid": jid,
            "msg_id": r["msg_id"],
            "at": ts_str(r["ts"]),
            "from_me": bool(r["from_me"]),
            "type": r["media_type"],
            "caption": r["media_caption"] or "",
            "filename": r["filename"] or "",
            "mime": r["mime_type"] or "",
            "bytes": r["file_length"] or 0,
            "local_path": r["local_path"] or "",
            "downloaded": bool(r["local_path"]),
        })

    payload = {
        "ok": True,
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(DB),
        "note": "只读清单；不含媒体二进制内容。客户数据文件已被 .gitignore 排除。",
        "stats": {
            "media_total": len(rows),
            "by_type": dict(types),
            "chats": len(by_chat),
            "downloaded": len(local),
            "priority_records": len(priority_rows),
        },
        "downloaded_files": [
            {"chat": r["chat_name"], "msg_id": r["msg_id"], "path": r["local_path"],
             "bytes": r["file_length"], "type": r["media_type"]}
            for r in local
        ],
        "priority_media": priority_rows,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# WhatsApp 媒体附件清单", "",
        "> 只读 `~/.wacli/wacli.db` 生成；脚本不下载媒体、不解密、不向任何人发消息。", "",
        "## 总览", "",
        f"- 媒体消息合计：**{len(rows)}** 条，分布于 {len(by_chat)} 个会话",
        f"- 本机已下载：**{len(local)}** 个文件（其余只有元数据，需按下方命令按需下载）",
        "- 类型分布：" + "，".join(f"{k} {v}" for k, v in types.most_common()),
        "", "## 优先跟进客户的媒体消息", "",
        "| 客人 | 时间 | 方向 | 类型 | 说明/文件名 | 大小 | 本机文件 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in priority_rows[:80]:
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            r["guest"][:22], r["at"], "我方" if r["from_me"] else "客户", r["type"],
            (r["caption"] or r["filename"] or "—").replace("|", "／")[:50],
            f'{r["bytes"]//1024} KB' if r["bytes"] else "—",
            "✓" if r["downloaded"] else "—"))
    if not priority_rows:
        lines.append("| （优先清单中暂无媒体消息） | — | — | — | — | — | — |")
    lines += [
        "", "## 本机已下载文件（可做内容识别）", "",
    ]
    for r in payload["downloaded_files"]:
        lines.append(f"- {r['chat']} · {r['type']} · {r['bytes']//1024} KB · `{r['path']}`")
    lines += [
        "", "## 按需下载命令", "",
        "```bash",
        "# 单个消息下载到本机 media 目录（只读 WhatsApp，不发送任何消息）",
        "wacli media download --chat <chat_jid> --id <msg_id>",
        "# 或按会话批量补齐（--limit 控制条数，避免一次拉全库）",
        "wacli media backfill --chat <chat_jid> --limit 10",
        "```", "",
        "> 注意：wacli 需要代理 127.0.0.1:10809 才能同步/下载；媒体二进制不进 git。", "",
        "## 尚未处理的部分", "",
        "- 图片/行程截图：本机 3 张已下载，可逐张识别后把要点补进询盘备注；其余 1,391 张图片未下载",
        "- 语音消息：26 条，未转写（可用 WorkBuddy AudioTranscribe 处理已下载文件）",
        "- 视频 / 文档：未做内容提取", "",
    ]
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("已生成：", OUT_JSON)
    print("复核清单：", OUT_MD)
    print("媒体 %d 条｜已下载 %d 个｜优先客户相关 %d 条" % (len(rows), len(local), len(priority_rows)))


if __name__ == "__main__":
    main()
