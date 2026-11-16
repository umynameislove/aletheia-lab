# Raw evidence archive

This archive preserves 27 original records (855,650 bytes) without changing their content. Reports, screenshots and reproduction source remain readable beside it. Raw records include unsuccessful trials; their presence does not change any acceptance decision.

Verify `raw-records-20261116.zip.sha256`, then extract the ZIP **into this directory**, retaining its paths. `ARCHIVE-MANIFEST.csv` gives the original size and SHA-256 of every member. Local originals are ignored because the verified archive is the published copy.

```powershell
Get-FileHash -Algorithm SHA256 ./raw-records-20261116.zip
Expand-Archive -LiteralPath ./raw-records-20261116.zip -DestinationPath . -Force
```

Extraction restores the relative paths referenced by the reports and original checksum manifests. The archive was checked by ZIP CRC and by comparing every member's SHA-256 with the original file. No credential files outside this evidence directory are included.
