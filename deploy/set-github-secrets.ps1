<#
Sets the deploy settings in BOTH SAFE repos at once, from files, so nothing is copy-pasted:

  secret   DEPLOY_HOST          the server's IPv4 address (not the domain: Cloudflare doesn't carry SSH)
  secret   DEPLOY_SSH_KEY       the private key the workflows log in with (made here if missing)
  secret   DEPLOY_KNOWN_HOSTS   the server's host keys, checked against what this PC already trusts
  variable SAFE_DOMAIN          the bare domain, like example.com

They go in as REPOSITORY secrets and variables, not environment ones: the frontend repo is private,
and GitHub's free plan has no environment secrets for private repos (and a misspelt environment
name silently makes a new, empty one). Running it again replaces the values; the key is kept.

Needs the GitHub CLI, logged in once:   gh auth login
Run from the backend checkout:
  powershell -ExecutionPolicy Bypass -File deploy\set-github-secrets.ps1 -Domain example.com -ServerIp 203.0.113.10
#>
param(
    [Parameter(Mandatory = $true)][string]$Domain,
    [Parameter(Mandatory = $true)][string]$ServerIp,
    [string]$Owner = "Iasam-KKK",
    [string[]]$Repos = @("safe-engineering-solutions-backend", "safe-engineering-solutions-frontend"),
    [string]$KeyPath = (Join-Path $env:USERPROFILE ".ssh\safe_deploy"),
    # trust the server's host keys even when this PC has never connected to it
    [switch]$TrustNewHostKey
)
$ErrorActionPreference = "Stop"

function Stop-With([string]$Message) {
    Write-Host ""
    Write-Host "STOPPED: $Message" -ForegroundColor Red
    exit 1
}

# Runs a program with exact arguments and optional text on its input; returns Code, Out, Err.
# (Windows PowerShell mangles empty arguments and adds CRLF to piped input; this does neither.)
function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$InputText = $null) {
    $quoted = foreach ($a in $Arguments) {
        if ($a -eq "") { '""' }
        elseif ($a -match '[\s"]') { '"' + ($a -replace '"', '\"') + '"' }
        else { $a }
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = $quoted -join " "
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    # On a UTF-8 console, .NET opens the program's input with the console's encoding and writes a
    # byte-order mark into it at once; that invisible mark would become the start of the secret.
    # So the program starts while the console encoding is UTF-8 without a mark.
    $saved = [Console]::InputEncoding
    try {
        [Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
        $p = [System.Diagnostics.Process]::Start($psi)
    } finally {
        [Console]::InputEncoding = $saved
    }
    if ($null -ne $InputText) { $p.StandardInput.Write($InputText) }
    $p.StandardInput.Close()
    $errTask = $p.StandardError.ReadToEndAsync()
    $out = $p.StandardOutput.ReadToEnd()
    $p.WaitForExit()
    return @{ Code = $p.ExitCode; Out = $out; Err = $errTask.Result }
}

function Write-LfFile([string]$Path, [string]$Text) {
    # LF line endings and no byte-order mark, exactly as the server and GitHub expect
    $Text = ($Text -replace "`r`n", "`n").TrimEnd("`n") + "`n"
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $false))
}

# ---- the values -------------------------------------------------------------------------------
$Domain = $Domain.Trim().ToLower() -replace '^[a-z]+://', '' -replace '/.*$', ''
if ($Domain -match '^(www|api)\.') { Stop-With "-Domain is the bare domain, like example.com (no www. or api.)" }
if ($Domain -notmatch '^[a-z0-9-]+(\.[a-z0-9-]+)+$') { Stop-With "-Domain '$Domain' isn't a domain name" }
$ServerIp = $ServerIp.Trim()
if ($ServerIp -notmatch '^(\d{1,3}\.){3}\d{1,3}$') {
    Stop-With "-ServerIp must be the server's IPv4 address (four numbers with dots), not a name"
}

$gh = (Get-Command gh -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $gh -and (Test-Path "C:\Program Files\GitHub CLI\gh.exe")) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }
if (-not $gh) { Stop-With "GitHub CLI not found. Install it (winget install GitHub.cli), then: gh auth login" }
$keygen = (Get-Command ssh-keygen -ErrorAction SilentlyContinue | Select-Object -First 1).Source
# Git's ssh-keyscan first: Windows' own is too old for newer servers (it offers a key exchange it
# can't do, "unsupported KEX method sntrup761x25519", and returns nothing).
$keyscans = @()
$gitScan = Join-Path $env:ProgramFiles "Git\usr\bin\ssh-keyscan.exe"
if (Test-Path $gitScan) { $keyscans += $gitScan }
$keyscans += @(Get-Command ssh-keyscan -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
if (-not $keygen -or $keyscans.Count -eq 0) { Stop-With "ssh-keygen / ssh-keyscan not found (Windows' OpenSSH client, or Git for Windows)" }

$r = Invoke-Native $gh @("auth", "status")
if ($r.Code -ne 0) { Stop-With "GitHub CLI isn't logged in. Run: gh auth login   (then this again)" }
foreach ($repo in $Repos) {
    $r = Invoke-Native $gh @("repo", "view", "$Owner/$repo", "--json", "name")
    if ($r.Code -ne 0) { Stop-With "Can't open $Owner/$repo with this GitHub login: $($r.Err.Trim())" }
}

# ---- the deploy key ---------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path (Split-Path $KeyPath) | Out-Null
if (-not (Test-Path $KeyPath)) {
    Write-Host "Making the deploy key $KeyPath (no passphrase: GitHub Actions uses it unattended)"
    $r = Invoke-Native $keygen @("-t", "ed25519", "-N", "", "-C", "safe-deploy (GitHub Actions)", "-f", $KeyPath, "-q")
    if ($r.Code -ne 0) { Stop-With "ssh-keygen failed: $($r.Err.Trim())" }
}
$privateKey = [System.IO.File]::ReadAllText($KeyPath)
if ($privateKey -notmatch 'PRIVATE KEY-----') { Stop-With "$KeyPath isn't a private key" }
$r = Invoke-Native $keygen @("-y", "-P", "", "-f", $KeyPath)
if ($r.Code -ne 0) { Stop-With "$KeyPath has a passphrase or is damaged; GitHub Actions needs one without. Move it away and run this again." }
$publicKey = $r.Out.Trim()
Write-LfFile "$KeyPath.pub" $publicKey

# ---- the server's host keys -------------------------------------------------------------------
$scanned = @()
foreach ($keyscan in $keyscans) {
    $r = Invoke-Native $keyscan @("-4", "-T", "15", "-t", "ed25519,ecdsa,rsa", $ServerIp)
    $scanned = @($r.Out -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith("#") })
    if ($scanned.Count -gt 0) { break }
}
if ($scanned.Count -eq 0) { Stop-With "No host keys from $ServerIp on port 22 (is the address right, and SSH open?)" }

# Compare with what this PC already trusts for that address (your own logins, the portfolio's deploy).
$trusted = @()
foreach ($f in @((Join-Path $env:USERPROFILE ".ssh\known_hosts"), (Join-Path $env:USERPROFILE ".ssh\portfolio_deploy_known_hosts"))) {
    if (Test-Path $f) {
        $r = Invoke-Native $keygen @("-F", $ServerIp, "-f", $f)
        $trusted += @($r.Out -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith("#") } |
            ForEach-Object { $p = $_ -split '\s+'; "$($p[1]) $($p[2])" })
    }
}
$scannedKeys = @($scanned | ForEach-Object { $p = $_ -split '\s+'; "$($p[1]) $($p[2])" })
if ($trusted.Count -gt 0) {
    $sameType = @($scannedKeys | Where-Object { $type = ($_ -split ' ')[0]; @($trusted | Where-Object { $_.StartsWith("$type ") }).Count -gt 0 })
    $mismatch = @($sameType | Where-Object { $trusted -notcontains $_ })
    if ($mismatch.Count -gt 0 -or $sameType.Count -eq 0) {
        Stop-With "The server at $ServerIp answers with host keys this PC doesn't know for it. Check the address; if the server was rebuilt, remove its old line: ssh-keygen -R $ServerIp"
    }
    Write-Host "Host keys of $ServerIp match what this PC already trusts."
} elseif (-not $TrustNewHostKey) {
    Write-Host "This PC has never connected to $ServerIp. Its host keys:"
    foreach ($line in $scanned) {
        $tmp = [System.IO.Path]::GetTempFileName()
        Write-LfFile $tmp $line
        $fp = Invoke-Native $keygen @("-l", "-f", $tmp)
        Remove-Item $tmp
        Write-Host "  $($fp.Out.Trim())"
    }
    Stop-With "Compare them with: sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub (on the server), then run this again with -TrustNewHostKey"
}
$knownHostsPath = "${KeyPath}_known_hosts"
Write-LfFile $knownHostsPath ($scanned -join "`n")

# ---- into both repos --------------------------------------------------------------------------
$values = [ordered]@{
    DEPLOY_HOST        = $ServerIp
    DEPLOY_SSH_KEY     = ($privateKey -replace "`r`n", "`n")
    DEPLOY_KNOWN_HOSTS = [System.IO.File]::ReadAllText($knownHostsPath)
}
foreach ($repo in $Repos) {
    $full = "$Owner/$repo"
    foreach ($name in $values.Keys) {
        $value = $values[$name].TrimStart([char]0xFEFF)
        $r = Invoke-Native $gh @("secret", "set", $name, "--app", "actions", "--repo", $full) $value
        if ($r.Code -ne 0) { Stop-With "Setting secret $name in $full failed: $($r.Err.Trim())" }
    }
    $r = Invoke-Native $gh @("variable", "set", "SAFE_DOMAIN", "--repo", $full, "--body", $Domain)
    if ($r.Code -ne 0) { Stop-With "Setting variable SAFE_DOMAIN in $full failed: $($r.Err.Trim())" }

    # read back the names, so a typo or a wrong place shows up now, not in a deploy
    $secretNames = (Invoke-Native $gh @("secret", "list", "--app", "actions", "--repo", $full, "--json", "name", "--jq", ".[].name")).Out -split "`n" | ForEach-Object { $_.Trim() }
    $varNames = (Invoke-Native $gh @("variable", "list", "--repo", $full, "--json", "name", "--jq", ".[].name")).Out -split "`n" | ForEach-Object { $_.Trim() }
    foreach ($name in $values.Keys) { if ($secretNames -notcontains $name) { Stop-With "$full doesn't list secret $name after setting it" } }
    if ($varNames -notcontains "SAFE_DOMAIN") { Stop-With "$full doesn't list variable SAFE_DOMAIN after setting it" }
    foreach ($stray in @("DEPLOY_HOST", "DEPLOY_SSH_KEY", "DEPLOY_KNOWN_HOSTS")) {
        if ($varNames -contains $stray) { Write-Host "  Note: $full also has a VARIABLE named $stray; delete it (gh variable delete $stray --repo $full)" -ForegroundColor Yellow }
    }
    if ($secretNames -contains "SAFE_DOMAIN") { Write-Host "  Note: $full also has a SECRET named SAFE_DOMAIN; delete it (gh secret delete SAFE_DOMAIN --repo $full)" -ForegroundColor Yellow }
    Write-Host "$full : secrets DEPLOY_HOST, DEPLOY_SSH_KEY, DEPLOY_KNOWN_HOSTS and variable SAFE_DOMAIN=$Domain are set." -ForegroundColor Green
}

Write-Host ""
Write-Host "Files on this PC:"
Write-Host "  $KeyPath            private key (stays here and in GitHub only)"
Write-Host "  $KeyPath.pub        public key (goes to the server)"
Write-Host "  $knownHostsPath"
Write-Host ""
Write-Host "On the server (deploy/README.md, 'Setting the server up'):"
Write-Host "  sudo bash safe-deploy/bootstrap.sh $Domain `"$publicKey`" <Drive folder id>"
