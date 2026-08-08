---
name: law-go-kr
description: 법제처 국가법령정보 OPEN API(DRF)로 법령·행정규칙을 검색하고, 별표·서식(HWP/HWPX/PDF 첨부파일)을 번호로 지정해 내려받아 파싱한다. 조문 본문을 조/항/호/목으로 구조화하고 food-code-analyzer 지식베이스로 내보낼 수 있다. 「식품의 기준 및 규격」처럼 본문이 비어있고 실제 내용이 첨부파일에 들어있는 고시를 다룰 때 사용. korean-doc-parser 스킬과 함께 동작한다.
---

# law-go-kr — 법제처 법령·별표 수집·파싱

법제처(www.law.go.kr) 국가법령정보 공동활용 OPEN API 를 호출해:

1. 법령/행정규칙(고시)을 이름으로 **검색**하고,
2. **별표 번호를 지정해**(예: `--byl "별표 4"`) 해당 첨부파일만 골라내고,
3. 파일을 **다운로드**한 뒤 `korean-doc-parser` 로 **파싱**하고,
4. 조문 본문을 **조/항/호/목 트리로 구조화**해 text/JSON/Markdown 으로 출력하거나
5. `food-code-analyzer` **지식베이스로 내보낸다**.

## 왜 필요한가

「식품의 기준 및 규격」(식약처 고시) 같은 문서는 law.go.kr 본문 페이지가
> 『식품의 기준 및 규격』의 자세한 내용은 상단 메뉴 "첨부파일" 버튼을 이용하십시오.

로만 되어 있고 실제 규격·정의는 전부 별표 첨부파일(HWP/PDF)에 있다.
따라서 본문 크롤링이 아니라 **첨부파일 다운로드 + 파싱**이 필요하다.

## 구조

```
law-go-kr/
├── SKILL.md
├── scripts/
│   ├── lawapi.py     HTTP 계층 — 재시도·타임아웃·디스크 캐시·오류 구분
│   ├── parsers.py    파싱 계층 — 별표 참조·조문 트리·출력 포맷 (순수 함수)
│   ├── law_fetch.py  CLI 와 오케스트레이션
│   └── run_local.sh  원클릭 실행
└── tests/            네트워크 없이 도는 단위·통합 테스트 (75개)
```

## 사전 요건

1. **OC 인증키**: open.law.go.kr 에서 OPEN API 를 신청하면 받는 아이디.
   환경변수로 설정하고 코드/리포에는 남기지 않는다.
   ```bash
   export LAW_GO_KR_OC=your_oc_id     # 또는 --oc 옵션
   ```
2. **네트워크**: 실행 환경이 `www.law.go.kr` 로 아웃바운드 HTTPS 가능해야 한다.
   Claude Code on the web 의 기본 egress 정책은 이 호스트를 차단하므로,
   Environments → Custom network 에 `www.law.go.kr`, `open.law.go.kr` 을 허용하거나
   접근 가능한 로컬/서버에서 실행한다. 차단 시 스크립트가 원인과 해결법을 안내한다.
3. **의존성**: `requests` + korean-doc-parser 스킬의 파싱 스택
   (`hwp-hwpx-parser`, `pymupdf`, `python-docx`, `mammoth`, `paddleocr`, `pytesseract`).

## 사용법

```bash
S=.claude/skills/law-go-kr/scripts/law_fetch.py

# 1) 검색 → 행정규칙일련번호 확인
python3 $S search "식품등의 표시기준"

# 2) 별표·서식 목록 (번호 인식 결과 포함)
python3 $S annexes --query "식품등의 표시기준"
#  별표 4    [HWP/기타]  영양성분 표시대상 식품    https://...
#  별표 4    [PDF]       영양성분 표시대상 식품    https://...
#  별표 4의2 [HWP/기타]  추가 기준                https://...

# 3) '별표 4' 만 내려받아 파싱
python3 $S fetch --query "식품등의 표시기준" --byl "별표 4" --parse

# 4) 조문 본문을 구조화해 마크다운으로
python3 $S articles --query "식품위생법" --target law --article 1-5 --format markdown

# 5) 지식베이스로 내보내기 (먼저 --dry-run 으로 확인)
python3 $S export --query "식품의 기준 및 규격" --codex food --dry-run
python3 $S export --query "식품의 기준 및 규격" --codex food

# 6) 캐시 확인/삭제
python3 $S cache info
python3 $S cache clear
```

### 원클릭(로컬 실행)
```bash
export LAW_GO_KR_OC=your_oc_id
bash .claude/skills/law-go-kr/scripts/run_local.sh "식품등의 표시기준" "" "별표 4"
```

## 별표 지정 문법 (`--byl`)

| 입력 | 뜻 |
|---|---|
| `별표 4` / `별표4` / `별표 제4호` | 별표 4 (가지번호 없는 것만) |
| `별표 4의2` | 별표 4의2 만 |
| `4` | 종류 무관, 번호 4 (별표·별지·서식 모두) |
| `별지 1` | 별지 1 |
| `별표 4,별표 5` | 여러 개 |

가지번호는 **정확히** 비교한다 — `별표 4` 를 요청하면 `별표 4의2` 는 따라오지 않는다.
못 찾으면 인식된 별표 목록을 보여주므로 오타를 바로 알 수 있다.

## 조문 지정 문법 (`--article`)

`3` (제3조) · `3의2` (제3조의2) · `1-5` (범위) · `1,3,5` (여러 개)

## 공통 옵션

| 옵션 | 설명 |
|---|---|
| `--format text\|json\|markdown` | 출력 형식 |
| `--target law\|admrul\|ordin` | 법령 / 행정규칙(고시) / 자치법규 |
| `--via detail\|admbyl` | 본문에서 별표 링크 추출 / 별표 목록 직접 조회 |
| `--refresh` | 캐시 무시하고 새로 받기 |
| `--no-cache` | 캐시를 읽지도 쓰지도 않음 |
| `--ttl 86400` | 캐시 유효기간(초) |
| `--retries 4` | 실패 시 재시도 횟수(지수 백오프) |
| `--connect-timeout` / `--read-timeout` | 타임아웃 분리 |
| `-q, --quiet` | 진행 로그 숨김 |

## 동작 원리

- 검색: `GET /DRF/lawSearch.do?OC=..&target=admrul&type=JSON&query=..`
- 상세: `GET /DRF/lawService.do?OC=..&target=admrul&type=JSON&ID=<seq>`
- 별표 링크 추출: 응답 JSON 을 재귀 탐색해 키에 `링크/파일/다운로드`가 있고 값이
  URL/경로인 항목을 수집(필드명이 버전마다 달라도 견고). 별표 번호는 `별표번호`·
  `별표가지번호`·`별표종류` 필드를 우선 쓰고, 없으면 제목에서 파싱한다.
- 본문에서 별표를 못 찾으면 `target=admbyl` 직접 조회로 자동 폴백한다.
- 다운로드: Content-Disposition 파일명 우선, 없으면 매직바이트로 확장자 판별
  (PDF `%PDF`, HWP/DOC OLE2 `D0CF11E0`, HWPX/DOCX ZIP → 내부 구조로 구분).
  한 번의 실행 안에서 이름이 겹치면 `(2)` 를 붙여 덮어쓰지 않는다.
- 캐시: URL+파라미터(OC 제외) 해시로 `~/.cache/law-go-kr/` 에 저장, 기본 TTL 24시간.
- 파싱: `korean-doc-parser/scripts/parse_doc.py` 로 위임.

## 테스트

네트워크 없이 전부 돈다 — egress 가 막힌 환경에서도 로직 검증이 가능하다.

```bash
cd .claude/skills/law-go-kr && python3 -m unittest discover -s tests -t tests
# Ran 75 tests — OK
```

## 한계

- DRM/암호 걸린 첨부는 다운로드는 되어도 파싱 불가(FOSS 공통 한계).
- 별표가 스캔 이미지 PDF 면 parse_doc.py 가 자동으로 OCR(PaddleOCR)로 폴백한다.
- 조문 구조화는 법제처가 주는 형태에 따라 결과가 달라진다. `조문단위` 배열이 있으면
  그대로 쓰고, 텍스트 blob 이면 정규식으로 파싱하므로 완벽하지 않을 수 있다.
- **법정 정의처럼 정확도가 중요한 텍스트는 파싱 결과를 원본과 대조 확인할 것.**
