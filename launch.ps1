param([ValidateSet('gui','observe','preview','run','check')][string]$Mode = 'gui')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$bk2Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $bk2Python)) {
    throw 'Python environment missing. Run: py -3.12 -m venv .venv; .venv\Scripts\python.exe -m pip install -r requirements.txt'
}
& $bk2Python -X utf8 -m bk2 $Mode
exit $LASTEXITCODE
