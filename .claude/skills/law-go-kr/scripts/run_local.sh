#!/usr/bin/env bash
# law.go.kr 접근이 가능한 로컬/서버에서 실행하는 원클릭 스크립트.
#
# 사용법:
#   export LAW_GO_KR_OC=your_oc_id
#   bash run_local.sh "식품의 기준 및 규격" 과자류
#   bash run_local.sh "식품등의 표시기준" "" "별표 4"     # 별표 4만 받기
#
#   인자1: 검색할 법령/고시명 (기본: "식품의 기준 및 규격")
#   인자2: 파싱 결과에서 필터링할 키워드 (기본: 과자류)  ""이면 전체 출력
#   인자3: 받을 별표 지정 (예: "별표 4", "4", "별표 4,별표 5")  비우면 전체
#
# 환경변수:
#   OUT=./law_attachments   다운로드 폴더
#   FORMAT=text|json        출력 형식
set -euo pipefail

QUERY="${1:-식품의 기준 및 규격}"
KEYWORD="${2:-과자류}"
BYL="${3:-}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${OUT:-./law_attachments}"
FORMAT="${FORMAT:-text}"

if [[ -z "${LAW_GO_KR_OC:-}" ]]; then
  echo "먼저 인증키를 설정하세요:  export LAW_GO_KR_OC=your_oc_id" >&2
  echo "  (open.law.go.kr 에서 OPEN API 신청 시 발급)" >&2
  exit 1
fi

FETCH=("python3" "$DIR/law_fetch.py")

echo "== 1) 검색: $QUERY ==" >&2
"${FETCH[@]}" search "$QUERY" --target admrul

echo >&2
echo "== 2) 별표·서식 목록 ==" >&2
"${FETCH[@]}" annexes --query "$QUERY" --target admrul || true

echo >&2
echo "== 3) 다운로드 + 파싱${BYL:+ (별표: $BYL)}${KEYWORD:+ (키워드: $KEYWORD)} ==" >&2
args=(fetch --query "$QUERY" --target admrul --outdir "$OUT" --parse --format "$FORMAT")
[[ -n "$BYL" ]] && args+=(--byl "$BYL")
[[ -n "$KEYWORD" ]] && args+=(--grep "$KEYWORD")
"${FETCH[@]}" "${args[@]}"
