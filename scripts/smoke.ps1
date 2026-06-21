# Smoke-test the running service (default http://localhost:15487).
# Usage: .\scripts\smoke.ps1 [-BaseUrl http://localhost:15487] [-Secret ""] [-Query "nezha deity"]
param(
    [string]$BaseUrl = "http://localhost:15487",
    [string]$Secret = "",
    [string]$Query = "Nezha Investiture of the Gods deity"
)

Write-Host "GET $BaseUrl/health" -ForegroundColor Cyan
Invoke-RestMethod -Uri "$BaseUrl/health" | ConvertTo-Json -Depth 5

$headers = @{ "Content-Type" = "application/json" }
if ($Secret -ne "") { $headers["Authorization"] = "Bearer $Secret" }

$body = @{
    query         = $Query
    max_results   = 5
    search_depth  = "basic"
    include_answer = $true
} | ConvertTo-Json

Write-Host "`nPOST $BaseUrl/search" -ForegroundColor Cyan
$resp = Invoke-RestMethod -Uri "$BaseUrl/search" -Method Post -Headers $headers -Body $body
$resp | ConvertTo-Json -Depth 6

Write-Host "`nResults: $($resp.results.Count)" -ForegroundColor Green
