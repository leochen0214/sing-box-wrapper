# install.ps1 - sing-box + sb 一键安装脚本 (Windows)
#
# 用法:
#   irm https://raw.githubusercontent.com/leochen0214/sing-box-wrapper/main/install.ps1 | iex
#   .\install.ps1

$ErrorActionPreference = "Stop"
$RepoUrl = "https://github.com/leochen0214/sing-box-wrapper"
$InstallDir = Join-Path $HOME ".config\sing-box"

Write-Host "=== sing-box + sb 安装 ===" -ForegroundColor Cyan
Write-Host "安装目录: $InstallDir"
Write-Host ""

# --- 1. 创建安装目录 ---
if (-not (Test-Path $InstallDir)) {
    New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null
    Write-Host "[1/5] 创建目录 $InstallDir"
} else {
    Write-Host "[1/5] 目录已存在"
}

# --- 2. 下载 sing-box ---
Write-Host "[2/5] 检查 sing-box..."
$sbExe = Join-Path $InstallDir "sing-box.exe"
$needDownload = $true

if (Test-Path $sbExe) {
    $currentVer = & $sbExe version 2>$null | Select-String "sing-box version" | ForEach-Object { ($_ -split " ")[2] }
    Write-Host "  当前版本: $currentVer"
}

# 获取最新版本
try {
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/SagerNet/sing-box/releases/latest" -UseBasicParsing
    $latestVer = $release.tag_name -replace '^v', ''
    Write-Host "  最新版本: $latestVer"

    if ($currentVer -eq $latestVer) {
        Write-Host "  已是最新，跳过" -ForegroundColor Green
        $needDownload = $false
    }
} catch {
    Write-Host "  Warning: 无法获取最新版本信息" -ForegroundColor Yellow
    if (Test-Path $sbExe) { $needDownload = $false }
}

if ($needDownload) {
    $asset = $release.assets | Where-Object { $_.name -match "windows-amd64\.zip$" -and $_.name -notmatch "legacy" } | Select-Object -First 1
    if (-not $asset) {
        Write-Host "  Error: 未找到 windows-amd64 下载包" -ForegroundColor Red
        exit 1
    }
    $zipFile = Join-Path $env:TEMP "sing-box.zip"
    Write-Host "  下载 $($asset.name)..."
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipFile -UseBasicParsing
    # 解压（sing-box.exe 在子目录中）
    $extractDir = Join-Path $env:TEMP "sing-box-extract"
    if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
    Expand-Archive -Path $zipFile -DestinationPath $extractDir -Force
    $exeFile = Get-ChildItem -Path $extractDir -Filter "sing-box.exe" -Recurse | Select-Object -First 1
    if (-not $exeFile) {
        Write-Host "  Error: ZIP 中未找到 sing-box.exe" -ForegroundColor Red
        exit 1
    }
    Copy-Item $exeFile.FullName -Destination $sbExe -Force
    Remove-Item $zipFile -Force -ErrorAction SilentlyContinue
    Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    $ver = & $sbExe version 2>$null | Select-String "sing-box version" | ForEach-Object { ($_ -split " ")[2] }
    Write-Host "  已安装 sing-box $ver" -ForegroundColor Green
}

# --- 3. 下载 sb 脚本 ---
Write-Host "[3/5] 下载 sb 脚本..."
$hasGit = $null -ne (Get-Command git -ErrorAction SilentlyContinue)

if ($hasGit) {
    if (Test-Path (Join-Path $InstallDir ".git")) {
        Write-Host "  git pull..."
        Push-Location $InstallDir
        git pull --quiet 2>$null
        Pop-Location
        Write-Host "  已更新" -ForegroundColor Green
    } else {
        # 先备份已有文件
        $backupFiles = @(".env", ".env.*") | ForEach-Object { Get-ChildItem -Path $InstallDir -Filter $_ -ErrorAction SilentlyContinue }
        $tmpBackup = Join-Path $env:TEMP "sb-backup-$(Get-Random)"
        if ($backupFiles) {
            New-Item -Path $tmpBackup -ItemType Directory -Force | Out-Null
            $backupFiles | Copy-Item -Destination $tmpBackup
        }
        # Clone
        Write-Host "  git clone..."
        $tmpClone = Join-Path $env:TEMP "sb-clone-$(Get-Random)"
        git clone --quiet $RepoUrl $tmpClone 2>$null
        # 复制文件（保留 .env 等本地文件）
        Get-ChildItem -Path $tmpClone -Exclude ".git" | Copy-Item -Destination $InstallDir -Recurse -Force
        # 移动 .git 目录
        if (Test-Path (Join-Path $tmpClone ".git")) {
            Copy-Item (Join-Path $tmpClone ".git") -Destination $InstallDir -Recurse -Force
        }
        Remove-Item $tmpClone -Recurse -Force -ErrorAction SilentlyContinue
        # 恢复备份
        if ($backupFiles -and (Test-Path $tmpBackup)) {
            Get-ChildItem $tmpBackup | Copy-Item -Destination $InstallDir -Force
            Remove-Item $tmpBackup -Recurse -Force
        }
        Write-Host "  已克隆" -ForegroundColor Green
    }
} else {
    # 无 git，下载 ZIP
    Write-Host "  未安装 git，下载 ZIP 包..."
    $repoZip = Join-Path $env:TEMP "sb-repo.zip"
    Invoke-WebRequest -Uri "$RepoUrl/archive/refs/heads/main.zip" -OutFile $repoZip -UseBasicParsing
    $extractDir = Join-Path $env:TEMP "sb-repo-extract"
    if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
    Expand-Archive -Path $repoZip -DestinationPath $extractDir -Force
    # ZIP 解压后有一层子目录 sing-box-wrapper-main/
    $srcDir = Get-ChildItem -Path $extractDir -Directory | Select-Object -First 1
    if ($srcDir) {
        # 复制所有文件，不覆盖 .env
        Get-ChildItem -Path $srcDir.FullName | ForEach-Object {
            $dest = Join-Path $InstallDir $_.Name
            if ($_.Name -match '^\.env$' -and (Test-Path $dest)) {
                # 不覆盖用户的 .env
            } else {
                Copy-Item $_.FullName -Destination $dest -Recurse -Force
            }
        }
    }
    Remove-Item $repoZip -Force -ErrorAction SilentlyContinue
    Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "  已下载" -ForegroundColor Green
}

# --- 4. 添加到 PATH ---
Write-Host "[4/5] 配置 PATH..."
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -split ";" | Where-Object { $_ -eq $InstallDir }) {
    Write-Host "  已在 PATH 中" -ForegroundColor Green
} else {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$InstallDir", "User")
    $env:Path += ";$InstallDir"
    Write-Host "  已添加到用户 PATH" -ForegroundColor Green
}

# --- 5. 下载 WinTun（TUN 模式可选）---
Write-Host "[5/5] 检查 WinTun..."
$wintunDll = Join-Path $InstallDir "wintun.dll"
if (Test-Path $wintunDll) {
    Write-Host "  已存在，跳过" -ForegroundColor Green
} else {
    try {
        Write-Host "  下载 wintun.dll..."
        $wintunZip = Join-Path $env:TEMP "wintun.zip"
        Invoke-WebRequest -Uri "https://www.wintun.net/builds/wintun-0.14.1.zip" -OutFile $wintunZip -UseBasicParsing
        $wintunExtract = Join-Path $env:TEMP "wintun-extract"
        Expand-Archive -Path $wintunZip -DestinationPath $wintunExtract -Force
        $dll = Get-ChildItem -Path $wintunExtract -Filter "wintun.dll" -Recurse |
            Where-Object { $_.DirectoryName -match "amd64" } | Select-Object -First 1
        if ($dll) {
            Copy-Item $dll.FullName -Destination $wintunDll -Force
            Write-Host "  已安装" -ForegroundColor Green
        } else {
            Write-Host "  Warning: 未找到 amd64 wintun.dll" -ForegroundColor Yellow
        }
        Remove-Item $wintunZip -Force -ErrorAction SilentlyContinue
        Remove-Item $wintunExtract -Recurse -Force -ErrorAction SilentlyContinue
    } catch {
        Write-Host "  Warning: 下载失败（TUN 模式需手动安装）" -ForegroundColor Yellow
    }
}

# --- 完成 ---
Write-Host ""
Write-Host "=== 安装完成 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "已安装:" -ForegroundColor Green
Write-Host "  sing-box.exe  → $sbExe"
Write-Host "  sb.ps1/sb.bat → $InstallDir"
Write-Host "  wintun.dll    → $wintunDll"
Write-Host ""
Write-Host "下一步:" -ForegroundColor Yellow
Write-Host "  1. 重新打开终端（使 PATH 生效）"
Write-Host "  2. 填写 VPS 凭据："
Write-Host "     cp $InstallDir\.env.example $InstallDir\.env"
Write-Host "     notepad $InstallDir\.env"
Write-Host "  3. 生成配置并启动："
Write-Host "     cd $InstallDir"
Write-Host "     sb init"
Write-Host "     sb mixed"
