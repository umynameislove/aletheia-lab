# Changelog

Tất cả thay đổi đáng chú ý của dự án được ghi tại đây.
Định dạng theo [Keep a Changelog](https://keepachangelog.com/),
phiên bản theo [SemVer](https://semver.org/lang/vi/).

## [Unreleased]

### Added
- `scripts/check_repo_hygiene.py`: guard tách bạch code với tracking/
  planning/office/junk; wire vào `make hygiene`, pre-commit và CI.
- Skeleton repo git-ready: `LICENSE` (MIT), `CONTRIBUTING.md`,
  `PULL_REQUEST_TEMPLATE.md`, `CODEOWNERS`, `CITATION.cff`, `CHANGELOG.md`.
- Cấu trúc `src/aletheia_lab/` (benchmark, evidence, diagnosis, evaluation,
  reporting), `tests/`, `configs/`, `docs/`.
- CI mẫu, pre-commit, Makefile, pyproject.
- P1 `data_drift` injector: `benchmark/injectors.py` (`CategoricalDriftInjector`
  — deterministic, one-factor, ground-truth tách khỏi evidence signals) +
  `benchmark/signals.py` (PSI + phân phối categorical) +
  `tests/unit/test_drift_injector.py`.
- Chốt dataset P1 (Telco Customer Churn, drift feature `Contract`) trong
  `configs/project.yaml`; 5 injection settings trong
  `configs/benchmark/fault_types.yaml`.

### Changed
- Dọn `docs/`: chỉ giữ spec kỹ thuật (`02`,`03`,`04`,`adr/`); chuyển tài liệu
  kế hoạch (research claims, paper plan, defense notes, foundation projects)
  ra folder tracking ngoài repo; bỏ các bản trùng lặp (brief, roadmap,
  implementation order).

### Removed
- Áp nguyên tắc "narrow and deep": chỉ giữ code phục vụ vertical slice P1.
  Bỏ file rác `_deltest.tmp` (bị commit nhầm) và cache lẫn trong cây.
- Bỏ module chưa tới phase: `src/aletheia_lab/agents/` (P6 FactoryLens),
  `src/aletheia_lab/memory/` (P3 projmem adapter). Sẽ thêm lại khi vào phase.
- Bỏ script stub `NotImplementedError`: `run_benchmark`, `run_diagnosis`,
  `run_evaluation`, `export_report_tables`, `create_case_manifest`. Giữ
  `run_vertical_slice.py` (P1) và `check_repo_hygiene.py` (guard).

## [0.1.0] - 2026-07-07
### Added
- Bản skeleton đầu tiên cho đồ án Aletheia Lab.
