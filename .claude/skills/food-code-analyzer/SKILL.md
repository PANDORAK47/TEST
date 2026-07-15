---
name: food-code-analyzer
description: 식품공전·건강기능식품공전·식품첨가물공전·기구용기포장공전 4대 공전 통합 검색·분석. LLM 기반 자연어 질의로 정의·기준·규격·용어를 즉시 조회.
license: MIT
---

# Food Code Analyzer — 4대 공전 통합 분석기

**「식품의 기준 및 규격」, 「건강기능식품공전」, 「식품첨가물공전」, 「기구 및 용기·포장공전」**의 정의·기준·규격을 자연어로 즉시 조회합니다.

## 사용법

```bash
# 1. 과자류 정의 조회
food-code "과자류 정의"

# 2. 과자에 사용 가능한 첨가물
food-code "과자에 사용 가능한 식품첨가물"

# 3. 건강기능식품 인삼의 기준
food-code "건강기능식품 인삼 기준"

# 4. 폴리에틸렌 용기 규격
food-code "폴리에틸렌 식품용 용기 규격"

# 5. 고급 검색 (공전 지정)
food-code --codex food "과자류"
food-code --codex additives "아스파탐"
food-code --codex health "홍삼"
food-code --codex equipment "플라스틱 용기"
```

## 동작 원리

1. **자연어 질의 분석** — LLM이 질문을 이해하고 관련 공전 식별
2. **4대 공전 지식베이스 검색** — 정의·기준·규격 조회
3. **korean-law-mcp 연동** — 최신 고시·행정규칙 실시간 확인 (네트워크 허용 시)
4. **결과 정렬·요약** — 신뢰도 순 출력

## 포함 범위

| 공전 | 담당 부처 | 포함 항목 |
|---|---|---|
| **식품공전** | 식약처 | 과자류, 빵류, 떡류, 유제품, 곡류가공품, 육제품 등 |
| **건강기능식품공전** | 식약처 | 홍삼, 인삼, 루테인, 프로바이오틱스 등 기능성 식재 |
| **식품첨가물공전** | 식약처 | 색소, 보존료, 감미료, 유화제, 팽창제 등 허용 첨가물 |
| **기구·용기·포장공전** | 식약처 | 플라스틱, 종이, 유리, 알루미늄, 스테인리스 규격 |

## 출처

- [법제처 국가법령정보 — 식품의 기준 및 규격](https://law.go.kr/행정규칙/식품의기준및규격/)
- [식품안전나라 식품공전](https://www.foodsafetykorea.go.kr/foodcode/)
- [식약처 정책정보 — 공전](https://www.mfds.go.kr/wpge/m_510/de050301l002.do)

---

## 스킬 내부 구조

```
food-code <query> [--codex {food|additives|health|equipment}] [--format {text|json|markdown}]
↓
analyzer.py
├── query_classifier() — 질의 타입 식별 (정의 vs 기준 vs 규격)
├── codex_selector() — 관련 공전 선택
├── knowledge_search() — 지식베이스 검색
├── llm_summarize() — LLM으로 요약·정렬
└── format_output() — 형식별 출력
```
