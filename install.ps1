# Installs Polymath on Windows, keeps it running, and opens it in your browser.
#
#   irm https://raw.githubusercontent.com/ipowerai/polymath/main/install.ps1 | iex
#
# To use another port, first run:  $env:POLYMATH_PORT = 9000
# From a downloaded copy:  powershell -ExecutionPolicy Bypass -File install.ps1 [-Port 9000] [-Uninstall]
#
# It needs Python 3.9 or later and a working AI model. It checks first: Claude Code signed in with an active
# subscription, a running local model, or an accepted API key. If none passes, nothing is installed.
# It writes only to your own account: no administrator rights are needed.

function Install-Polymath {
    param([object[]]$Options)
    $ErrorActionPreference = 'Stop'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    $ZipUrl = 'https://github.com/ipowerai/polymath/releases/latest/download/polymath.zip'
    $Parts = 'blueprints', 'references', 'scripts', 'skills', 'web', '.claude-plugin', 'LICENSE', 'NOTICE', 'README.md', 'install.sh', 'install.ps1'
    $Root = Join-Path $env:LOCALAPPDATA 'Polymath'
    $App = Join-Path $Root 'app'
    $Engine = Join-Path $App 'scripts\polymath.py'
    $PortFile = Join-Path $Root 'port'
    $BinDir = Join-Path $Root 'bin'
    $Control = Join-Path $App 'scripts\polymath_control.py'
    $StartupFile = Join-Path ([Environment]::GetFolderPath('Startup')) 'Polymath.cmd'
    $Utf8 = New-Object Text.UTF8Encoding($false)

    $Port = 0; $Remove = $false; $Service = $true; $OpenIt = $true; $Llm = $env:POLYMATH_LLM
    if ($env:POLYMATH_PORT) { $Port = [int]$env:POLYMATH_PORT }
    for ($i = 0; $i -lt $Options.Count; $i++) {
        switch -Regex ([string]$Options[$i]) {
            '^--?port$' { $i++; $Port = [int]$Options[$i] }
            '^--?llm$' { $i++; $Llm = [string]$Options[$i] }
            '^--?uninstall$' { $Remove = $true }
            '^--?no-?service$' { $Service = $false }
            '^--?no-?open$' { $OpenIt = $false }
            default { throw "Unknown option $($Options[$i]). Options: -Port N, -Llm MODEL, -Uninstall, -NoService, -NoOpen." }
        }
    }

    function Stop-Running {
        Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine.Contains($Engine) } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 500
    }

    function Test-PortBusy([int]$Number) {
        $client = New-Object Net.Sockets.TcpClient
        try {
            $attempt = $client.BeginConnect('127.0.0.1', $Number, $null, $null)
            if ($attempt.AsyncWaitHandle.WaitOne(300)) { $client.EndConnect($attempt); return $true }
            return $false
        } catch { return $false } finally { $client.Close() }
    }

    function Find-Python {
        foreach ($try in @(@('py', '-3'), @('python'), @('python3'))) {
            $exe = $try[0]
            $rest = @($try | Select-Object -Skip 1)
            if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
            try {
                $out = & $exe @rest -c "import sys; print(sys.executable if sys.version_info >= (3, 9) else '')" 2>$null
            } catch { continue }
            $path = [string]($out | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and $path.Trim() -and (Test-Path -LiteralPath $path.Trim())) { return $path.Trim() }
        }
        return $null
    }

    if ($Remove) {
        Stop-Running
        Remove-Item -LiteralPath $StartupFile -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $App -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $PortFile, (Join-Path $Root 'Polymath-startup.off') -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $BinDir -Recurse -Force -ErrorAction SilentlyContinue
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if ($userPath) { [Environment]::SetEnvironmentVariable('Path', (($userPath -split ';' | Where-Object { $_ -and $_ -ne $BinDir }) -join ';'), 'User') }
        if ((Test-Path -LiteralPath $Root) -and -not (Get-ChildItem -LiteralPath $Root -Force)) { Remove-Item -LiteralPath $Root }
        Write-Host "Polymath is removed. Your study data is still in $(Join-Path $HOME '.polymath')."
        return
    }

    $Python = Find-Python
    if (-not $Python) { throw 'Python 3.9 or later is needed. Get it from https://www.python.org/downloads/ and run this again.' }
    $Windowless = Join-Path (Split-Path $Python) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $Windowless)) { $Windowless = $Python }

    $env:PYTHONUTF8 = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $claudeHome = Join-Path $HOME '.local\bin'
    if ((Test-Path -LiteralPath $claudeHome) -and -not (($env:Path -split ';') -contains $claudeHome)) { $env:Path = "$claudeHome;$env:Path" }
    $Work = Join-Path ([IO.Path]::GetTempPath()) ("polymath-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Work | Out-Null
    $Choice = Join-Path $Work 'choice.json'
    $Source = $null
    if ($PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'scripts\polymath.py'))) { $Source = $PSScriptRoot }
    try {
        if (-not $Source) {
            Write-Host 'Downloading Polymath...'
            Invoke-WebRequest -Uri $ZipUrl -OutFile (Join-Path $Work 'polymath.zip') -UseBasicParsing
            Expand-Archive -LiteralPath (Join-Path $Work 'polymath.zip') -DestinationPath (Join-Path $Work 'unpacked')
            $Source = Join-Path $Work 'unpacked\polymath'
            if (-not (Test-Path -LiteralPath (Join-Path $Source 'scripts\polymath.py'))) { throw 'The download was incomplete.' }
        }

        # Stops here, with nothing installed or changed, unless a model passes and you confirm it.
        $checkArgs = @((Join-Path $Source 'scripts\polymath_setup.py'), 'check', '--out', $Choice)
        if ($Llm) { $checkArgs += @('--llm', $Llm) }
        & $Python @checkArgs
        if ($LASTEXITCODE -ne 0) { throw 'No AI model was confirmed.' }

        Stop-Running
        New-Item -ItemType Directory -Force -Path $Root | Out-Null
        $New = "$App.new"
        if (Test-Path -LiteralPath $New) { Remove-Item -LiteralPath $New -Recurse -Force }
        New-Item -ItemType Directory -Path $New | Out-Null
        foreach ($part in $Parts) {
            $from = Join-Path $Source $part
            if (-not (Test-Path -LiteralPath $from)) { throw "$part is missing from the download." }
            Copy-Item -LiteralPath $from -Destination $New -Recurse -Force
        }
        Get-ChildItem -LiteralPath $New -Recurse -Force -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
        if (Test-Path -LiteralPath $App) { Remove-Item -LiteralPath $App -Recurse -Force }
        Move-Item -LiteralPath $New -Destination $App
        & $Python (Join-Path $App 'scripts\polymath_setup.py') save --from $Choice
        if ($LASTEXITCODE -ne 0) { throw 'Your default model could not be saved.' }
    } finally {
        if ($Work) { Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue }
    }

    if ($Port) {
        if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Choose a port from 1024 to 65535.' }
        if (Test-PortBusy $Port) { throw "Port $Port is already in use. Choose another." }
    } else {
        $candidates = @()
        if (Test-Path -LiteralPath $PortFile) { $candidates += [int](Get-Content -LiteralPath $PortFile -Raw) }
        $candidates += 8765..8785
        foreach ($candidate in $candidates) { if (-not (Test-PortBusy $candidate)) { $Port = $candidate; break } }
        if (-not $Port) { throw 'Ports 8765 to 8785 are all in use. Choose one with -Port.' }
    }
    [IO.File]::WriteAllText($PortFile, [string]$Port, $Utf8)
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    [IO.File]::WriteAllText((Join-Path $BinDir 'polymath.cmd'), "@echo off`r`nrem Polymath terminal command`r`nchcp 65001 >nul`r`nset PYTHONUTF8=1`r`n`"$Python`" `"$Control`" %*`r`n", $Utf8)
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not (($userPath -split ';') -contains $BinDir)) { [Environment]::SetEnvironmentVariable('Path', (@($userPath, $BinDir) -ne '' -join ';'), 'User') }
    $env:Path = "$BinDir;$env:Path"
    if ($Service) {
        [IO.File]::WriteAllText($StartupFile, "@echo off`r`nchcp 65001 >nul`r`nset PYTHONUTF8=1`r`ncd /d `"$App`"`r`nstart `"`" `"$Windowless`" `"$Engine`" serve --port $Port`r`n", $Utf8)
    }

    Start-Process -FilePath $Windowless -ArgumentList "`"$Engine`" serve --port $Port" -WorkingDirectory $App -WindowStyle Hidden
    $up = $false
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-PortBusy $Port) { $up = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $up) { throw "It did not start. To see why, run: `"$Python`" `"$Engine`" serve --port $Port" }

    Write-Host ''
    Write-Host "Polymath is running at http://127.0.0.1:$Port"
    if ($Service) { Write-Host 'It starts by itself when you log in.' }
    Write-Host "Your study data stays on this computer, in $(Join-Path $HOME '.polymath')."
    Write-Host ''
    Write-Host 'Control it from a new terminal:'
    Write-Host '  polymath status | start | stop | restart | open'
    Write-Host ''
    Write-Host "Remove it:      powershell -ExecutionPolicy Bypass -File `"$App\install.ps1`" -Uninstall"
    if ($OpenIt) { & $Python $Control open *> $null }
}

# Everything runs from here, so a download cut off halfway does nothing.
try {
    Install-Polymath -Options @($args)
} catch {
    Write-Host "Polymath could not be installed: $($_.Exception.Message)" -ForegroundColor Red
    if ($PSCommandPath) { exit 1 }
}
