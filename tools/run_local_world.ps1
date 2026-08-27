[CmdletBinding()]
param(
    [string]$WorldPath = $env:AMP_WORLD_PATH,
    [string]$MinecraftVersion = $(if ($env:AMP_MC_VERSION) { $env:AMP_MC_VERSION } else { "26.2" }),
    [int]$Port = $(if ($env:AMP_SERVER_PORT) { [int]$env:AMP_SERVER_PORT } else { 25565 }),
    [string]$Username = $(if ($env:AMP_BOT_USERNAME) { $env:AMP_BOT_USERNAME } else { "AMP" }),
    [string]$GameMode = $(if ($env:AMP_GAME_MODE) { $env:AMP_GAME_MODE } else { "survival" }),
    [string]$JavaPath = $env:AMP_JAVA_PATH,
    [string]$PythonPath = $(if ($env:AMP_PYTHON_PATH) { $env:AMP_PYTHON_PATH } else { "python" }),
    [switch]$AcceptEula,
    [switch]$RefreshWorldCopy
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not $WorldPath) {
    throw "Supply -WorldPath or set AMP_WORLD_PATH to a Minecraft Java save directory."
}
if (-not $AcceptEula) {
    throw "Read https://aka.ms/MinecraftEULA, then rerun with -AcceptEula if you agree."
}
$sourceWorld = (Resolve-Path -LiteralPath $WorldPath).Path
if (-not (Test-Path -LiteralPath (Join-Path $sourceWorld "level.dat"))) {
    throw "The selected directory is not a Minecraft world: $sourceWorld"
}

if (-not $JavaPath) {
    $javaCommand = Get-Command java -ErrorAction SilentlyContinue
    if (-not $javaCommand) {
        throw "Java was not found. Set AMP_JAVA_PATH to java.exe from a compatible JDK."
    }
    $JavaPath = $javaCommand.Source
}
if (-not (Test-Path -LiteralPath $JavaPath)) {
    $javaCommand = Get-Command $JavaPath -ErrorAction SilentlyContinue
    if (-not $javaCommand) { throw "Java executable not found: $JavaPath" }
    $JavaPath = $javaCommand.Source
}

$runRoot = Join-Path $repoRoot ".tmp\local-world-$MinecraftVersion"
$serverWorld = Join-Path $runRoot "world"
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

if ($RefreshWorldCopy -and (Test-Path -LiteralPath $serverWorld)) {
    $resolvedRunRoot = (Resolve-Path -LiteralPath $runRoot).Path
    $resolvedServerWorld = (Resolve-Path -LiteralPath $serverWorld).Path
    if (-not $resolvedServerWorld.StartsWith($resolvedRunRoot + [IO.Path]::DirectorySeparatorChar)) {
        throw "Refusing to remove a world outside the local run directory."
    }
    Remove-Item -LiteralPath $resolvedServerWorld -Recurse -Force
}
if (-not (Test-Path -LiteralPath $serverWorld)) {
    Write-Host "Copying the world. The original save will not be modified..."
    Copy-Item -LiteralPath $sourceWorld -Destination $serverWorld -Recurse
} else {
    Write-Host "Reusing server world copy at $serverWorld"
}

$serverJar = Join-Path $runRoot "server.jar"
if (-not (Test-Path -LiteralPath $serverJar)) {
    Write-Host "Downloading the official Minecraft $MinecraftVersion server..."
    $manifest = Invoke-RestMethod "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
    $entry = $manifest.versions | Where-Object id -EQ $MinecraftVersion | Select-Object -First 1
    if (-not $entry) { throw "Minecraft version $MinecraftVersion was not found in Mojang's manifest." }
    $versionManifest = Invoke-RestMethod $entry.url
    if (-not $versionManifest.downloads.server.url) {
        throw "Mojang does not publish a server JAR for Minecraft $MinecraftVersion."
    }
    Invoke-WebRequest $versionManifest.downloads.server.url -OutFile $serverJar
    $actualHash = (Get-FileHash -LiteralPath $serverJar -Algorithm SHA1).Hash.ToLowerInvariant()
    if ($actualHash -ne $versionManifest.downloads.server.sha1.ToLowerInvariant()) {
        Remove-Item -LiteralPath $serverJar -Force
        throw "The downloaded server JAR failed Mojang's SHA-1 integrity check."
    }
}

Set-Content -LiteralPath (Join-Path $runRoot "eula.txt") -Value "eula=true" -Encoding ascii
$properties = @(
    "server-port=$Port"
    "server-ip=127.0.0.1"
    "level-name=world"
    "online-mode=false"
    "enforce-secure-profile=false"
    "motd=AMP local world"
    "spawn-protection=0"
)
Set-Content -LiteralPath (Join-Path $runRoot "server.properties") -Value $properties -Encoding ascii

$stdout = Join-Path $runRoot "server-console.log"
$stderr = Join-Path $runRoot "server-error.log"
$server = $null
try {
    if (Test-NetConnection -ComputerName 127.0.0.1 -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue) {
        throw "Port $Port is already in use. Stop that server or select another port."
    }
    Write-Host "Starting the local server on port $Port..."
    $server = Start-Process -FilePath $JavaPath -ArgumentList "-Xms1G", "-Xmx2G", "-jar", "server.jar", "nogui" -WorkingDirectory $runRoot -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddMinutes(3)
    do {
        if ($server.HasExited) {
            throw "The Minecraft server exited during startup. See $stdout and $stderr"
        }
        $ready = Test-NetConnection -ComputerName 127.0.0.1 -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue
        if (-not $ready) { Start-Sleep -Seconds 2 }
    } until ($ready -or (Get-Date) -ge $deadline)
    if (-not $ready) { throw "The server did not open port $Port within 3 minutes." }

    Write-Host "Starting $Username. Join localhost:$Port with Minecraft $MinecraftVersion."
    & $PythonPath (Join-Path $PSScriptRoot "hold_bot.py") --host 127.0.0.1 --port $Port --username $Username --version $MinecraftVersion --game-mode $GameMode
    if ($LASTEXITCODE -ne 0) { throw "AMP exited with status $LASTEXITCODE" }
} finally {
    if ($server -and -not $server.HasExited) {
        Write-Host "Stopping the local Minecraft server..."
        Stop-Process -Id $server.Id
        $server.WaitForExit(10000) | Out-Null
    }
}
