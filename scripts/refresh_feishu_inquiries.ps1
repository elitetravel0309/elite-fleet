# refresh_feishu_inquiries.ps1
# 拉取飞书询盘工作台「询盘记录」表全部记录 → data/feishu-inquiries.json
# 然后在工作台「智能客服 / 询价管理」点「导入飞书询盘」选择该文件合并。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\refresh_feishu_inquiries.ps1
param(
  [string]$BaseToken = "Dfd8bANo9ar33Ns03Iicrxnp6Ec",
  [string]$TableId   = "tblDuAfoxxYJKZ7Z"
)
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outPath   = Join-Path $scriptDir '..\data\feishu-inquiries.json'

$records = New-Object System.Collections.ArrayList
$offset  = 0

do {
  $r = lark-cli base +record-list --base-token $BaseToken --table-id $TableId --limit 200 --offset $offset --format json --as user 2>&1 | Out-String
  $j = $r | ConvertFrom-Json
  if (-not $j.ok) { throw "lark-cli 返回错误：$r" }

  $fields = $j.data.fields
  $ids    = $j.data.record_id_list
  $rows   = $j.data.data

  for ($i = 0; $i -lt $rows.Count; $i++) {
    $row = $rows[$i]
    $obj = @{ recordId = [string]$ids[$i]; fields = @{} }
    for ($c = 0; $c -lt $fields.Count; $c++) {
      $v = $row[$c]
      if ($null -ne $v -and "$v" -ne '') { $obj.fields[[string]$fields[$c]] = $v }
    }
    [void]$records.Add($obj)
  }

  $offset  += $rows.Count
  $hasMore = $j.data.has_more
  Write-Output ("已读取 {0} 条…" -f $records.Count)
} while ($hasMore -eq $true)

$payload = @{
  fetchedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  count     = $records.Count
  records   = $records
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $outPath -Encoding UTF8
Write-Output ("完成：共 {0} 条询盘 → {1}" -f $records.Count, $outPath)
