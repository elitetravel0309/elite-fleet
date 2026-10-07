# -*- coding: utf-8 -*-
"""
Elite Fleet 收款收据 PDF 生成 + 邮件外发
用法:
  python scripts/send_receipt.py -j qa-receipt.json [--to guest@example.com] [--pdf out.pdf]

数据源 qa-receipt.json 由工作台「导出邮件 JSON」或 quote-agent「导出邮件 JSON」生成。
SMTP 凭据从 scripts/.smtp.env 或环境变量读取（不落库、不入 git）。
"""
import argparse, json, os, smtplib, sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import formataddr
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

def load_smtp_env():
    env = {}
    env_file = SCRIPT_DIR / '.smtp.env'
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    env.setdefault('SMTP_USER', os.environ.get('SMTP_USER', ''))
    env.setdefault('SMTP_PASS', os.environ.get('SMTP_PASS', ''))
    env.setdefault('SMTP_FROM', os.environ.get('SMTP_FROM', 'booking@chinawondercars.com'))
    env.setdefault('SMTP_FROM_NAME', os.environ.get('SMTP_FROM_NAME', 'China Wonder Cars Customer Service'))
    env.setdefault('SMTP_HOST', os.environ.get('SMTP_HOST', 'smtp.feishu.cn'))
    env.setdefault('SMTP_PORT', os.environ.get('SMTP_PORT', '465'))
    return env

def build_pdf(payload, out_pdf):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)

    # 中文字体
    fonts_dir = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
    reg = []
    for name, file in (('MSYH', 'msyh.ttc'), ('MSYHBD', 'msyhbd.ttc')):
        p = fonts_dir / file
        if p.exists():
            try:
                pdfmetrics.registerFont(TTFont(name, str(p)))
                reg.append(name)
            except Exception:
                pass
    if 'MSYH' not in reg:
        print('[warn] 未找到微软雅黑字体，中文可能显示为方块，建议安装 msyh.ttc', file=sys.stderr)
        fallback = fonts_dir / 'simhei.ttf'
        if fallback.exists():
            try:
                pdfmetrics.registerFont(TTFont('MSYH', str(fallback)))
                reg.append('MSYH')
            except Exception:
                pass

    F = 'MSYH' if 'MSYH' in reg else 'Helvetica'
    FB = 'MSYHBD' if 'MSYHBD' in reg else F
    st_title = ParagraphStyle('t', fontName=FB, fontSize=20, alignment=1, leading=26, textColor=HexColor('#1a1a1a'))
    st_sub = ParagraphStyle('s', fontName=F, fontSize=10, alignment=1, leading=14, textColor=HexColor('#777777'))
    st_no = ParagraphStyle('n', fontName=F, fontSize=9, alignment=1, leading=13, textColor=HexColor('#999999'))
    st_h = ParagraphStyle('h', fontName=FB, fontSize=10, leading=14, textColor=HexColor('#333333'))
    st_b = ParagraphStyle('b', fontName=F, fontSize=9.5, leading=15)
    st_f = ParagraphStyle('f', fontName=F, fontSize=8.5, leading=13, textColor=HexColor('#888888'))
    st_green = ParagraphStyle('g', fontName=FB, fontSize=10, leading=15, textColor=HexColor('#2E7D6B'))
    st_orange = ParagraphStyle('o', fontName=FB, fontSize=10, leading=15, textColor=HexColor('#d48806'))

    svc = payload.get('service') or {}
    paid = float(payload.get('paid') or 0)
    balance = float(payload.get('balance') or 0)
    total = float(svc.get('quote') or payload.get('total') or 0)
    method = payload.get('method') or '—'
    pay_date = payload.get('payDate') or '—'

    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4,
                            leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm)
    story = []
    story.append(Paragraph('Elite Travel 曦驰旅游', st_title))
    story.append(Paragraph('China Wonder Tours · 收款收据 / Payment Receipt', st_sub))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph('Receipt No：%s　·　日期：%s' % (payload.get('receiptNo',''), payload.get('date','')), st_no))
    story.append(Spacer(1, 4*mm))

    rows = [
        [Paragraph('客人 / Guest', st_h), Paragraph('%s' % (payload.get('guest') or ''), st_b)],
        [Paragraph('服务日期 / Service Date', st_h), Paragraph('%s %s' % (svc.get('date',''), svc.get('time','') or ''), st_b)],
        [Paragraph('线路 / Route', st_h), Paragraph('%s' % (svc.get('route') or ''), st_b)],
        [Paragraph('车型 / Vehicle', st_h), Paragraph('%s · %s 人' % (svc.get('vehicle') or '—', svc.get('pax') or '—'), st_b)],
        [Paragraph('服务费用 / Service Fee', st_h), Paragraph('人民币 RMB %s 元' % format(int(total), ','), st_b)],
        [Paragraph('本次实收 / Amount Received', st_h), Paragraph('人民币 RMB %s 元' % format(int(paid), ','), st_green)],
        [Paragraph('收款方式 / Method', st_h), Paragraph('%s' % method, st_b)],
        [Paragraph('收款日期 / Paid Date', st_h), Paragraph('%s' % pay_date, st_b)],
    ]
    if balance > 0:
        rows.append([Paragraph('待收余额 / Balance Due', st_h), Paragraph('人民币 RMB %s 元（服务当日支付）' % format(int(balance), ','), st_orange)])
    else:
        rows.append([Paragraph('付款状态 / Payment Status', st_h), Paragraph('已全额付清 / Fully Paid ✓', st_green)])

    t = Table(rows, colWidths=[52*mm, 118*mm])
    t.setStyle(TableStyle([
        ('FONT', (0,0), (-1,-1), F, 9.5),
        ('GRID', (0,0), (-1,-1), 0.6, HexColor('#dddddd')),
        ('BACKGROUND', (0,0), (0,-1), HexColor('#f5f5f5')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 7),
        ('RIGHTPADDING', (0,0), (-1,-1), 7),
    ]))
    story.append(t)
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph('说明：本凭证确认已收到上述款项，作为您的付款记录。余额请在服务当日支付给司机或按约定方式支付。如通过 PayPal 付款，实际到账金额可能因手续费略有差异。', st_f))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('Alice Li · China Wonder Cars / China Wonder Tours', st_h))
    story.append(Paragraph('E-mail: booking@chinawondertours.com · WhatsApp: +86 15347723823', st_f))
    story.append(Paragraph('www.chinawondercars.com · www.chinawondertours.com', st_f))
    doc.build(story)
    return out_pdf

def send_mail(env, to_addr, subject, payload, pdf_path):
    from_addr = env['SMTP_FROM']
    msg = MIMEMultipart('mixed')
    msg['From'] = formataddr((env['SMTP_FROM_NAME'], from_addr))
    msg['To'] = to_addr
    msg['Subject'] = subject
    body_zh = payload.get('text_zh') or ''
    body_en = payload.get('text_en') or ''
    text = 'Dear %s,\n\n%s\n\n---\n\n%s\n\nBest regards,\nAlice Li\nChina Wonder Cars / China Wonder Tours\nbooking@chinawondertours.com · +86 15347723823' % (payload.get('guest') or 'Guest', body_zh, body_en)
    msg.attach(MIMEText(text, 'plain', 'utf-8'))
    with open(pdf_path, 'rb') as f:
        part = MIMEApplication(f.read(), _subtype='pdf')
        part.add_header('Content-Disposition', 'attachment', filename='Receipt_%s.pdf' % (payload.get('receiptNo') or 'receipt'))
        msg.attach(part)

    port = int(env['SMTP_PORT'])
    if port == 465:
        server = smtplib.SMTP_SSL(env['SMTP_HOST'], port, timeout=30)
    else:
        server = smtplib.SMTP(env['SMTP_HOST'], port, timeout=30)
        server.starttls()
    try:
        server.login(env['SMTP_USER'], env['SMTP_PASS'])
        server.sendmail(from_addr, [to_addr], msg.as_string())
        print('OK: %s -> %s (%s)' % (from_addr, to_addr, pdf_path))
    finally:
        server.quit()

def main():
    ap = argparse.ArgumentParser(description='收款收据 PDF + 邮件外发')
    ap.add_argument('-j', '--json', default='qa-receipt.json', help='qa-receipt.json 路径')
    ap.add_argument('--to', help='覆盖收件人邮箱（默认取 JSON 的 email）')
    ap.add_argument('--pdf', help='PDF 输出路径（默认 Receipt_<编号>.pdf）')
    ap.add_argument('--subject', default='Payment Receipt / 收款收据', help='邮件主题')
    ap.add_argument('--no-send', action='store_true', help='只生成 PDF 不发送')
    args = ap.parse_args()

    jp = Path(args.json)
    if not jp.exists():
        print('错误：找不到 %s。请先在工作台或 quote-agent 导出 qa-receipt.json。' % jp, file=sys.stderr)
        sys.exit(1)
    payload = json.loads(jp.read_text(encoding='utf-8'))
    to_addr = args.to or payload.get('email') or ''
    if not to_addr:
        print('错误：JSON 中没有 email 字段，请用 --to 指定收件人。', file=sys.stderr)
        sys.exit(1)

    env = load_smtp_env()
    if not args.no_send and (not env['SMTP_USER'] or not env['SMTP_PASS']):
        print('错误：缺少 SMTP 凭据。请在 scripts/.smtp.env 配置 SMTP_USER/SMTP_PASS（参考 .smtp.env.example）。', file=sys.stderr)
        sys.exit(1)

    pdf_path = args.pdf or ('Receipt_%s.pdf' % (payload.get('receiptNo') or 'receipt'))
    build_pdf(payload, pdf_path)
    print('PDF 已生成：%s' % pdf_path)
    if args.no_send:
        print('已跳过发送（--no-send）')
        return
    send_mail(env, to_addr, args.subject, payload, pdf_path)

if __name__ == '__main__':
    main()
