param([string]$Tag = 'aletheia-hosted:r418-k16-wolfi-offline')
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
Set-Location -LiteralPath (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
$kit = (Resolve-Path 'Release-K01-K18-R4.18').Path
$baseline = (Resolve-Path 'evidence/linux-release-r4.18/installed-proof').Path
$proof = (Resolve-Path 'evidence/k16-r418-image').Path
$probe = Join-Path $PSScriptRoot 'probe.py'
$trivy = 'aquasec/trivy@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb'
$image = (docker image inspect $tag | ConvertFrom-Json)[0]
$meta = Get-Content -Raw (Join-Path $kit 'source-manifest.json') | ConvertFrom-Json
if ($image.Config.Labels.'org.opencontainers.image.revision' -ne $meta.source_identity) { throw 'Wrong source label.' }
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 192 --cpus 2 --memory 2g --tmpfs /tmp:rw,noexec,nosuid,size=268435456,mode=1777 --mount "type=bind,source=$kit,target=/kit,readonly" --mount "type=bind,source=$baseline,target=/baseline,readonly" --mount "type=bind,source=$probe,target=/probe.py,readonly" --entrypoint /opt/venv/bin/python $tag -I -B /probe.py > "$proof/wolfi-probe.json" 2> "$proof/wolfi-probe.log"
docker run --rm --network none --read-only --cap-drop ALL --entrypoint /sbin/apk $tag info -vv > "$proof/wolfi-apk-inventory.txt"
$db = (Resolve-Path 'tmp/k16-fresh-trivy/db').Path
$database = Get-Content -Raw (Join-Path $db 'metadata.json') | ConvertFrom-Json
if ([DateTimeOffset]::Parse($database.NextUpdate) -le [DateTimeOffset]::UtcNow) { throw 'Trivy database expired; refresh the isolated cache.' }
$scratch = (Resolve-Path 'tmp/k16-r418-wolfi').Path
docker image save -o (Join-Path $scratch 'candidate.tar') $tag
$common = @('run','--rm','--network','none','--mount',"type=bind,source=$scratch,target=/input,readonly",
    '--mount',"type=bind,source=$db,target=/cache/db,readonly",$trivy,'image','--cache-dir','/cache',
    '--skip-db-update','--skip-java-db-update','--offline-scan','--input','/input/candidate.tar')
docker @common --scanners vuln --list-all-pkgs --format json > "$proof/wolfi-trivy.json" 2> "$proof/wolfi-trivy.log"
docker @common --format cyclonedx > "$proof/wolfi-sbom.cdx.json" 2> "$proof/wolfi-sbom.log"
$scan = Get-Content -Raw "$proof/wolfi-trivy.json" | ConvertFrom-Json
$sbom = Get-Content -Raw "$proof/wolfi-sbom.cdx.json" | ConvertFrom-Json
if ($scan.Metadata.OS.Family -ne 'wolfi') { throw 'Trivy did not identify Wolfi.' }
if (-not ($scan.Results | Where-Object Class -eq 'os-pkgs') -or -not ($scan.Results | Where-Object Class -eq 'lang-pkgs')) { throw 'Missing OS or Python scanner coverage.' }
$osResult = @($scan.Results | Where-Object Class -eq 'os-pkgs')
$vulns = @($scan.Results | ForEach-Object Vulnerabilities | Where-Object { $_ })
$old = Get-Content -Raw 'evidence/image-r418-security-summary.json' | ConvertFrom-Json
$severe = @($vulns | Where-Object Severity -in @('HIGH', 'CRITICAL'))
$receipt = [ordered]@{
    source_identity = $meta.source_identity
    runtime_identity = $meta.runtime_identity
    image_id = $image.Id
    original_image_id = $old.image_id
    tag = $tag
    architecture = $image.Architecture
    image_bytes = $image.Size
    database = $database
    scanner = $trivy
    scan_sha256 = (Get-FileHash "$proof/wolfi-trivy.json").Hash.ToLowerInvariant()
    sbom_sha256 = (Get-FileHash "$proof/wolfi-sbom.cdx.json").Hash.ToLowerInvariant()
    sbom_components = @($sbom.components).Count
    os_packages_scanned = @($osResult | ForEach-Object Packages | Where-Object { $_ }).Count
    original_high_cves_still_reported = @($vulns | Where-Object VulnerabilityID -in $old.high_unique_ids | Select-Object -ExpandProperty VulnerabilityID -Unique)
    severity_counts = [ordered]@{}
    severe_records = @($severe | Select-Object VulnerabilityID,PkgName,InstalledVersion,FixedVersion,Severity,PrimaryURL)
    runtime_probe = (Get-Content -Raw "$proof/wolfi-probe.json" | ConvertFrom-Json)
    completed_utc = [DateTimeOffset]::UtcNow.ToString('o')
}
foreach ($group in ($vulns | Group-Object Severity)) { $receipt.severity_counts[$group.Name] = $group.Count }
$receipt.image_vulnerability_gate_pass = ($severe.Count -eq 0 -and $receipt.os_packages_scanned -gt 0)
$receipt.k16_deployed_gate_pass = $false
$receipt | ConvertTo-Json -Depth 12 | Set-Content -Encoding utf8 "$proof/wolfi-result.json"
Get-Content -Raw "$proof/wolfi-result.json"
if (-not $receipt.image_vulnerability_gate_pass) { exit 1 }
