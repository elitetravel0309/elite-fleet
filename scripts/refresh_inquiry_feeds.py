#!/usr/bin/env python3
"""全渠道询盘一键刷新：重新生成各渠道 JSON → 上传私有 KV → 可选并入线上工作台。

用法：
  python scripts/refresh_inquiry_feeds.py                 # 只生成 + 上传 KV
  python scripts/refresh_inquiry_feeds.py --with-cloud   # 再并入 data:workbench（需 EF_AUTH_PASS / EF_UPLOAD_KEY）
  EF_WECHAT_EXPORT_DIR=D:/微信导出 python scripts/refresh_inquiry_feeds.py

不打印、不写入任何凭据；客户数据文件均在 .gitignore 中，不进公开仓库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WORKER_DIR = ROOT / "cloudflare-worker" / "elite-fleet-api"
DATA = ROOT / "data"
PY = sys.executable
WRANGLER = shutil.which("wrangler") or "wrangler"  # Windows 下 which 会解析到 .cmd 绝对路径

# (KV key, 本地文件)
FEEDS = [
    ("page:whatsapp-priority.json", "whatsapp-priority.json"),
    ("page:whatsapp-inquiries.json", "whatsapp-inquiries.json"),
    ("page:email-priority.json", "email-priority.json"),
    ("page:email-inquiries.json", "email-inquiries.json"),
    ("page:wechat-priority.json", "wechat-priority.json"),
    ("page:wechat-inquiries.json", "wechat-inquiries.json"),
    ("page:wa-kb.json", "wa-kb.json"),
    ("page:index.html", "../index.html"),
]


def run(args, cwd=ROOT, optional=False):
    print("▶", " ".join(str(a) for a in args))
    r = subprocess.run([str(a) for a in args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.stdout:
        print("  ", r.stdout.strip().replace("\n", "\n   ")[-800:])
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()[-500:]
        if optional:
            print("  ⚠ 跳过（非致命）：", msg)
            return False
        raise SystemExit(f"步骤失败：{args[1] if len(args) > 1 else args[0]}\n{msg}")
    return True


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-cloud", action="store_true", help="生成后并入线上 data:workbench")
    ap.add_argument("--skip-generate", action="store_true")
    ap.add_argument("--skip-upload", action="store_true")
    ap.add_argument("--only", default="", help="只上传含该字符串的 KV key")
    args = ap.parse_args()

    if not args.skip_generate:
        print("=== 1. 重新生成各渠道数据 ===")
        run([PY, SCRIPTS / "wa_inquiry_import.py"])
        run([PY, SCRIPTS / "email_inquiry_import.py"])
        wx = [PY, SCRIPTS / "wechat_inquiry_import.py"]
        d = os.environ.get("EF_WECHAT_EXPORT_DIR")
        if d:
            wx += ["--input", d]
        run(wx)
        run([PY, SCRIPTS / "build_wa_kb.py"])
        run([PY, SCRIPTS / "apply_media_ocr.py"])
        run([PY, SCRIPTS / "wa_media_index.py"], optional=True)

    if not args.skip_upload:
        print("=== 2. 上传私有 KV ===")
        for key, rel in FEEDS:
            if args.only and args.only not in key:
                continue
            p = (DATA / rel).resolve()
            if not p.exists():
                print("  ⚠ 跳过（文件不存在）：", p)
                continue
            r = subprocess.run(
                [WRANGLER, "kv", "key", "put", key, "--binding", "EF_DATA", "--remote", "--path", str(p)],
                cwd=str(WORKER_DIR), capture_output=True, text=True, encoding="utf-8", errors="replace")
            ok = r.returncode == 0
            print(f"  {'✓' if ok else '✗'} {key}  {sha(p)[:12]}  ({p.stat().st_size} bytes)")
            if not ok:
                print("   ", (r.stderr or "").strip()[-300:])
        print("=== 3. 上传后校验（sha256 与本地一致） ===")
        for key, rel in FEEDS:
            if args.only and args.only not in key:
                continue
            p = (DATA / rel).resolve()
            if not p.exists():
                continue
            tmp = Path(os.environ.get("TEMP", "/tmp")) / ("kvcheck_" + key.replace(":", "_").replace("/", "_"))
            r = subprocess.run(
                [WRANGLER, "kv", "key", "get", key, "--binding", "EF_DATA", "--remote"],
                cwd=str(WORKER_DIR), capture_output=True)
            if r.returncode == 0:
                tmp.write_bytes(r.stdout)
                print(f"  {'✓' if sha(tmp) == sha(p) else '✗ MISMATCH'} {key}")
            else:
                print("  ✗ 读取失败", key)

    if args.with_cloud:
        print("=== 4. 并入线上工作台 ===")
        run([PY, SCRIPTS / "push_inquiries_to_cloud.py", "--apply"])
    print("完成。工作台「数据与同步」可一键拉取各渠道清单。")


if __name__ == "__main__":
    main()
