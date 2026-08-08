# Food Code Analyzer 🍽️
## 식품공전 4대 공전 통합 분석 스킬

**식품공전, 건강기능식품공전, 식품첨가물공전, 기구·용기·포장공전**을 자연어로 즉시 검색하는 Claude 스킬입니다.

---

## 설치

이미 `~/.claude/skills/food-code-analyzer/`에 설치되어 있습니다.

```bash
# 다음 세션부터 자동으로 로드됩니다.
# 또는 현재 세션에서 스킬을 명시적으로 호출:
python3 ~/.claude/skills/food-code-analyzer/scripts/analyzer.py "과자류 정의"
```

---

## 사용법

### 1️⃣ 기본 검색

**자동 공전 판별** — 질문만 하면 관련 공전을 자동으로 찾습니다:

```bash
# 과자류, 빵류, 떡류 정의
food-code "과자류 정의"
→ [식품공전] 과자, 캔디류, 추잉껌, 빵류, 떡류 정의 5개 출력

# 첨가물 검색
food-code "아스파탐 사용 기준"
→ [식품첨가물공전] 아스파탐 감미료 정의 + 사용 기준

# 건강기능식품
food-code "홍삼 기능성"
→ [건강기능식품공전] 홍삼의 면역력·피로 개선 기능성

# 용기·포장
food-code "폴리에틸렌 규격"
→ [기구·용기·포장공전] PE 용융지수, 색상 기준 출력
```

### 2️⃣ 공전 지정 검색

특정 공전만 검색하려면 `--codex` 옵션:

```bash
# 식품공전만
python3 ~/.claude/skills/food-code-analyzer/scripts/analyzer.py "우유" --codex food

# 식품첨가물공전만
python3 ~/.claude/skills/food-code-analyzer/scripts/analyzer.py "보존료" --codex additives

# 건강기능식품공전만
python3 ~/.claude/skills/food-code-analyzer/scripts/analyzer.py "오메가3" --codex health

# 기구·용기·포장공전만
python3 ~/.claude/skills/food-code-analyzer/scripts/analyzer.py "알루미늄" --codex equipment
```

### 3️⃣ 출력 형식

```bash
# 일반 텍스트 (기본값)
python3 ... "과자류" --format text

# JSON (프로그래밍용)
python3 ... "과자류" --format json

# 마크다운
python3 ... "과자류" --format markdown
```

---

## 포함 정의 & 기준

### 📖 식품공전 (food-codex.json)
- 과자류, 빵류, 떡류 (과자, 캔디류, 추잉껌, 빵류, 떡류)
- 빙과류 (아이스크림)
- 유제품 (우유, 요구르트)
- 육제품 (소시지)
- *확장 가능: 모든 식품유형 추가 가능*

### 💊 건강기능식품공전 (health-food-codex.json)
- 인삼류 (홍삼, 인삼)
- 루테인 (눈 건강)
- 오메가3 (심혈관 건강)
- 프로바이오틱스 (유산균, 장 건강)
- 비타민, 칼슘, 홍국, 화분
- *기능성·함량·사용 연령 기준 포함*

### 🧪 식품첨가물공전 (food-additives.json)
- 색소 (타르색소, 천연색소)
- 보존료 (소르빈산, 산화방지제)
- 감미료 (아스파탐, 스테비아, 자일리톨)
- 유화제 (레시틴, 모노글리세리드)
- 팽창제 (베이킹파우더, 암모니움탄산염)
- *사용 기준·사용 식품·함량 상한 포함*

### 📦 기구·용기·포장공전 (equipment-packaging.json)
- 플라스틱: PE, PP, PET, PVC
- 종이 (판지, 백판지)
- 유리
- 알루미늄 (호일, 용기)
- 스테인리스강 (SUS304, SUS316)
- *용융지수, 색상, 중금속 용출 기준 포함*

---

## 구조

```
~/.claude/skills/food-code-analyzer/
├── SKILL.md                      # 스킬 메타데이터
├── README.md                     # 이 파일
├── scripts/
│   ├── analyzer.py               # 분석 엔진 (Python)
│   └── data_manager.py           # 데이터 관리 (미포함, 향후 확장용)
└── data/
    ├── food-codex.json           # 식품공전 정의 및 기준
    ├── health-food-codex.json    # 건강기능식품공전
    ├── food-additives.json       # 식품첨가물공전
    └── equipment-packaging.json  # 기구·용기·포장공전
```

### 엔진 동작 (analyzer.py)

```
사용자 질의 입력
    ↓
query_classifier() — 공전 분류
  • 첨가물 → [첨가물공전, 식품공전]
  • 건강기능식품 → [건강기능식품공전, 첨가물공전]
  • 용기·포장 → [기구·용기·포장공전]
  • 정의 → [모든 공전]
    ↓
search_kb() — 지식베이스 검색
  • 4대 공전 JSON 데이터 검색
  • 텍스트 매칭 (향후 fuzzy match, 토큰화 개선 예정)
    ↓
format_output() — 결과 포맷팅
  • text: 가독성 중심
  • json: 프로그래밍 접근
  • markdown: 문서화용
```

---

## 확장

### 데이터 추가

각 `data/*.json` 파일의 `entries` 배열에 항목 추가:

```json
{
  "category": "분류",
  "term": "용어명",
  "type": "유형",
  "definition": "정의 또는 설명",
  "standard": "(선택) 기준·규격"
}
```

### 코드 개선

- **Fuzzy matching**: `fuzzywuzzy` 라이브러리로 오탈자 허용
- **LLM 통합**: Claude API로 자연어 요약
- **실시간 동기화**: korean-law-mcp로 최신 고시 자동 확인
- **웹 크롤링**: foodsafetykorea.go.kr 자동 파싱

---

## 출처

- [법제처 국가법령정보](https://law.go.kr/)
- [식품안전나라 식품공전](https://www.foodsafetykorea.go.kr/foodcode/)
- [식약처 정책정보](https://www.mfds.go.kr/)

---

## 라이선스

MIT

---

## 버전 히스토리

| 버전 | 날짜 | 변경사항 |
|---|---|---|
| 1.0 | 2026-07-15 | 초판 배포 — 4대 공전 통합, 텍스트 검색 |
| (계획) 1.1 | TBD | Fuzzy matching, LLM 요약 |
| (계획) 2.0 | TBD | korean-law-mcp 자동 동기화 |

