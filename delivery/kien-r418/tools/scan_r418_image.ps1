$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$tag = 'aletheia-hosted:r418-65c87fdb0a43'
$manifest = Get-Content -Raw 'Release-K01-K18-R4.18/source-manifest.json' | ConvertFrom-Json
$imageId = docker image inspect --format '{{.Id}}' $tag
$label = docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' $tag
if ($label -ne $manifest.source_identity) { throw 'Container source label did not match the sealed manifest.' }
$smoke = @'
import hashlib, json, os, sqlite3, sys, zlib
from pathlib import Path
import aletheia_lab
import aletheia_lab.hosted.managed_worker as worker
assert (os.getuid(), os.getgid()) == (10001, 10001)
assert callable(worker._worker_clock)
assert zlib.decompress(zlib.compress(b'R4.18')) == b'R4.18'
manifest = json.load(sys.stdin)
base = Path(aletheia_lab.__file__).resolve().parent
runtime = {name.removeprefix('src/aletheia_lab/'): digest for name, digest in manifest['files'].items() if name.startswith('src/aletheia_lab/')}
assert len(runtime) == manifest['runtime_count']
assert all(hashlib.sha256((base / path).read_bytes()).hexdigest() == digest for path, digest in runtime.items())
try:
    open('/var/lib/aletheia/write-probe', 'xb').close()
except OSError as exc:
    assert exc.errno == 30
else:
    raise AssertionError('Container root is writable')
print(json.dumps(dict(uid=os.getuid(),gid=os.getgid(),sqlite=sqlite3.sqlite_version,zlib=zlib.ZLIB_RUNTIME_VERSION,release_id=os.environ['ALETHEIA_RELEASE_ID'],readonly=True,worker_entrypoint_import=True,runtime_files_verified=len(runtime))))
'@
Get-Content -Raw 'Release-K01-K18-R4.18/source-manifest.json' | docker run --rm -i --network none --read-only --cap-drop ALL --security-opt no-new-privileges --entrypoint /opt/venv/bin/python $tag -B -c $smoke | Set-Content -Encoding utf8 'evidence/image-r418-smoke.json'
$scratch = Join-Path (Get-Location) 'tmp/r418-image-offline'
New-Item -ItemType Directory -Path $scratch | Out-Null
$db = (Resolve-Path 'tmp/trivy-public/db').Path
$tar = Join-Path $scratch 'candidate.tar'
docker image save -o $tar $tag
$trivy = 'aquasec/trivy:0.76.2'
docker run --rm --network none --mount "type=bind,source=$scratch,target=/input,readonly" --mount "type=bind,source=$db,target=/cache/db,readonly" $trivy image --cache-dir /cache --skip-db-update --skip-java-db-update --offline-scan --scanners vuln --format json --input /input/candidate.tar > evidence/image-r418-trivy-offline.json 2> evidence/image-r418-trivy-offline.log
docker run --rm --network none --mount "type=bind,source=$scratch,target=/input,readonly" $trivy image --skip-db-update --skip-java-db-update --offline-scan --format cyclonedx --input /input/candidate.tar > evidence/image-r418-sbom.cdx.json 2> evidence/image-r418-sbom.log
$scan = Get-Content -Raw 'evidence/image-r418-trivy-offline.json' | ConvertFrom-Json
$sbom = Get-Content -Raw 'evidence/image-r418-sbom.cdx.json' | ConvertFrom-Json
$vulns = @($scan.Results | ForEach-Object { $_.Vulnerabilities } | Where-Object { $_ })
$high = @($vulns | Where-Object Severity -eq HIGH)
$critical = @($vulns | Where-Object Severity -eq CRITICAL)
$fingerprint = { param($report) @($report.Results | ForEach-Object { $_.Vulnerabilities } | Where-Object { $_ } | ForEach-Object { $_.VulnerabilityID + '|' + $_.PkgName + '|' + $_.InstalledVersion + '|' + $_.FixedVersion + '|' + $_.Severity } | Sort-Object) }
$previous = Get-Content -Raw 'evidence/image-r415-trivy-offline.json' | ConvertFrom-Json
$same = -not (Compare-Object -ReferenceObject (& $fingerprint $previous) -DifferenceObject (& $fingerprint $scan))
$receipt = [ordered]@{
    source_identity = $manifest.source_identity
    runtime_identity = $manifest.runtime_identity
    image_id = $imageId
    tag = $tag
    scanner_image_id = (docker image inspect --format '{{.Id}}' $trivy)
    scanner_db = (Get-Content -Raw (Join-Path $db 'metadata.json') | ConvertFrom-Json)
    readonly_offline_smoke = (Get-Content -Raw 'evidence/image-r418-smoke.json' | ConvertFrom-Json)
    scan_sha256 = (Get-FileHash 'evidence/image-r418-trivy-offline.json').Hash.ToLowerInvariant()
    sbom_sha256 = (Get-FileHash 'evidence/image-r418-sbom.cdx.json').Hash.ToLowerInvariant()
    counts = [ordered]@{}
    critical = $critical.Count
    high = $high.Count
    high_unique_ids = @($high.VulnerabilityID | Sort-Object -Unique)
    high_with_fixed_version = @($high | Where-Object FixedVersion).Count
    sbom_components = @($sbom.components).Count
    same_package_vulnerability_inventory_as_r415 = $same
    release_security_pass = ($critical.Count -eq 0 -and $high.Count -eq 0)
    finished_utc = [DateTime]::UtcNow.ToString('o')
}
foreach ($group in ($vulns | Group-Object Severity)) { $receipt.counts[$group.Name] = $group.Count }
$receipt | ConvertTo-Json -Depth 10 | Tee-Object -FilePath 'evidence/image-r418-security-summary.json'
