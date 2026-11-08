<#
.SYNOPSIS
    Gera os wheels do api-quality-orchestrator (+ dependencias) e os coloca
    em extension/resources/python-dist/, prontos para o .vsix.

.DESCRIPTION
    Passo MANUAL de preparacao de release da extensao -- NUNCA roda em
    runtime (a extensao, quando ativada num computador de usuario final,
    so INSTALA os wheels que ja estao aqui; nunca faz build nem baixa nada
    da internet). Rode isso sempre que:
      - a versao do api-quality-orchestrator (pyproject.toml) mudar, ou
      - as dependencias de runtime (PyYAML/openpyxl) mudarem, ou
      - a versao/plataforma do Python padrao da(s) maquina(s) alvo mudar.

    Os valores de --python-version/--implementation/--abi/--platform abaixo
    precisam casar com o interpretador que extension/src/pythonEnv/
    interpreterResolver.ts efetivamente vai encontrar no sistema do
    usuario. Hoje (validado no ambiente de desenvolvimento): Python 3.13.3
    / Windows 11 x64 -> cp313 / win_amd64. Revisar se isso mudar.

.EXAMPLE
    cd extension
    ./scripts/prepare-release.ps1
#>

$ErrorActionPreference = 'Stop'

# Pins da plataforma/versao alvo -- revisar conforme o comentario acima.
$PythonVersion = '3.13'
$Implementation = 'cp'
$Abi = 'cp313'
$Platform = 'win_amd64'

$ExtensionRoot = Split-Path -Parent $PSScriptRoot
$RepoRoot = Split-Path -Parent $ExtensionRoot
$DistDir = Join-Path $RepoRoot 'dist'
$PythonDistDir = Join-Path $ExtensionRoot 'resources\python-dist'
$PyprojectPath = Join-Path $RepoRoot 'pyproject.toml'

Write-Host '== API Quality Orchestrator - preparar release da extensao ==' -ForegroundColor Cyan

if (-not (Test-Path $PyprojectPath)) {
    throw "pyproject.toml nao encontrado em '$RepoRoot' -- rode este script a partir de extension/scripts/ dentro do repositorio correto."
}
if (-not (Test-Path $ExtensionRoot)) {
    throw "Pasta extension/ nao encontrada em '$ExtensionRoot'."
}

# 1) Descobrir a versao atual do pacote a partir do pyproject.toml (nunca
#    hardcoded aqui, pra nao desincronizar de proposito com um bump real).
$pyprojectContent = Get-Content $PyprojectPath -Raw
if ($pyprojectContent -notmatch '(?m)^version\s*=\s*"([^"]+)"') {
    throw 'Nao foi possivel localizar a linha version = "..." em pyproject.toml.'
}
$PackageVersion = $Matches[1]
Write-Host "Versao detectada: $PackageVersion"

# 2) Limpar artefatos de uma execucao anterior (mantem a pasta via .gitkeep).
Write-Host 'Limpando wheels e manifest.json anteriores...'
if (Test-Path $PythonDistDir) {
    Get-ChildItem $PythonDistDir -Filter '*.whl' -ErrorAction SilentlyContinue | Remove-Item -Force
    $oldManifest = Join-Path $PythonDistDir 'manifest.json'
    if (Test-Path $oldManifest) { Remove-Item $oldManifest -Force }
} else {
    New-Item -ItemType Directory -Path $PythonDistDir -Force | Out-Null
}
if (Test-Path $DistDir) {
    Remove-Item $DistDir -Recurse -Force
}

# 3) Garantir que o pacote 'build' esta instalado no interpretador atual.
#    Unico passo deste script que legitimamente toca a rede -- feito uma
#    vez, manualmente, pelo desenvolvedor (nunca em runtime da extensao).
python -m pip show build | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Pacote 'build' nao encontrado -- instalando (python -m pip install --upgrade build)..." -ForegroundColor Yellow
    python -m pip install --upgrade build
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar o pacote 'build'." }
}

# 4) Build do wheel do proprio projeto, a partir da raiz do repo.
Write-Host 'Gerando wheel do api-quality-orchestrator (python -m build --wheel)...'
Push-Location $RepoRoot
try {
    python -m build --wheel --outdir dist
    if ($LASTEXITCODE -ne 0) { throw "python -m build falhou (exit code $LASTEXITCODE)." }
} finally {
    Pop-Location
}

$builtWheel = Get-ChildItem $DistDir -Filter '*.whl' -ErrorAction SilentlyContinue
if (-not $builtWheel) {
    throw "Nenhum .whl encontrado em '$DistDir' apos o build -- algo falhou silenciosamente."
}
Write-Host "Wheel do projeto gerado: $($builtWheel.Name)"

# 5) Baixar o fechamento de dependencias (PyYAML, openpyxl, et_xmlfile...)
#    numa unica resolucao consistente, casada com a plataforma/versao alvo,
#    usando o wheel recem-buildado como fonte adicional via --find-links.
Write-Host "Baixando dependencias para $Implementation-$Abi / $Platform (Python $PythonVersion)..."
python -m pip download "api-quality-orchestrator==$PackageVersion" `
    --find-links $DistDir `
    --only-binary=:all: `
    --python-version $PythonVersion `
    --implementation $Implementation `
    --abi $Abi `
    --platform $Platform `
    --dest $PythonDistDir
if ($LASTEXITCODE -ne 0) {
    throw "pip download falhou (exit code $LASTEXITCODE) -- verifique se existe wheel binario pra essa combinacao de versao/plataforma pra TODAS as dependencias (PyYAML costuma publicar wheels por versao de CPython; se a versao do Python mudou recentemente, pode nao haver wheel ainda)."
}

# 6) Nunca aceitar sdist (.tar.gz) -- instalacao offline (--no-index) no
#    computador do usuario final nao tem compilador disponivel.
$sdist = Get-ChildItem $PythonDistDir -Filter '*.tar.gz' -ErrorAction SilentlyContinue
if ($sdist) {
    $sdistNames = ($sdist.Name -join ', ')
    throw "Encontrado(s) sdist (.tar.gz) em '$PythonDistDir': $sdistNames. Isso indica ausencia de wheel binario pra essa combinacao de versao/plataforma -- a instalacao offline no usuario final quebraria. Ajuste os pins de plataforma/versao no topo deste script ou investigue a dependencia especifica."
}

$wheels = Get-ChildItem $PythonDistDir -Filter '*.whl' | Sort-Object Name
if ($wheels.Count -eq 0) {
    throw "Nenhum wheel encontrado em '$PythonDistDir' apos o download -- algo falhou silenciosamente."
}

# 7) Gerar manifest.json -- pequeno, versionado em git, e o que o
#    bootstrap da extensao (venvManager.ts) usa para decidir se precisa
#    reinstalar (comparacao de versao/lista de wheels).
$manifest = [ordered]@{
    packageVersion = $PackageVersion
    pythonTag      = "$Abi-$Platform"
    wheelFileNames = @($wheels.Name)
    generatedAt    = (Get-Date).ToUniversalTime().ToString('o')
}
$manifestPath = Join-Path $PythonDistDir 'manifest.json'
$manifestJson = $manifest | ConvertTo-Json -Depth 5
# Set-Content/Out-File -Encoding utf8 no Windows PowerShell 5.1 sempre
# escreve um BOM (marca de byte-order) no inicio do arquivo -- e o
# fs.readFileSync(..., "utf8") do Node NAO remove isso sozinho, entao
# JSON.parse quebrava ao ler esse manifest.json (bug real, reproduzido).
# .NET's UTF8Encoding($false) escreve UTF-8 sem BOM.
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($manifestPath, $manifestJson, $utf8NoBom)

Write-Host ''
Write-Host "== Wheels gerados em $PythonDistDir ==" -ForegroundColor Green
foreach ($w in $wheels) {
    $sizeKb = [math]::Round($w.Length / 1KB, 1)
    Write-Host ("  {0,-45} {1,8} KB" -f $w.Name, $sizeKb)
}
Write-Host ''
Write-Host 'manifest.json:'
Get-Content $manifestPath | Write-Host
Write-Host ''
Write-Host 'Proximo passo (dentro de extension/):' -ForegroundColor Cyan
Write-Host '  npm install; npm run compile; npx @vscode/vsce ls; npx @vscode/vsce package --allow-missing-repository'
