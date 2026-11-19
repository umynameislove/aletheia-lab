#!/bin/bash
set -eu
cd "$(dirname "$0")"
read -r -s -p "Ma dien tap K16 (khong hien): " token
printf '\n'
if [[ ! "$token" =~ ^[0-9a-f]{48}$ ]]; then
  printf 'Ma khong dung dinh dang.\n' >&2
  exit 1
fi
call() {
  path="$1"
  shift
  printf 'header = "Authorization: Bearer %s"\n' "$token" |
    curl -fsS --max-time 8 --noproxy '*' --cacert ca.pem       --connect-to 'localhost:19016:192.168.2.61:19016' -K - "$@" "https://localhost:19016/$path"
}
log="k16-bu-$(date +%Y%m%d-%H%M%S).txt"
printf 'K16 dien tap staging: xem queue_age, http_burn va so job dang cho.\n'
read -r -p 'Nhan Enter khi san sang truc, he thong se bat dau dien tap: '
call ready -X POST | tee -a "$log"
printf '\n'
for ((i=0;i<37;i++)); do
  view="$(call state)"
  printf '%s %s\n' "$(date +%FT%T%z)" "$view" >> "$log"
  if [[ "$view" == *'"result":{'* ]]; then
    printf '%s\nPhien da ket thuc. Gui Kin %s\n' "$view" "$log"
    exit 1
  fi
  if [[ "$view" == *'"may_recover":true'* && "$view" == *'"recovery_requested":false'* ]]; then
    printf '%s\n' "$view"
    break
  fi
  sleep 3
done
if [[ "$view" != *'"may_recover":true'* ]]; then
  printf 'Chua nhan du canh bao. Gui Kin %s\n' "$log"
  exit 1
fi
printf 'Runbook: queue_age kem http_burn; worker staging dang dung, viec da nhan con cho. Chon khoi phuc worker va doi chieu job.\n'
while true; do
  action=""
  # The server enforces the recovery deadline while the operator types.
  read -r -p 'Sau khi doc so viec cho va tuoi hang doi, go dung KHOIPHUC: ' action || exit 1
  view="$(call state)"
  printf '%s %s\n' "$(date +%FT%T%z)" "$view" >> "$log"
  if [[ "$view" != *'"may_recover":true'* ]]; then
    printf 'Da het cua so khoi phuc. Gui lai tep %s\n' "$log"
    exit 1
  fi
  if [[ "$action" == KHOIPHUC ]]; then break; fi
  if [[ -n "$action" ]]; then printf 'Chua gui thao tac. Nhap lai KHOIPHUC.\n'; fi
done
call recover -X POST | tee -a "$log"
printf '\n'
for ((i=0;i<37;i++)); do
  view="$(call state)"
  printf '%s %s\n' "$(date +%FT%T%z)" "$view" >> "$log"
  if [[ "$view" == *'"result":{'* ]]; then
    printf '%s\nGui lai tep %s\n' "$view" "$log"
    exit 0
  fi
  sleep 3
done
printf 'Chua thay ket qua cuoi. Gui file %s\n' "$log"
exit 1