"""Package verified R4.14 technical evidence for Quân; never issue final acceptance."""
from pathlib import Path
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'evidence'
KIT = ROOT / 'Release-K01-K18-R4.14'
HANDOFF = ROOT / 'handoff'


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    assert not path.exists(), path
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def suite(path):
    cases = list(ET.parse(path).getroot().iter('testcase'))
    return dict(tests=len(cases), failed=sum(c.find('failure') is not None or c.find('error') is not None for c in cases),
                skipped=sum(c.find('skipped') is not None for c in cases), sha256=sha(path))


def archive(path, entries):
    assert not path.exists() and len(entries) == len(set(entries))
    assert all(not n.startswith('/') and '..' not in Path(n).parts and '\\' not in n
               and '.private' not in Path(n).parts and Path(n).name not in ('.env', 'actors.jsonl') for n in entries)
    hashes = {n: sha(p) for n, p in entries.items()}
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, original in sorted(entries.items()):
            z.write(original, name)
        z.writestr('SHA256SUMS.json', json.dumps(hashes, sort_keys=True, indent=2))
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        assert set(z.namelist()) == set(entries) | {'SHA256SUMS.json'}
        assert all(hashlib.sha256(z.read(n)).hexdigest() == h for n, h in hashes.items())
    path.with_suffix(path.suffix + '.sha256').write_text(sha(path) + '  ' + path.name + '\n', encoding='ascii')


def main():
    m = read(KIT / 'source-manifest.json')
    assert all(sha(KIT / 'source' / n) == h for n, h in m['files'].items())
    unit, db = suite(E / 'windows-unit-r4.14/junit.xml'), suite(E / 'windows-r414-preview-migration/junit.xml')
    assert unit['tests'] == 2948 and db['tests'] == 33
    assert not any(x['failed'] or x['skipped'] for x in (unit, db))
    for p in ('windows-unit-r4.14/result.json', 'windows-r414-preview-migration/result.json'):
        assert read(E / p)['exit_code'] == 0 and read(E / p)['source_stable']
    browser = read(E / 'browser-api-pdf-r414-repeat-upload-sealed/observation-summary.json')
    assert browser['repeat_uploads'] == 3 and browser['http_status_counts'] == {'2xx': 86}
    assert read(E / 'browser-api-pdf-r414-repeat-upload-sealed/pdf-parser.json')['pages'] == 12
    lint = read(E / 'r414-lint-hygiene-clean.json')
    assert lint['lint_exit'] == lint['hygiene_exit'] == 0
    build_a, build_b = [read(E / ('linux-release-r4.14/build-' + n + '.json')) for n in ('a', 'b')]
    assert build_a['artifacts'] == build_b['artifacts']
    installed = read(E / 'linux-release-r4.14/installed-proof/result.json')
    assert installed['status'] == 'passed' and installed['child_origin_records'] == 4
    assert installed['all_runtime_byte_parity'] and installed['runtime_pin_count'] == 52 and installed['no_source_pythonpath']
    image = read(E / 'image-build-r414-result.json')
    assert image['source_identity'] == m['source_identity'] and image['read_only_no_network_smoke']['preview_v2_packaged']
    security = read(E / 'image-r414-security-summary.json')
    assert security['image_id'] == image['image_id']
    static = read(E / 'security-b608-per-finding-review-r414.json')
    maintain = read(E / 'r414-maintainability.json')

    source_zip = HANDOFF / 'Source-Kien-R4.14-ac2951f16664.zip'
    entries = {'Release-K01-K18-R4.14/source/' + n: KIT / 'source' / n for n in m['files']}
    for n in ('source-manifest.json', 'changes-from-r31.json', 'installed_wheel_check.py', 'test-constraints.txt'):
        entries['Release-K01-K18-R4.14/' + n] = KIT / n
    for folder in ('dist', 'tools', 'tests'):
        for p in (KIT / folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                entries['Release-K01-K18-R4.14/' + p.relative_to(KIT).as_posix()] = p
    archive(source_zip, entries)

    current = dict(scope='Internal technical closeout observations. Not final Bao acceptance.',
        updated_utc=datetime.now(timezone.utc).isoformat(), revision='R4.14', source_identity=m['source_identity'],
        runtime_identity=m['runtime_identity'], unit=unit, selected_postgres=db,
        actual_browser=browser, reproducible_build=build_a, installed_release=installed, image=image,
        security_advisory_counts=security['counts'], bandit_findings=static['findings_count'],
        maintainability_findings=maintain['findings'], source_zip=source_zip.name, source_zip_sha256=sha(source_zip),
        all_k01_k18_pass=False, final_bao_report_exported=False,
        remaining=['Production v3 exact-decimal window contract/producer/parser/consumer and primary dataset not accepted or wired.',
                   'Staging/IdP + 32 identity and joint browser cases + remote CI/release Git SHA.',
                   'Separate generator, primary50k/soak/cost, DB40/F26 and release topology fairness.',
                   'Off-host backup and independent restore/rollout/rollback.',
                   '38 B608 CLI findings, frozen maintainability budgets and 47 HIGH image records require handling.'])
    save(E / 'r414-working-evidence-status.json', current)

    original = (HANDOFF / 'PHAN_HOI_GUI_QUAN_R413.md').read_text(encoding='utf-8')
    original = original.replace('R4.13', 'R4.14').replace('Bộ unit Python R4.12', 'Bộ unit Python R4.14')
    note = f'''# Bổ sung R4.14 — lỗi ZIP đã qua luồng Chrome thật

Đây là gói trao đổi kỹ thuật với Quân/Bù, chưa phải hồ sơ nghiệm thu cuối cho Bảo.
Không tuyên bố tất cả K01–K18 đã pass.

Source identity: `{m['source_identity']}`. Runtime identity: `{m['runtime_identity']}`.
Source ZIP: `{source_zip.name}`, SHA-256 `{sha(source_zip)}`.
Mỗi ZIP có SHA256SUMS.json, đã đọc lại CRC và hash toàn bộ entry sau đóng gói.

## Phần sửa cuối và cách nâng cấp

R4.13 vẫn thất bại ở lượt confirm thứ hai trên Chrome/API/worker thật. Log chẩn đoán cho thấy
CheckViolation SQLSTATE 23514 tại guard preview, ngoài lỗi CAS đã tái hiện trước đó.
R4.14 thêm preview migration V2. SQL V1 và dòng ledger V1 giữ nguyên; không tắt trigger.
Guard mới vẫn ghim creator, source mới, preview còn hạn, workspace và hash ZIP; cho phép source
cũ của snapshot chỉ khi cùng hash byte ZIP. Publisher vẫn yêu cầu mọi state/mapping ngoài source ID
giống hệt, tái kiểm quyền source mới/cũ và current pointer. Mapping khác và ZIP khác bị chặn.

Dừng/drain API và worker cũ; dùng operator để migrate core V17, migrate preview V2 rồi grant
runtime và khởi động bản mới. Không sửa checksum lịch sử, không chạy DDL tự động lúc startup.
Đã kiểm nâng cấp V1→V2 giữ nguyên ledger cũ và core facts. R4.14 chỉ đổi 7 tệp so R4.13;
180 tệp UI byte-for-byte không đổi. Xem r414-change-scope.json.

## Kết quả có receipt

| Phép kiểm | Kết quả và phạm vi |
|---|---|
| Windows unit trên source niêm phong R4.14 | 2.948 pass, 5 deselected; 198,88 giây pytest; source stable |
| PostgreSQL hồi quy trên R4.14 | 33 pass, không skip, 158,01 giây pytest; preview, nâng cấp, revoke/quyền hạn chế, ZIP lặp, conflict sau ACK |
| UI cùng bytes R4.14 | 357/357 unit, hosted build/budget pass; Chrome transport kiểm soát 27/27 ở receipt R4.13 |
| Chrome 157.0.8103.52 + API/worker/PG thật | ZIP 662 byte nhập 3 lần vào cùng workspace; 9 job preview/confirm/analyze settled; 86 HTTP đều 2xx |
| Thời gian browser | {browser['chrome_journey_seconds']:.3f} giây luồng Chrome; {browser['total_setup_and_journey_seconds']:.3f} giây kể cả setup |
| Cấu hình luồng | API pool max 4; worker pool 2, 2 slot; lease 120s; worker timeout 90s; poll min 2s; TLS ghim SPKI; session seed synthetic, không có login IdP |
| Tính nhất quán | Cả 3 cùng project/snapshot/result, 3 analyze job khác nhau; graph 22 node/30 cạnh, endpoint bấm được |
| PDF qua download thật | 35.797 byte, 12 trang, có metric tiếng Việt và đúng snapshot/result; SHA-256 `{browser['pdf_sha256']}` |
| Wheel/sdist | Build hai lần cho hash artifact trùng; clean runtime-only install, 4 child thật, JSON/MD/PDF và parser PDF pass; đủ SQL preview V2 |
| Container | non-root UID/GID 10001, read-only, không mạng, cap-drop ALL; SQL V2 có trong image |

Full Windows PostgreSQL R4.12 giữ nguyên kết quả 417 pass/1 fail/18 skip, không đổi thành pass.
Ca fail yêu cầu ít nhất 3 background read trong cửa sổ cạn pool nhưng lần đó chỉ có 1.
Chạy riêng nguyên assertion trên R4.13: 2/2 pass; pool 250ms có 9 read, max 16ms; row lock 313ms
có 8 read, max 78ms; không read lỗi, failed attempt 0 effect, replay 1 effect, pool hết waiter.
Đây là test biên repository hai tenant, không phải fairness/SLO primary.

Linux focused R4.12: 93 pass/1 fail do child chưa start trong 20s từ filesystem /mnt/c.
Cùng source/case trên ext4, giữ timeout 20s: 1 pass/7,74s. Giữ cả log fail và pass riêng.
Các lỗi fixture âm tính/launcher trong quá trình sửa cũng giữ lại, không tính thành lỗi sản phẩm đã qua.

## Tải đã đo và những cổng còn mở

Campaign cũ R4.10/R4.11, không đổi nhãn thành campaign R4.14: 100 principal synthetic, 5 vai trò,
1 API pool 4, 1 worker pool 2/2 slot; export active 1 + queue 4; cùng laptop i5-13500H/16,786GB RAM.
Hai campaign open 5 action/s, 30s warmup + 120s steady đều 590/590 measured-positive đúng hạn.
10/s chỉ 1.178/1.180 (99,8305%); 20/s chỉ 2.025/2.346 (86,3171%). Closed 100 của R4.11 dừng
vì follow-up failed, 924/968 (95,4545%). Không nâng tỷ lệ bằng cách bỏ lỗi khỏi mẫu số.
Đó là quan sát local, chưa chứng nhận per-stratum SLO, 50k user hay công suất production.

Mẫu v3 đã có CSV/JSON đầy đủ 100/1.000/10.000 claim nhưng là prototype offline, chưa nối production.
Nhờ trả lời 6 quyết định contract ở phần bên dưới và cung cấp staging/IdP, máy phát tải riêng,
metrics API/worker/DB/generator, người chạy độc lập restore và Git SHA/CI.
Nhờ chạy các ca còn thiếu trong 3 CSV kèm theo; không tự điền pass nếu chưa có log/candidate/identity.

Quét image chính xác R4.14 offline: 0 CRITICAL, {security['counts'].get('HIGH', 0)} HIGH record,
{len(security['unique_high_ids'])} CVE HIGH riêng; không có FixedVersion trong DB đang dùng; SBOM {security['sbom_components']} component.
Đây không phải kết luận an toàn. Bandit còn 38 B608 (CLI exit 1), maintainability còn 141 C901
và 10 finding budget/hash/module. Không đổi frozen budget hay suppress để biến thành pass.
Lockfile UI cùng bytes có 126 package, Trivy offline không thấy CVE; chưa chạy npm audit CLI.

'''
    detail = HANDOFF / 'PHAN_HOI_GUI_QUAN_R414.md'
    assert not detail.exists()
    detail.write_text(note + original, encoding='utf-8')
    message = HANDOFF / 'TIN_NHAN_GUI_QUAN_R414.txt'
    message.write_text('Quân/Bù ơi, Kiên đã nhận đúng gói UI 708aa29a… và sửa xong lỗi nhập lại ZIP.\n'
        'R4.14 đã chạy Chrome thật nhập cùng ZIP 3 lần: cả ba confirm/analyze thành công, giữ cùng snapshot/result; PDF tải đúng hash, đủ 12 trang.\n'
        'Có 2.948 unit + 33 PostgreSQL pass. Có migration core V17 và preview V2, cần drain/migrate/grant rồi restart; đừng sửa checksum cũ.\n'
        'Gói Gui-Quan-Bu-R4.14-20261114.zip chứa source, wheel/sdist, receipt và mô tả chi tiết PHAN_HOI_GUI_QUAN_R414.md.\n\n'
        'Nhờ Quân/Bù:\n1. Ghép source/API mới, chốt Git SHA rồi lặp ZIP và hai writer gây conflict; xác nhận metadata tại attempts + feature jobs.conflict_v1.\n'
        '2. Trả lời/chuyển Bảo 6 quyết định v3 trên mẫu CSV/JSON thực kèm gói; prototype chưa phải producer production.\n'
        '3. Gửi đầu mối staging/IdP, 5 vai trò test, máy phát tải riêng, metrics và kho backup ngoài host. Không gửi mật khẩu/token trong chat.\n'
        '4. Bố trí CI và người/máy restore độc lập trên Git SHA cuối; chạy các ca còn thiếu trong 3 CSV và trả receipt/log.\n'
        '5. Phân công xử lý gate maintainability/Bandit và HIGH image. Hiện vẫn chưa pass các gate đó.\n\n'
        'Chưa có xác nhận 50k/primary hoặc all K01–K18 pass. Đây là bàn giao kỹ thuật cho Quân/Bù, chưa xuất nghiệm thu cuối cho Bảo.\n', encoding='utf-8')

    selected = ['r414-working-evidence-status.json', 'r414-change-scope.json', 'r414-lint-hygiene-clean.json',
        'r414-sealed-lint.log', 'r414-sealed-hygiene-clean.log', 'security-static-r414.json',
        'security-b608-per-finding-review-r414.json', 'r414-maintainability.json', 'image-build-r414-result.json',
        'image-r414-security-summary.json', 'image-r414-trivy-offline.json', 'image-r414-sbom.cdx.json',
        'ui-r413-trivy-offline-summary.json', 'ui-r413-trivy-offline.json', 'quan-r411-received.json',
        'quan-r411-ui-integration.json', 'quan-r411-peer-evidence-review.json', 'r412-ui-conflict-unit.log',
        'r412-ui-hosted-build.log', 'r412-ui-hosted-budget.log', 'r413-ui-hosted-browser-native.log',
        'capacity-campaign-comparison-r411.json']
    packet = {source_zip.name: source_zip, source_zip.name + '.sha256': source_zip.with_suffix('.zip.sha256'),
              detail.name: detail, message.name: message}
    for n in selected:
        packet['evidence/' + n] = E / n
    folders = ['windows-unit-r4.14', 'windows-r414-preview-migration', 'browser-api-pdf-r414-repeat-upload-sealed',
        'linux-release-r4.14', 'r413-k17-fault-isolated', 'windows-postgres-r4.12', 'r412-repeat-import-before3',
        'r412-repeat-import-after', 'browser-api-pdf-r413-repeat-diagnostic', 'k17-window-contract-prototype-r412']
    for folder in folders:
        for p in (E / folder).rglob('*'):
            if p.is_file():
                packet['evidence/' + p.relative_to(E).as_posix()] = p
    for n in ('Ca-kiem-danh-tinh.csv', 'Gate-phat-hanh.csv', 'Nghiem-thu-chung.csv'):
        packet['can-phoi-hop/' + n] = HANDOFF / n
    for n in ('run_browser_pdf.py', 'browser_pdf.mjs', 'k17_window_contract_prototype.py', 'linux_r414_build_runtime.sh'):
        packet['actual-reproduction-tools/' + n] = ROOT / 'tools' / n
    target = HANDOFF / 'Gui-Quan-Bu-R4.14-20261114.zip'
    archive(target, packet)
    save(E / 'r414-handoff-package-receipt.json', dict(source_identity=m['source_identity'],runtime_identity=m['runtime_identity'],
        source_archive=dict(name=source_zip.name,bytes=source_zip.stat().st_size,sha256=sha(source_zip)),
        technical_packet=dict(name=target.name,bytes=target.stat().st_size,sha256=sha(target),files=len(packet)+1),
        archive_crc_and_every_entry_sha256_verified=True, sent_to_anyone=False, final_bao_report_exported=False))
    print(json.dumps(dict(packet=str(target),sha256=sha(target),all_k01_k18_pass=False),ensure_ascii=True))


if __name__ == '__main__':
    main()
