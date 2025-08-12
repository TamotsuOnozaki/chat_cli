param([int]$Port=8084)
$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot

if(-not (Test-Path .venv\Scripts\python.exe)){
  python -m venv .venv
}
. .\.venv\Scripts\Activate.ps1
python -m pip install -U pip > $null
pip install -r requirements.txt
$env:PYTHONPATH = "$PSScriptRoot\backend"

function Test-PortBindable {
  param([int]$p)
  try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $p)
    $listener.Start()
    $listener.Stop()
    return $true
  } catch {
    return $false
  }
}

# 実際にバインド可能かでチェックし、空きポートを探索
$orig = $Port
$maxTries = 20
$found = $false
for($i=0; $i -lt $maxTries; $i++){
  if(Test-PortBindable -p $Port){
    $found = $true
    break
  }
  $Port++
}
if(-not $found){
  Write-Error "No bindable port found starting from $orig (tried $maxTries ports)."
  Pop-Location
  exit 1
}
if($Port -ne $orig){ Write-Host "Port $orig is busy. Using $Port" }

Start-Process "http://localhost:$Port" | Out-Null
Write-Host "Starting uvicorn on port $Port..."
uvicorn app:app --app-dir backend --host 0.0.0.0 --port $Port
Pop-Location
