# push_feishu_status.ps1
# 把工作台导出的状态回写飞书询盘工作台。
# 前置：在工作台「智能客服 / 询价管理」点「导出回写」→ 得到 data/feishu-status-updates.json（或任意位置该文件）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\push_feishu_status.ps1 [-Path 文件路径]
# 回写字段：状态 / 报价金额（>0 时）/ 下次跟进日期（非空时）。备注不回写（避免覆盖飞书侧人工备注）。
param(
  [string]$Path = "",
  [string]$BaseToken = "Dfd8bANo9ar33Ns03Iicrxnp6Ec",
  [string]$TableId   = "tblDuAfoxxYJKZ7Z"
)
$ErrorActionPreference = 'Stop'

if (-not $Path) {
  $Path = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..\data\feishu-status-updates.json'
}
if (-not (Test-Path $Path)) { throw "找不到回写文件：$Path（请先在两个工作台导出）" }

$d = Get-Content $Path -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $d.items -or $d.items.Count -eq 0) { Write-Output '回写文件为空（items 为 0 条）'; exit 0 }

$statusMap = @{ 'new'='新询盘'; 'quoted'='已报价'; 'following'='已报价'; 'won'='已确认'; 'lost'='已取消' }
$ok = 0; $skip = 0; $fail = 0

foreach ($it in $d.items) {
  $rid = [string]$it.recordId
  if (-not $rid) { $skip++; continue }
  $st = $statusMap[[string]$it.status]
  if (-not $st) { Write-Output ("跳过 {0}：未知状态 {1}" -f $rid, $it.status); $skip++; continue }

  $patch = @{ '状态' = $st }
  $q = 0.0
  if ($null -ne $it.quote) { [double]$q = $it.quote }
  if ($q -gt 0) { $patch['报价金额'] = $q }
  $fu = [string]$it.followUp
  if ($fu) { $patch['下次跟进日期'] = $fu + ' 00:00:00' }

  $json = $patch | ConvertTo-Json -Compress
  $out = lark-cli base +record-upsert --base-token $BaseToken --table-id $TableId --record-id $rid --json $json --as user 2>&1 | Out-String
  $res = $null
  try { $res = $out | ConvertFrom-Json } catch {}
  if ($res -and $res.ok) {
    $ok++
    Write-Output ("✓ {0} → {1}" -f $rid, $st)
  } else {
    $fail++
    Write-Output ("✗ {0} → {1}" -f $rid, $out)
  }
}

Write-Output ("完成：成功 {0}，跳过 {1}，失败 {2}" -f $ok, $skip, $fail)
if ($fail -gt 0) { exit 1 }
