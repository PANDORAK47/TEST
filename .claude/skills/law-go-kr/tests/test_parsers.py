#!/usr/bin/env python3
"""parsers.py / lawapi.py 의 순수 함수 단위 테스트.

네트워크를 타지 않으므로 egress 가 막힌 환경에서도 전부 실행된다.
  python3 -m unittest discover -s .claude/skills/law-go-kr/tests -v
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from parsers import (  # noqa: E402
    BylRef,
    articles_to_kb_entries,
    articles_to_markdown,
    articles_to_text,
    byl_matches,
    byl_ref_from_item,
    extract_articles,
    filter_articles,
    merge_kb,
    para_label,
    parse_articles,
    parse_byl_ref,
    parse_byl_spec,
    parse_byl_specs,
    render,
    strip_markup,
)

SAMPLE = """제1조(목적) 이 고시는 식품의 기준 및 규격에 관한 사항을 정함을 목적으로 한다.
제2조(정의) 이 고시에서 사용하는 용어의 뜻은 다음과 같다.
① "과자류"란 곡분, 전분 등을 주원료로 하여 제조·가공한 것을 말한다.
1. 과자
2. 캔디류
가. 하드캔디
나. 소프트캔디
② 제1항에도 불구하고 다음 각 호는 제외한다.
제3조의2(특례) 배추김치는 별도의 기준을 적용한다.
"""


class TestBylSpec(unittest.TestCase):
    def test_kind_and_number(self):
        self.assertEqual(parse_byl_spec("별표 4"), BylRef("별표", 4, None))
        self.assertEqual(parse_byl_spec("별표4"), BylRef("별표", 4, None))
        self.assertEqual(parse_byl_spec("별표 제4호"), BylRef("별표", 4, None))
        self.assertEqual(parse_byl_spec("별지 1"), BylRef("별지", 1, None))

    def test_branch_number(self):
        self.assertEqual(parse_byl_spec("별표 4의2"), BylRef("별표", 4, 2))
        self.assertEqual(parse_byl_spec("4의2"), BylRef(None, 4, 2))

    def test_bare_number_is_wildcard_kind(self):
        self.assertEqual(parse_byl_spec("4"), BylRef(None, 4, None))

    def test_multiple_specs(self):
        got = parse_byl_specs("별표 4, 별표 5")
        self.assertEqual(got, [BylRef("별표", 4, None), BylRef("별표", 5, None)])

    def test_invalid_spec_raises(self):
        with self.assertRaises(ValueError):
            parse_byl_spec("영양성분")
        with self.assertRaises(ValueError):
            parse_byl_spec("")


class TestBylRefParsing(unittest.TestCase):
    def test_from_bracketed_title(self):
        self.assertEqual(
            parse_byl_ref("[별표 4] 영양성분 표시대상 식품"), BylRef("별표", 4, None)
        )
        self.assertEqual(parse_byl_ref("[별표 4의2] 추가 기준"), BylRef("별표", 4, 2))
        self.assertEqual(
            parse_byl_ref("[별지 1] 표시사항별 세부표시기준"), BylRef("별지", 1, None)
        )

    def test_no_reference_returns_none(self):
        self.assertIsNone(parse_byl_ref("영양성분 표시대상 식품"))
        self.assertIsNone(parse_byl_ref(""))

    def test_from_api_item_with_explicit_fields(self):
        item = {
            "별표종류": "별표",
            "별표번호": "4",
            "별표가지번호": "0",
            "별표명": "영양성분 표시대상 식품",
        }
        self.assertEqual(byl_ref_from_item(item), BylRef("별표", 4, None))

    def test_from_api_item_falls_back_to_title(self):
        item = {"별표명": "[별표 4의2] 추가 기준", "별표서식파일링크": "/DRF/x.do"}
        self.assertEqual(byl_ref_from_item(item), BylRef("별표", 4, 2))


class TestBylMatching(unittest.TestCase):
    def test_bare_number_matches_any_kind(self):
        self.assertTrue(byl_matches(BylRef(None, 4, None), BylRef("별표", 4, None)))
        self.assertTrue(byl_matches(BylRef(None, 4, None), BylRef("별지", 4, None)))

    def test_kind_must_match_when_given(self):
        self.assertFalse(byl_matches(BylRef("별지", 1, None), BylRef("별표", 1, None)))

    def test_branch_is_exact(self):
        # '별표 4' 요청이 '별표 4의2' 를 조용히 집어오면 안 된다
        self.assertFalse(byl_matches(BylRef("별표", 4, None), BylRef("별표", 4, 2)))
        self.assertTrue(byl_matches(BylRef("별표", 4, 2), BylRef("별표", 4, 2)))

    def test_none_ref_never_matches(self):
        self.assertFalse(byl_matches(BylRef("별표", 4, None), None))


class TestArticleParsing(unittest.TestCase):
    def setUp(self):
        self.arts = parse_articles(SAMPLE)

    def test_article_count_and_numbers(self):
        self.assertEqual(len(self.arts), 3)
        self.assertEqual([a["번호"] for a in self.arts], ["1", "2", "3"])

    def test_titles(self):
        self.assertEqual([a["제목"] for a in self.arts], ["목적", "정의", "특례"])

    def test_branch_article(self):
        self.assertEqual(self.arts[2]["가지"], "2")

    def test_paragraphs(self):
        a2 = self.arts[1]
        self.assertEqual(len(a2["항"]), 2)
        self.assertEqual(a2["항"][0]["번호"], 1)
        self.assertEqual(a2["항"][1]["번호"], 2)
        self.assertIn("과자류", a2["항"][0]["내용"])

    def test_items_and_subitems(self):
        ho = self.arts[1]["항"][0]["호"]
        self.assertEqual([h["번호"] for h in ho], ["1", "2"])
        self.assertEqual(ho[0]["내용"], "과자")
        mok = ho[1]["목"]
        self.assertEqual([m["번호"] for m in mok], ["가", "나"])
        self.assertEqual(mok[0]["내용"], "하드캔디")

    def test_unstructured_text_returns_empty(self):
        self.assertEqual(parse_articles("그냥 줄글입니다.\n조문이 아닙니다."), [])

    def test_continuation_line_appends(self):
        arts = parse_articles("제1조(목적) 첫 줄\n이어지는 둘째 줄")
        self.assertIn("이어지는 둘째 줄", arts[0]["내용"])


class TestStripMarkup(unittest.TestCase):
    def test_cdata_and_tags(self):
        self.assertEqual(strip_markup("<![CDATA[제1조]]>"), "제1조")
        self.assertEqual(strip_markup("<p>본문</p>"), "본문")
        self.assertEqual(strip_markup("a &lt;b&gt; c"), "a <b> c")

    def test_empty(self):
        self.assertEqual(strip_markup(""), "")


class TestArticleFilter(unittest.TestCase):
    def setUp(self):
        self.arts = parse_articles(SAMPLE)

    def test_single(self):
        got = filter_articles(self.arts, "2")
        self.assertEqual([a["번호"] for a in got], ["2"])

    def test_branch(self):
        got = filter_articles(self.arts, "3의2")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["가지"], "2")

    def test_range(self):
        got = filter_articles(self.arts, "1-2")
        self.assertEqual([a["번호"] for a in got], ["1", "2"])

    def test_none_spec_returns_all(self):
        self.assertEqual(len(filter_articles(self.arts, None)), 3)


class TestExtractArticles(unittest.TestCase):
    def test_structured_json_units(self):
        detail = {
            "법령": {
                "조문": {
                    "조문단위": [
                        {
                            "조문번호": "1",
                            "조문제목": "목적",
                            "조문내용": "제1조(목적) 이 법은 ...",
                            "항": [{"항번호": "①", "항내용": "첫째 항"}],
                        }
                    ]
                }
            }
        }
        arts = extract_articles(detail)
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["번호"], "1")
        self.assertEqual(arts[0]["제목"], "목적")
        self.assertEqual(arts[0]["항"][0]["내용"], "첫째 항")

    def test_text_blob_fallback(self):
        detail = {"행정규칙": {"조문내용": SAMPLE}}
        arts = extract_articles(detail)
        self.assertEqual([a["번호"] for a in arts], ["1", "2", "3"])

    def test_empty_detail(self):
        self.assertEqual(extract_articles({}), [])


class TestFormatters(unittest.TestCase):
    def setUp(self):
        self.arts = parse_articles(SAMPLE)

    def test_text_contains_hierarchy(self):
        out = articles_to_text(self.arts)
        self.assertIn("제1조(목적)", out)
        self.assertIn("제3조의2(특례)", out)
        self.assertIn("하드캔디", out)

    def test_paragraph_uses_circled_numbers(self):
        # 항은 ①, 호는 '1.' 이어야 두 층이 눈으로 구분된다
        out = articles_to_text(self.arts)
        self.assertIn("① ", out)
        self.assertIn("② ", out)
        self.assertIn("1. 과자", out)

    def test_para_label_variants(self):
        self.assertEqual(para_label(1), "①")
        self.assertEqual(para_label("2"), "②")
        self.assertEqual(para_label("①"), "①")  # JSON 응답이 이미 원문자인 경우
        self.assertEqual(para_label(None), "")
        self.assertEqual(para_label(99), "(99)")  # 원문자 범위 밖

    def test_markdown_has_headings(self):
        out = articles_to_markdown(self.arts, title="식품의 기준 및 규격")
        self.assertTrue(out.startswith("# 식품의 기준 및 규격"))
        self.assertIn("## 제1조 (목적)", out)

    def test_render_json_roundtrip(self):
        out = render(self.arts, "json")
        self.assertEqual(json.loads(out)[0]["제목"], "목적")

    def test_render_dispatches_by_format(self):
        self.assertIn("## ", render(self.arts, "markdown"))
        self.assertIn("제1조", render(self.arts, "text"))


class TestKbExport(unittest.TestCase):
    def setUp(self):
        self.arts = parse_articles(SAMPLE)

    def test_entries_shape(self):
        entries = articles_to_kb_entries(self.arts, "식품공전", "고시 제2026-1호")
        self.assertTrue(entries)
        e = entries[0]
        for key in ("category", "term", "type", "definition", "standard", "source"):
            self.assertIn(key, e)
        self.assertEqual(e["category"], "식품공전")
        self.assertEqual(e["standard"], "제1조")

    def test_merge_adds_new(self):
        existing = {"entries": [{"term": "기존", "category": "식품공전"}]}
        merged, added, updated = merge_kb(existing, [{"term": "신규", "category": "식품공전"}])
        self.assertEqual((added, updated), (1, 0))
        self.assertEqual(len(merged["entries"]), 2)

    def test_merge_updates_existing(self):
        existing = {"entries": [{"term": "정의", "category": "식품공전", "definition": "old"}]}
        merged, added, updated = merge_kb(
            existing, [{"term": "정의", "category": "식품공전", "definition": "new"}]
        )
        self.assertEqual((added, updated), (0, 1))
        self.assertEqual(merged["entries"][0]["definition"], "new")

    def test_merge_into_empty(self):
        merged, added, updated = merge_kb({}, [{"term": "a", "category": "c"}])
        self.assertEqual((added, updated), (1, 0))


class TestCacheKey(unittest.TestCase):
    def test_oc_excluded_from_key(self):
        from lawapi import Cache

        a = Cache.key("https://x/y", {"OC": "alice", "query": "식품"})
        b = Cache.key("https://x/y", {"OC": "bob", "query": "식품"})
        self.assertEqual(a, b)

    def test_different_query_differs(self):
        from lawapi import Cache

        a = Cache.key("https://x/y", {"query": "식품"})
        b = Cache.key("https://x/y", {"query": "의약품"})
        self.assertNotEqual(a, b)

    def test_param_order_irrelevant(self):
        from lawapi import Cache

        a = Cache.key("https://x/y", {"query": "식품", "display": 20})
        b = Cache.key("https://x/y", {"display": 20, "query": "식품"})
        self.assertEqual(a, b)


class TestApiErrorEnvelope(unittest.TestCase):
    """법제처는 인증 실패도 HTTP 200 + JSON 으로 준다. 이걸 '결과 없음'으로
    보여주면 고칠 수 있는 문제가 묻힌다 — 실제 호출에서 발견된 버그의 회귀 테스트."""

    def setUp(self):
        from lawapi import ApiResponseError, check_api_error

        self.check = check_api_error
        self.Err = ApiResponseError

    def test_auth_failure_raises_with_guidance(self):
        payload = {
            "result": "사용자 정보 검증에 실패하였습니다.",
            "msg": "OPEN API 호출 시 사용자 검증을 위하여 정확한 서버장비의 IP주소 및 도메인주소를 등록해 주세요.",
        }
        with self.assertRaises(self.Err) as cm:
            self.check(payload, "https://www.law.go.kr/DRF/lawSearch.do")
        msg = str(cm.exception)
        self.assertIn("사용자 정보 검증에 실패", msg)
        self.assertIn("open.law.go.kr", msg)  # 해결 방법을 알려줘야 한다
        self.assertIn("lawSearch.do", msg)

    def test_legit_search_response_passes(self):
        self.check({"AdmRulSearch": {"admrul": [{"행정규칙명": "식품등의 표시기준"}]}})

    def test_legit_service_response_passes(self):
        self.check({"행정규칙": {"행정규칙명": "식품등의 표시기준", "조문내용": "제1조..."}})

    def test_non_dict_passes(self):
        self.check([])
        self.check("문자열")
        self.check(None)

    def test_empty_result_field_passes(self):
        self.check({"result": "", "other": 1})


class TestCacheEvict(unittest.TestCase):
    def test_evict_removes_body_and_headers(self):
        import tempfile
        from pathlib import Path as P

        from lawapi import Cache

        with tempfile.TemporaryDirectory() as td:
            c = Cache(P(td))
            c.put("k1", b"data", {"Content-Type": "application/json"})
            self.assertIsNotNone(c.get("k1"))
            c.evict("k1")
            self.assertIsNone(c.get("k1"))

    def test_evict_missing_key_is_safe(self):
        import tempfile
        from pathlib import Path as P

        from lawapi import Cache

        with tempfile.TemporaryDirectory() as td:
            Cache(P(td)).evict("nonexistent")  # 예외가 나면 안 된다


if __name__ == "__main__":
    unittest.main(verbosity=2)
