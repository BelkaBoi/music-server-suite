# Rotate the FlClash VPN group to the next node (used by the tunnel health watchdog).
# Prints the node it switched to.
$ErrorActionPreference = "Stop"
$controller = "http://127.0.0.1:9090"
$grp = Invoke-RestMethod -Uri "$controller/proxies/VPN" -TimeoutSec 8
$nodes = @($grp.all) | Where-Object { $_ -notmatch "Все операторы" }
if ($nodes.Count -eq 0) { $nodes = @($grp.all) }
$current = $grp.now
$idx = [array]::IndexOf($nodes, $current)
$next = $nodes[($idx + 1) % $nodes.Count]
Invoke-RestMethod -Uri "$controller/proxies/VPN" -Method Put -Body (@{ name = $next } | ConvertTo-Json) -ContentType "application/json" -TimeoutSec 8
Write-Output "rotated: $current -> $next"
