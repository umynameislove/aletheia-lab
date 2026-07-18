# Dataset Card — Telco Customer Churn

Dataset của benchmark `data_drift`. Card này mô tả nguồn,
checksum, schema, và cách tái lập chính xác. Script liên quan: `scripts/download_dataset.py`
(logic ở `src/aletheia_lab/data/`).

## Nguồn & checksum (pinned)

| Trường | Giá trị |
|---|---|
| `dataset.id` | `telco_customer_churn` |
| URL | `raw.githubusercontent.com/IBM/telco-customer-churn-on-icp4d/master/data/Telco-Customer-Churn.csv` |
| SHA-256 | `16320c9c1ec72448db59aa0a26a0b95401046bef5d02fd3aeb906448e3055e91` |
| Kích thước | 970,457 bytes |
| Số dòng | 7,043 (không kể header) |
| Số cột | 21 |
| Pin tại | `src/aletheia_lab/data/sources.py` |

Checksum là hợp đồng tái lập: file tải về chỉ được chấp nhận khi SHA-256 khớp
pin. Nguồn bị đổi/di dời/hỏng sẽ làm bước tải **fail** thay vì trôi âm thầm.

## License & phạm vi

Đây là IBM sample data. Người sử dụng phải kiểm tra điều khoản của nguồn upstream
trước khi phân phối lại. Repository chỉ pin URL và checksum; dữ liệu không được
phân phối trong Git.

Dữ liệu **không** được commit vào git (`data/raw/*`, `data/processed/*` đã nằm
trong `.gitignore`). Chỉ script, card, và pin (URL + checksum) nằm trong repo;
provenance khi chạy được ghi ở `data/raw/telco_customer_churn.provenance.json`
(local, gitignored).

## Schema (sau prep)

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `customerID` | str | Định danh khách hàng |
| `gender` | str | |
| `SeniorCitizen` | int | 0 / 1 |
| `Partner` | str | Yes / No |
| `Dependents` | str | Yes / No |
| `tenure` | int | Số tháng gắn bó |
| `PhoneService` | str | |
| `MultipleLines` | str | |
| `InternetService` | str | DSL / Fiber optic / No |
| `OnlineSecurity` | str | |
| `OnlineBackup` | str | |
| `DeviceProtection` | str | |
| `TechSupport` | str | |
| `StreamingTV` | str | |
| `StreamingMovies` | str | |
| `Contract` | str | **Drift feature**: Month-to-month / One year / Two year |
| `PaperlessBilling` | str | |
| `PaymentMethod` | str | |
| `MonthlyCharges` | float | |
| `TotalCharges` | float | Xem quirk bên dưới |
| `Churn` | str | **Target**: Yes / No |

## Quirk đã xử lý

`TotalCharges` ở raw là kiểu chuỗi vì 11 khách mới (`tenure == 0`) có ô trống.
Prep coerce về `float`, gán các ô trống thành `0.0` (chưa phát sinh cước), và
đếm lại số ô bị gán để báo cáo (`total_charges_blanks_zeroed`). Không drop dòng.

## Splits

Case manifest phân dữ liệu thành `dev` / `main` / `human_audit` /
`organic_validity` (xem `evidence-contract.md` và `benchmark/manifest.py`).

## Tái lập

```bash
make data
# tương đương:
python scripts/download_dataset.py all         # tải + verify checksum, rồi prep
python scripts/download_dataset.py verify       # chỉ kiểm checksum, không cần mạng
python scripts/download_dataset.py download --offline   # verify file đã có sẵn
```

Prep thuần và xác định: cùng một file raw luôn cho ra file processed giống hệt
(cùng bytes, cùng checksum), là điều kiện để loader và seeded baseline tái lập
được metric.
