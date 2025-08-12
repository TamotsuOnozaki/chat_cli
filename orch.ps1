param(
  [Parameter(Mandatory=$true)][string]$InputText,
  [string]$Roles = "idea_ai,writer_ai,proof_ai"
)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
if (-not (Test-Path ".\.venv\Scripts\python.exe")) { Write-Error "venv python not found"; exit 1 }
. .\.venv\Scripts\Activate.ps1
python .\multi_agent_orchestrator.py -i $InputText -r $Roles
