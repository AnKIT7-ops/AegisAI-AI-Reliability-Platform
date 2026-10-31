# Uploads every document in data/sample_docs through nginx.
# Works with sign-in off only: it does not hold a session.
# Usage: .\scripts\ingest_samples.ps1 [-Api http://127.0.0.1:8080]
param([string]$Api = "http://127.0.0.1:8080")

$docs = Get-ChildItem (Join-Path $PSScriptRoot "..\data\sample_docs") -Filter *.md |
    Where-Object Name -ne "ATTRIBUTION.md"
$failed = 0
foreach ($f in $docs) {
    $code = curl.exe -s --connect-timeout 5 --max-time 60 -o NUL -w "%{http_code}" -F "file=@$($f.FullName)" "$Api/api/v1/documents/ingest"
    # 201 = ingested, 200 = already present
    "{0}  {1}" -f $code, $f.Name
    if ($code -notin "200", "201") { $failed++ }
}
if ($failed) { Write-Error "$failed of $($docs.Count) uploads failed"; exit 1 }
