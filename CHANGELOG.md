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
  memory, reporting, agents), `tests/`, `configs/`, `docs/`.
- CI mẫu, pre-commit, Makefile, pyproject.

### Changed
- Dọn `docs/`: chỉ giữ spec kỹ thuật (`02`,`03`,`04`,`adr/`); chuyển tài liệu
  kế hoạch (research claims, paper plan, defense notes, foundation projects)
  ra folder tracking ngoài repo; bỏ các bản trùng lặp (brief, roadmap,
  implementation order).

## [0.1.0] - 2026-07-07
### Added
- Bản skeleton đầu tiên cho đồ án Aletheia Lab.
