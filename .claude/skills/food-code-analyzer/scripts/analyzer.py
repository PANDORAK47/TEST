#!/usr/bin/env python3
"""
Food Code Analyzer — 4대 공전 통합 분석 엔진
식품공전, 건강기능식품공전, 식품첨가물공전, 기구·용기·포장공전 검색·분석
"""

import json
import sys
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

# 공전별 지식베이스 로드
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"

CODEX_FILES = {
    "food": DATA_DIR / "food-codex.json",
    "additives": DATA_DIR / "food-additives.json",
    "health": DATA_DIR / "health-food-codex.json",
    "equipment": DATA_DIR / "equipment-packaging.json",
}

class FoodCodeAnalyzer:
    def __init__(self):
        self.kb = {}  # knowledge base
        self.load_knowledge_base()

    def load_knowledge_base(self):
        """공전 데이터 로드"""
        for codex_type, filepath in CODEX_FILES.items():
            if filepath.exists():
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        self.kb[codex_type] = json.load(f)
                except Exception as e:
                    print(f"⚠️ {codex_type} 로드 실패: {e}", file=sys.stderr)
                    self.kb[codex_type] = {}
            else:
                self.kb[codex_type] = {}

    def classify_query(self, query: str) -> Tuple[str, List[str]]:
        """
        질의 타입 분류 및 관련 공전 선택
        Returns: (query_type, relevant_codices)
        - query_type: "definition" | "standard" | "additive" | "equipment" | "general"
        - relevant_codices: ["food", "additives", ...] 리스트
        """
        query_lower = query.lower()

        # 첨가물 관련
        if any(kw in query_lower for kw in ['첨가물', 'additive', '색소', '보존료', '감미료', '유화제']):
            return "additive", ["additives", "food"]

        # 건강기능식품 관련
        if any(kw in query_lower for kw in ['건강기능식품', 'health', '홍삼', '루테인', '프로바이오틱', '기능성']):
            return "health", ["health", "additives"]

        # 용기·포장 관련
        if any(kw in query_lower for kw in ['용기', '포장', '용기·포장', '기구', '플라스틱', '종이', '유리', 'equipment']):
            return "equipment", ["equipment", "food"]

        # 정의 관련
        if any(kw in query_lower for kw in ['정의', 'definition', '이란', '란 함은', '말한다']):
            return "definition", ["food", "health", "additives"]

        # 기준/규격 관련
        if any(kw in query_lower for kw in ['기준', '규격', 'standard', 'specification']):
            return "standard", ["food", "health", "additives", "equipment"]

        # 사용 가능 여부
        if any(kw in query_lower for kw in ['사용 가능', '허용', 'allowed', 'can use']):
            return "standard", ["additives", "food"]

        return "general", list(self.kb.keys())

    def search_kb(self, query: str, codices: List[str]) -> List[Dict]:
        """
        지식베이스에서 검색
        Returns: [{"codex": "food", "category": "과자류", "term": "과자", "definition": "..."}, ...]
        """
        results = []
        query_terms = set(query.split())

        for codex_type in codices:
            if codex_type not in self.kb:
                continue

            kb_entries = self.kb[codex_type].get("entries", [])

            for entry in kb_entries:
                # 간단한 텍스트 매칭 (향후 fuzzy match 개선 가능)
                entry_text = json.dumps(entry, ensure_ascii=False).lower()

                # 모든 쿼리 항목이 포함되어 있으면 매칭
                if all(term.lower() in entry_text for term in query_terms):
                    results.append({
                        "codex": codex_type,
                        "codex_name": {
                            "food": "식품공전",
                            "additives": "식품첨가물공전",
                            "health": "건강기능식품공전",
                            "equipment": "기구·용기·포장공전"
                        }.get(codex_type, codex_type),
                        **entry
                    })

        return results

    def format_output(self, results: List[Dict], format_type: str = "text") -> str:
        """결과 포맷팅"""
        if not results:
            return "❌ 검색 결과 없음. 다시 시도해주세요.\n\n💡 팁:\n- '과자류 정의'\n- '아스파탐 사용 기준'\n- '건강기능식품 홍삼'\n- '폴리에틸렌 용기 규격'"

        if format_type == "json":
            return json.dumps(results, ensure_ascii=False, indent=2)

        # Text format (default)
        output = []
        for i, result in enumerate(results, 1):
            codex = result.get('codex_name', result.get('codex'))
            category = result.get('category', '')
            term = result.get('term', '')
            definition = result.get('definition', '')

            output.append(f"\n🔍 결과 {i}. [{codex}]")
            if category:
                output.append(f"   분류: {category}")
            if term:
                output.append(f"   용어: {term}")
            if definition:
                output.append(f"   정의: {definition}")

        return "\n".join(output) if output else "검색 결과 없음"

    def analyze(self, query: str, codex_filter: str = None, format_type: str = "text") -> str:
        """통합 분석"""
        query_type, relevant_codices = self.classify_query(query)

        # codex_filter 지정시 우선
        if codex_filter:
            relevant_codices = [codex_filter] if codex_filter in self.kb else relevant_codices

        results = self.search_kb(query, relevant_codices)
        output = self.format_output(results, format_type)

        return output

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Food Code Analyzer")
    parser.add_argument("query", help="검색할 용어 또는 질문")
    parser.add_argument("--codex", choices=["food", "additives", "health", "equipment"],
                        help="특정 공전 지정")
    parser.add_argument("--format", choices=["text", "json", "markdown"], default="text",
                        help="출력 형식")

    args = parser.parse_args()

    analyzer = FoodCodeAnalyzer()
    result = analyzer.analyze(args.query, codex_filter=args.codex, format_type=args.format)
    print(result)

if __name__ == "__main__":
    main()
