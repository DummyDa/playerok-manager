param([string]$Server = "144.31.158.11", [string]$SshUser = "root")
$ErrorActionPreference = "Stop"
$projectFolder = Split-Path -Parent $PSScriptRoot
$keyFile = Join-Path $env:USERPROFILE ".ssh\id_ed25519_playerok_vps"
if (-not (Test-Path -LiteralPath $keyFile)) { throw "SSH key not found: $keyFile" }
$pidFile = Join-Path $projectFolder "data\deploy\dashboard-tunnel.pid"
$listener = Get-NetTCPConnection -LocalPort 3100 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $expectedPid = if (Test-Path -LiteralPath $pidFile) { [int](Get-Content -LiteralPath $pidFile) } else { 0 }
    if (-not $expectedPid -or ($listener | Where-Object { $_.OwningProcess -ne $expectedPid })) {
        throw "Port 3100 is occupied. Stop the local project before opening the VPS dashboard."
    }
} else {
    New-Item -ItemType Directory -Path (Split-Path -Parent $pidFile) -Force | Out-Null
    $sshProcess = Start-Process -FilePath "ssh.exe" -ArgumentList @("-i", "`"$keyFile`"", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", "-L", "127.0.0.1:3100:127.0.0.1:3100", "$SshUser@$Server") -WindowStyle Hidden -PassThru
    $sshProcess.Id | Set-Content -LiteralPath $pidFile
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ($sshProcess.HasExited) { throw "SSH tunnel failed. Check the SSH connection to the VPS." }
        if (Get-NetTCPConnection -LocalPort 3100 -State Listen -ErrorAction SilentlyContinue) { break }
        Start-Sleep -Milliseconds 300
    }
}
Start-Process "http://127.0.0.1:3100"
