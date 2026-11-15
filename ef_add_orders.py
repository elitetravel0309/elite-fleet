# -*- coding: utf-8 -*-
"""Append two new orders to elite-fleet workbench (KV data:workbench)."""
import json, base64, time, urllib.request, os, sys

# 安全（2026-11-15）：凭据从环境变量读取，不再写进脚本/仓库。
# PowerShell:  $env:EF_AUTH_USER="elite"; $env:EF_AUTH_PASS="..."; $env:EF_UPLOAD_KEY="..."
# Bash:        export EF_AUTH_USER=elite EF_AUTH_PASS=... EF_UPLOAD_KEY=...
API = os.environ.get("EF_API", "https://fleet.chinawondercars.com")
_user = os.environ.get("EF_AUTH_USER", "")
_pass = os.environ.get("EF_AUTH_PASS", "")
KEY = os.environ.get("EF_UPLOAD_KEY", "")
if not (_user and _pass and KEY):
    sys.exit("缺少 EF_AUTH_USER / EF_AUTH_PASS / EF_UPLOAD_KEY 环境变量，已停止（避免写入线上失败）")
AUTH = "Basic " + base64.b64encode((_user + ":" + _pass).encode()).decode()
NOW = int(time.time() * 1000)

def get(url):
    req = urllib.request.Request(url, headers={"Authorization": AUTH})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

data = get(API + "/api/data")
print("before: orders=%d prices=%d" % (len(data.get("orders", [])), len(data.get("prices", []))))

def new_order(**kw):
    o = {
        "id": "", "date": "", "time": "", "type": "charter", "airport": "", "flight": "",
        "route": "", "vehicle": "", "pax": 0, "guest": "", "contact": "", "driver": "",
        "plate": "", "fleet": "", "price": 0, "quote": 0, "cost": 0, "collectAmount": 0,
        "status": "inquiry", "note": "", "city": "",
        "_lastModified": NOW,
        "statusHistory": [{"status": kw.get("status", "inquiry"), "time": NOW, "note": "新订单录入"}],
    }
    o.update(kw)
    return o

orders = data.setdefault("orders", [])

orders.append(new_order(
    id="x261012_iris_mutianyu",
    date="2026-10-12",
    time="12:30",
    type="charter",
    route="北京恭王府花间堂·后海 → 慕田峪长城(下午游览,看日落) → 送回酒店",
    vehicle="七座商务车",
    pax=4,
    guest="Iris (CAMPETELLA Iris)",
    contact="WhatsApp +39 347 217 0172 / irisma@hotmail.it",
    status="confirmed",
    note=("12:30-13:00酒店接（Blossom House Prince Kung's Mansion·Beijing Houhai 北京恭王府花间堂·后海，"
          "西城区德内大街羊房胡同9号，010-53859000）；慕田峪长城下午游览，看完日落回程送回酒店；"
          "帮客人买慕田峪门票+摆渡车+往返缆车（费用已付）；护照：RIVELLO Mara YB9255799 / "
          "CAMPETELLA Iris YB3552071 / STAROCCIA Settimio YB1579371 / MUZI Lino YB5714348；"
          "价格待定；参考成本：7座慕田峪下午日落约750，建议报价1000-1100"),
    city="北京",
))

orders.append(new_order(
    id="x261015_melissa_mutianyu",
    date="2026-10-15",
    time="08:00",
    type="charter",
    route="北京朗丽兹西山花园酒店 → 慕田峪长城 → 午餐 → 市区一个点或酒店",
    vehicle="五座",
    pax=2,
    guest="Melissa Tomiczek",
    contact="WhatsApp +61 422 788 262",
    status="inquiry",
    note=("08:00酒店接（Palace Garden Hotel 北京朗丽兹西山花园酒店，海淀区丰智东路13号）；"
          "慕田峪长城；午餐；结束后送市区一个点或送回酒店；2人5座车；价格待定；"
          "参考成本：5座慕田峪+午餐+市区点约700-800，建议报价950-1000"),
    city="北京",
))

payload = {"data": data}
res = post(API + "/api/upload?key=" + KEY, payload)
print("upload:", json.dumps(res, ensure_ascii=False))

# verify
data2 = get(API + "/api/data")
print("after: orders=%d" % len(data2.get("orders", [])))
for o in data2.get("orders", [])[-2:]:
    print("-", o["id"], o["date"], o["guest"], o["status"])
