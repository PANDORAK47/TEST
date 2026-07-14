#!/usr/bin/env bash
# law.go.kr 접근이 가능한 로컬/서버(B안)에서 실행하는 원클릭 스크립트.
#
# 사용법:
#   export LAW_GO_KR_OC=khb          # 법제처 OPEN API 인증키
#   bash run_local.sh "식품의 기준 및 규격" 과자류
#
#   인자1: 검색할 법령/고시명 (기본: "식품의 기준 및 규격")
#   인자2: 파싱 결과에서 필터링할 키워드 (기본: 과자류)  ""이면 전체 출력
set -euo pipefail

QUERY="${1:-식품의 기준 및 규격}"
KEYWORD="${2:-과자류}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${OUT:-./law_attachments}"

if [[ -z "${LAW_GO_KR_OC:-}" ]]; then
  echo "먼저 인증키를 설정하세요:  export LAW_GO_KR_OC=khb" >&2
  exit 1
fi

echo "== 1) 검색: $QUERY ==" >&2
python3 "$DIR/law_fetch.py" search "$QUERY" --target admrul

echo >&2
echo "== 2) 첨부파일 다운로드 + 파싱${KEYWORD:+ (키워드: $KEYWORD)} ==" >&2
if [[ -n "$KEYWORD" ]]; then
  python3 "$DIR/law_fetch.py" fetch --query "$QUERY" --target admrul \
          --outdir "$OUT" --parse --grep "$KEYWORD"
else
  python3 "$DIR/law_fetch.py" fetch --query "$QUERY" --target admrul \
          --outdir "$OUT" --parse
fi
