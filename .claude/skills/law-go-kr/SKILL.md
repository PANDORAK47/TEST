---
name: law-go-kr
description: 법제처 국가법령정보 OPEN API(DRF)로 법령·행정규칙을 검색하고, 본문에 딸린 별표·서식(HWP/HWPX/PDF 첨부파일)을 내려받아 파싱한다. 「식품의 기준 및 규격」처럼 본문이 비어있고 실제 내용이 첨부파일에 들어있는 고시를 다룰 때 사용. korean-doc-parser 스킬과 함께 동작한다.
---

# law-go-kr — 법제처 첨부파일 수집·파싱

법제처(www.law.go.kr) 국가법령정보 공동활용 OPEN API를 호출해:
1. 법령/행정규칙(고시)을 이름으로 **검색**하고,
2. 상세 응답에서 **별표·서식 첨부파일(HWP/HWPX/PDF) 링크를 추출**하고,
3. 파일을 **다운로드**한 뒤,
4. `korean-doc-parser` 스킬로 **텍스트를 파싱**한다.

## 왜 필요한가

「식품의 기준 및 규격」(식약처 고시) 같은 문서는 law.go.kr 본문 페이지가
> 『식품의 기준 및 규격』의 자세한 내용은 상단 메뉴 "첨부파일" 버튼을 이용하십시오.

로만 되어 있고 실제 규격·정의는 전부 별표 첨부파일(HWP/PDF)에 있다. 따라서
본문 크롤링이 아니라 **첨부파일 다운로드 + 파싱**이 필요하다.

## 사전 요건

1. **OC 인증키**: open.law.go.kr 에서 OPEN API를 신청하면 받는 아이디(보통
   가입 이메일 앞자리). 환경변수로 설정하고 코드/리포에는 남기지 않는다.
   ```bash
   export LAW_GO_KR_OC=your_oc_id
   ```
2. **네트워크**: 실행 환경이 `www.law.go.kr` 로 아웃바운드 HTTPS 가능해야 한다.
   (Claude Code on the web 의 기본 egress 정책은 이 호스트를 차단할 수 있으므로,
   막히면 environment 설정에서 허용하거나 law.go.kr 접근이 되는 로컬/서버에서 실행.)
3. **의존성**: `requests`, 그리고 파싱용으로 korean-doc-parser 스킬의 의존성
   (`hwp-hwpx-parser`, `pymupdf`, `python-docx`, `mammoth`, `pytesseract`,
   `paddleocr`).

## 사용법

```bash
S=.claude/skills/law-go-kr/scripts/law_fetch.py

# 1) 검색 → 행정규칙일련번호(ID) 확인
python3 $S search "식품의 기준 및 규격" --target admrul
#  2100000279602   식품의 기준 및 규격

# 2) 첨부파일(별표/서식) 링크 목록
python3 $S attachments 2100000279602 --target admrul

# 3) 첨부파일 전부 다운로드
python3 $S fetch 2100000279602 --target admrul --outdir ./out

# 4) 다운로드 + 파싱 + 키워드 필터 (예: 과자류 정의만)
python3 $S fetch 2100000279602 --target admrul --outdir ./out --parse --grep 과자류

# 이름으로 바로 검색→다운로드→파싱
python3 $S fetch --query "식품의 기준 및 규격" --target admrul --parse --grep 과자류
```

### target 값
| target | 대상 |
|---|---|
| `admrul` | 행정규칙(고시·훈령·예규) — 식약처 고시 등 |
| `law` | 법률·시행령·시행규칙 |
| `ordin` | 자치법규 |

## 동작 원리 (요약)

- 검색: `GET /DRF/lawSearch.do?OC=..&target=admrul&type=JSON&query=..`
- 상세: `GET /DRF/lawService.do?OC=..&target=admrul&type=JSON&ID=<seq>`
- 첨부 링크 추출: 상세 JSON을 재귀 탐색해 키에 `링크/파일/다운로드`가 있고
  값이 URL/경로인 항목을 모두 수집(별표 필드명이 버전마다 달라도 견고).
- 다운로드: Content-Disposition 파일명 우선, 없으면 매직바이트로 확장자 판별
  (PDF `%PDF`, HWP/DOC OLE2 `D0CF11E0`, HWPX/DOCX ZIP → 내부 구조로 구분).
- 파싱: `korean-doc-parser/scripts/parse_doc.py` 로 위임.

## 한계

- DRM/암호 걸린 첨부는 다운로드는 되어도 파싱 불가(FOSS 공통 한계).
- 별표가 스캔 이미지 PDF면 parse_doc.py 가 자동으로 OCR(PaddleOCR)로 폴백.
- 법정 정의처럼 정확도가 중요한 텍스트는 파싱 결과를 원본과 대조 확인할 것.
