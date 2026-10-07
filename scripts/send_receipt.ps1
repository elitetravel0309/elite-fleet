# 收款收据 PDF 邮件外发（方向1）
# 用法：
#   1) 在工作台订单「收款单」弹窗点「导出邮件 JSON」，或在 quote-agent 收据卡点「导出邮件 JSON」，得到 qa-receipt.json
#   2) 复制 qa-receipt.json 到 elite-fleet 目录
#   3) 首次运行前复制 .smtp.env.example 为 .smtp.env 并填入 SMTP_USER / SMTP_PASS
#   4) 运行本脚本：
#      .\scripts\send_receipt.ps1                 # 发到 JSON 里的 email
#      .\scripts\send_receipt.ps1 -To xxx@x.com   # 覆盖收件人
#      .\scripts\send_receipt.ps1 -NoSend         # 只生成 PDF 不发信
$ErrorActionPreference = 'Stop'
param(
  [string]$Json = 'qa-receipt.json',
  [string]$To = '',
  [string]$Pdf = '',
  [switch]$NoSend
)
$scriptDir = $PSScriptRoot
$python = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'py' }
$argsList = @('-j', (Join-Path (Get-Location) $Json))
if ($To) { $argsList += @('--to', $To) }
if ($Pdf) { $argsList += @('--pdf', $Pdf) }
if ($NoSend) { $argsList += '--no-send' }
& $python (Join-Path $scriptDir 'send_receipt.py') @argsList
if ($LASTEXITCODE -ne 0) { Write-Host '发送失败，请检查 qa-receipt.json 与 .smtp.env 配置。' -ForegroundColor Red; exit $LASTEXITCODE }
