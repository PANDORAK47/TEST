# 법제처 문서 수집·파싱 스킬

법제처 국가법령정보의 법령·고시 첨부파일(HWP/HWPX/PDF/DOC/DOCX)을
내려받아 텍스트로 파싱하기 위한 Claude Code 스킬 모음.

## 스킬

### `law-go-kr` — 법제처 OPEN API 클라이언트
법제처 DRF OPEN API로 법령/행정규칙을 검색하고, 별표·서식 첨부파일 링크를
추출해 다운로드한 뒤 파싱한다. 「식품의 기준 및 규격」처럼 본문이 비어있고
내용이 전부 첨부파일에 있는 고시를 다룰 때 필수.
자세한 사용법: `.claude/skills/law-go-kr/SKILL.md`

### `korean-doc-parser` — 한국어 문서 파서 + OCR
`.hwp/.hwpx/.pdf/.doc/.docx` 를 확장자별로 자동 분기해 텍스트 추출.
스캔 이미지 PDF는 한국어 OCR(PaddleOCR, Tesseract 백업)로 폴백.
자세한 사용법: `.claude/skills/korean-doc-parser/SKILL.md`

## 파싱 스택 선정 근거 (조사 결과)

| 분야 | 채택 | 이유 |
|---|---|---|
| HWP/HWPX | `hwp-hwpx-parser` | HWP5+HWPX 동시 지원, 순수 파이썬(JVM 불필요), 표/이미지/각주 지원 |
| PDF | `PyMuPDF` | 빠른 텍스트 추출, CJK 폰트 폴백 |
| DOCX/DOC | `python-docx` / `mammoth` | 표준·경량 |
| 한국어 OCR | `PaddleOCR` (+ Tesseract 백업) | 공개 FOSS 중 한국어 인식률 최상, CPU 실행 가능 |

## 설치

```bash
pip install -r .claude/skills/korean-doc-parser/requirements.txt
apt install -y tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng
export LAW_GO_KR_OC=your_oc_id   # open.law.go.kr 에서 발급
```

## 예시: 「식품의 기준 및 규격」에서 과자류 정의 찾기

```bash
S=.claude/skills/law-go-kr/scripts/law_fetch.py
python3 $S fetch --query "식품의 기준 및 규격" --target admrul --parse --grep 과자류
```

### 원클릭
```bash
export LAW_GO_KR_OC=khb
bash .claude/skills/law-go-kr/scripts/run_local.sh "식품의 기준 및 규격" 과자류
```

## 실행 환경 (A안 vs B안)

| 방법 | 상태 | 설명 |
|---|---|---|
| **A. Claude Code 웹 세션에서 직접** | ❌ 차단됨 | 이 세션의 egress 정책이 `www.law.go.kr` 등 모든 KR 법령/공공 API 호스트를 `403 CONNECT` 로 막는다. environment 설정에서 egress 허용 목록에 `www.law.go.kr` 을 추가해야 열린다(관리자 권한 필요). |
| **B. law.go.kr 접근 가능한 로컬/서버** | ✅ 사용 가능 | smting MCP가 OC=khb 로 이미 붙고 있는 Mac 등에서 위 원클릭 스크립트를 실행하면 바로 동작한다. **채택된 방법.** |

> 참고: 더 많은 기능(판례·조례·인용 환각 검증 등)이 필요하면 성숙한 오픈소스
> MCP [`chrisryugj/korean-law-mcp`](https://github.com/chrisryugj/korean-law-mcp)
> 를 함께 쓸 수 있다(별표 HWP/HWPX 추출 `get_annexes` 내장). 단, 이 MCP도
> 실행 환경이 law.go.kr 에 접근 가능해야 한다(A안 제약 동일).
