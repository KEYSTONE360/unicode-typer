$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m venv .venv-build
if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed.' }
$buildPython = Join-Path $PSScriptRoot '.venv-build\Scripts\python.exe'
& $buildPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed.' }
& $buildPython -m PyInstaller --noconfirm --clean --onefile --windowed --name UnicodeTyper typer.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
Write-Host 'Built: dist\UnicodeTyper.exe'
