#!/usr/bin/env python3
"""law_fetch.py 오케스트레이션 통합 테스트 (네트워크 없이 가짜 클라이언트 사용).

실제 law.go.kr 응답 형태를 흉내 낸 JSON 으로 배선(wiring)을 검증한다.
"""
import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from law_fetch import (  # noqa: E402
    apply_byl_filter,
    collect_attachments,
    detail_title,
    extract_attachments,
    item_name,
    item_seq,
    safe_filename,
    search_items,
    sniff_ext,
)
from parsers import BylRef  # noqa: E402

# --- 실제 응답 형태를 본뜬 픽스처 ---------------------------------------------

# 실제 law.go.kr 응답에서 그대로 옮긴 픽스처.
# 핵심: 별표번호가 "000100"(=별표 1) 처럼 0 으로 채워져 오고, 끝 두 자리가
# 가지번호다. 그리고 소관부처명이 별표명보다 앞에 온다(제목 오인 유발).
ADMBYL_SEARCH = {
    "admRulBylSearch": {
        "키워드": "식품등의 표시기준",
        "page": "1",
        "target": "admbyl",
        "totalCnt": "3",
        "section": "admBylNm",
        "admrulbyl": [
            {
                "별표행정규칙상세링크": "/DRF/lawService.do?OC=x&target=admbyl&ID=2823433&type=HTML&mobileYn=",
                "발령일자": "20241017",
                "관련법령ID": "75449",
                "소관부처명": "식품의약품안전처",
                "행정규칙종류": "고시",
                "별표서식파일링크": "/LSW/flDownload.do?flSeq=144836895",
                "관련행정규칙명": "부당한 표시 또는 광고로 보지 아니하는 식품등의 기능성 표시 또는 광고에 관한 규정",
                "id": "1",
                "별표일련번호": "2823433",
                "별표종류": "별표",
                "별표번호": "000100",
                "별표명": "기능성 표시 식품등의 영양성분 함량 기준(제3조제2항제2호 관련)",
                "관련행정규칙일련번호": "2100000248274",
                "발령번호": "2024-62",
            },
            {
                "별표행정규칙상세링크": "/DRF/lawService.do?OC=x&target=admbyl&ID=3225591&type=HTML&mobileYn=",
                "소관부처명": "식품의약품안전처",
                "별표서식파일링크": "/LSW/flDownload.do?flSeq=164241631",
                "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=164241632",
                "id": "2",
                "별표종류": "별표",
                "별표번호": "000400",
                "별표명": "식품이력추적관리 또는 수입식품등의 유통이력추적관리의 표시기준(제6조 관련)",
                "관련행정규칙일련번호": "2100000279276",
            },
            {
                "소관부처명": "식품의약품안전처",
                "별표서식파일링크": "/LSW/flDownload.do?flSeq=164241633",
                "id": "3",
                "별표종류": "별표",
                "별표번호": "000402",
                "별표명": "가지번호가 붙은 별표",
            },
        ],
    }
}

ADMRUL_SEARCH = {
    "AdmRulSearch": {
        "admrul": [
            {"행정규칙명": "식품등의 표시기준", "행정규칙일련번호": "2100000279602"},
            {"행정규칙명": "식품의 기준 및 규격", "행정규칙일련번호": "2100000279603"},
        ]
    }
}

DETAIL_WITH_LINKS = {
    "행정규칙": {
        "행정규칙명": "식품등의 표시기준",
        "별표": [
            {
                "별표명": "[별표 4] 영양성분 표시대상 식품",
                "별표파일링크": "/DRF/flDownload.do?flSeq=999",
            }
        ],
    }
}

DETAIL_NO_LINKS = {"행정규칙": {"행정규칙명": "식품등의 표시기준", "조문내용": "본문 없음"}}


class FakeClient:
    """LawClient 를 대신하는 최소 스텁 — 호출 인자를 기록한다."""

    def __init__(self, detail=None, search_payload=None):
        self._detail = detail or {}
        self._search = search_payload or {}
        self.search_calls = []
        self.service_calls = []

    def search(self, query, target="admrul", display=20, page=1, search=None):
        self.search_calls.append((query, target, display, search))
        return self._search

    def service(self, seq, target="admrul"):
        self.service_calls.append((seq, target))
        return self._detail


def ns(**kw):
    base = dict(via="detail", query=None, seq=None, target="admrul", display=20, byl=None,
                byl_search=2, no_owner_filter=True, quiet=True)
    base.update(kw)
    return argparse.Namespace(**base)


class TestSearchItems(unittest.TestCase):
    def test_extracts_list(self):
        items = search_items(ADMRUL_SEARCH)
        self.assertEqual(len(items), 2)

    def test_name_and_seq(self):
        items = search_items(ADMRUL_SEARCH)
        self.assertEqual(item_name(items[0]), "식품등의 표시기준")
        self.assertEqual(item_seq(items[0]), "2100000279602")

    def test_empty_payload(self):
        self.assertEqual(search_items({}), [])


class TestItemNameTargetAware(unittest.TestCase):
    """실제 API 응답에서 확인된 버그: ordin(자치법규) 검색 결과의 진짜 제목은
    '자치법규명' 인데, '명'으로 끝나는 첫 필드를 집는 방식이 '자치법규분야명'
    (예: "제5장 맑은도시" 같은 분류 라벨)을 제목으로 잘못 골랐다. 서로 다른
    조례 수십 건이 같은 분야에 속해 전부 같은 "제목"으로 나오는 결과였다."""

    ORDIN_ITEM = {
        "자치법규분야명": "제5장 맑은도시",
        "자치법규명": "가평군 주차장 설치 및 관리 조례",
        "자치법규일련번호": "1234567",
    }
    ADMRUL_ITEM = {"담당부서기관명": "식품의약품안전처(식품표시광고정책과)", "행정규칙명": "식품등의 표시기준"}
    LAW_ITEM = {"법령명한글": "식품위생법", "법령약칭명": "식품위생법"}

    def test_ordin_prefers_real_title_over_category_label(self):
        self.assertEqual(item_name(self.ORDIN_ITEM, "ordin"), "가평군 주차장 설치 및 관리 조례")

    def test_ordin_never_returns_category_label(self):
        self.assertNotEqual(item_name(self.ORDIN_ITEM, "ordin"), "제5장 맑은도시")

    def test_admrul_ignores_department_name(self):
        self.assertEqual(item_name(self.ADMRUL_ITEM, "admrul"), "식품등의 표시기준")

    def test_law_prefers_full_name(self):
        self.assertEqual(item_name(self.LAW_ITEM, "law"), "식품위생법")

    def test_unknown_target_falls_back_but_excludes_known_decoys(self):
        item = {"소관부처명": "무관", "실제제목명": "진짜 제목"}
        self.assertEqual(item_name(item, "unknown_target"), "진짜 제목")

    def test_missing_name_field_returns_placeholder(self):
        self.assertEqual(item_name({"아무값": "x"}), "?")


class TestExportDirectoryGuard(unittest.TestCase):
    """--out 에 디렉터리를 주면 IsADirectoryError 로 죽지 않고 안내하며 종료해야 한다.

    실제 cmd_export() 를 그대로 실행하되, make_client() 만 FakeClient 를
    돌려주도록 바꿔치기해서 네트워크 없이 실제 가드 코드 경로를 검증한다.
    """

    def test_directory_out_path_exits_cleanly(self):
        import argparse

        import law_fetch

        detail = {
            "행정규칙": {
                "행정규칙명": "테스트 고시",
                "조문내용": "제1조(목적) 이 고시는 디렉터리 가드 테스트를 목적으로 하며 40자를 넘기기 위한 문구를 덧붙인다.",
            }
        }
        client = FakeClient(detail=detail, search_payload=ADMRUL_SEARCH)

        orig_make_client = law_fetch.make_client
        law_fetch.make_client = lambda args: client
        try:
            with tempfile.TemporaryDirectory() as td:
                args = argparse.Namespace(
                    seq=None,
                    query="테스트",
                    target="admrul",
                    display=20,
                    codex="food",
                    category=None,
                    article=None,
                    out=td,  # 파일이 아니라 디렉터리
                    dry_run=False,
                    quiet=True,
                )
                with self.assertRaises(SystemExit) as cm:
                    law_fetch.cmd_export(args)
                self.assertIn("디렉터리", str(cm.exception))
        finally:
            law_fetch.make_client = orig_make_client


class TestItemSeqTargetAware(unittest.TestCase):
    """공식 가이드: admrul 은 ID=행정규칙일련번호, law 는 ID=법령ID.
    검색 응답에 둘 다 있어서 아무거나 집으면 본문이 비어 온다."""

    ADMRUL_ITEM = {
        "행정규칙ID": "36814",              # ← LID 파라미터용. ID 에 넣으면 안 된다
        "행정규칙명": "식품등의 표시기준",
        "행정규칙일련번호": "2100000279602",  # ← ID 파라미터용
    }
    LAW_ITEM = {"법령ID": "001234", "법령명한글": "식품위생법", "법령일련번호": "9988776655"}

    def test_admrul_uses_serial_number(self):
        self.assertEqual(item_seq(self.ADMRUL_ITEM, "admrul"), "2100000279602")

    def test_admrul_ignores_rule_id(self):
        self.assertNotEqual(item_seq(self.ADMRUL_ITEM, "admrul"), "36814")

    def test_law_uses_law_id(self):
        self.assertEqual(item_seq(self.LAW_ITEM, "law"), "001234")

    def test_default_target_is_admrul(self):
        self.assertEqual(item_seq(self.ADMRUL_ITEM), "2100000279602")

    def test_missing_fields_returns_none(self):
        self.assertIsNone(item_seq({"행정규칙명": "이름만 있음"}, "admrul"))


class TestZeroPaddedBylNumber(unittest.TestCase):
    """'000100' 을 그대로 int 로 읽으면 별표 1 이 '별표 100' 이 된다.
    실제 실행에서 별표 4 를 못 찾은 원인의 회귀 테스트."""

    def test_decode_plain(self):
        from parsers import decode_byl_number

        self.assertEqual(decode_byl_number("000100"), (1, None))
        self.assertEqual(decode_byl_number("000400"), (4, None))

    def test_decode_branch(self):
        from parsers import decode_byl_number

        self.assertEqual(decode_byl_number("000402"), (4, 2))

    def test_decode_large_number(self):
        from parsers import decode_byl_number

        self.assertEqual(decode_byl_number("010000"), (100, None))

    def test_short_form_is_literal(self):
        from parsers import decode_byl_number

        self.assertEqual(decode_byl_number("4"), (4, None))
        self.assertEqual(decode_byl_number("12"), (12, None))

    def test_explicit_branch_field_wins(self):
        from parsers import decode_byl_number

        self.assertEqual(decode_byl_number("000400", "2"), (4, 2))

    def test_invalid_returns_none(self):
        from parsers import decode_byl_number

        self.assertIsNone(decode_byl_number(""))
        self.assertIsNone(decode_byl_number(None))
        self.assertIsNone(decode_byl_number("000000"))


class TestExtractAttachments(unittest.TestCase):
    def test_decodes_zero_padded_numbers(self):
        refs = {r["ref"] for r in extract_attachments(ADMBYL_SEARCH)}
        self.assertIn(BylRef("별표", 1, None), refs)   # "000100" — 별표 100 이 아니다
        self.assertIn(BylRef("별표", 4, None), refs)   # "000400"
        self.assertIn(BylRef("별표", 4, 2), refs)      # "000402"
        self.assertNotIn(BylRef("별표", 100, None), refs)
        self.assertNotIn(BylRef("별표", 400, None), refs)

    def test_title_is_annex_name_not_ministry(self):
        # 소관부처명이 별표명보다 앞에 와서 '식품의약품안전처' 를 집던 버그
        recs = extract_attachments(ADMBYL_SEARCH)
        titles = {r["title"] for r in recs}
        self.assertNotIn("식품의약품안전처", titles)
        self.assertTrue(any("기능성 표시 식품등의 영양성분" in t for t in titles))

    def test_html_viewer_link_classified_separately(self):
        recs = extract_attachments(ADMBYL_SEARCH)
        kinds = {r["fmt"] for r in recs}
        self.assertIn("HTML", kinds)  # 별표행정규칙상세링크
        self.assertIn("파일", kinds)  # 별표서식파일링크
        self.assertIn("PDF", kinds)
        html = [r for r in recs if r["fmt"] == "HTML"]
        self.assertTrue(all("type=HTML" in r["url"] for r in html))

    def test_urls_absolutized(self):
        recs = extract_attachments(ADMBYL_SEARCH)
        self.assertTrue(all(r["url"].startswith("https://www.law.go.kr/") for r in recs))

    def test_ref_from_title_when_no_number_field(self):
        recs = extract_attachments(DETAIL_WITH_LINKS)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["ref"], BylRef("별표", 4, None))

    def test_no_links(self):
        self.assertEqual(extract_attachments(DETAIL_NO_LINKS), [])


# 실제 lawService.do(target=admrul) 응답에서 그대로 옮긴 픽스처(OC=test 로 조회,
# ID=2100000279014, 「식품등의 표시기준」). 핵심 함정 두 개:
#   1) `별표번호`="0001".."0007" 은 별표/별지/별도 각 그룹 안의 순번일 뿐이고,
#      진짜 번호는 `별표키`="000100" 등에 있다 (admbyl 검색 응답과 필드 의미가 다르다)
#   2) `담당부서기관명`이 `행정규칙명`보다 먼저 나와 제목을 오인하기 쉽다
REAL_ADMRUL_DETAIL = {
    "AdmRulService": {
        "행정규칙기본정보": {
            "현행여부": "현행",
            "담당부서기관명": "식품의약품안전처(식품표시광고정책과)",
            "담당자명": "정샛별",
            "행정규칙명": "식품등의 표시기준",
            "소관부처명": "식품의약품안전처",
        },
        "별표": {
            "별표단위": [
                {
                    "별표제목": "인삼의 유래 기본문안",
                    "별표번호": "0001",
                    "별표키": "000100",
                    "별표구분": "별표",
                    "별표가지번호": "00",
                    "별표서식파일링크": "/LSW/flDownload.do?flSeq=164484193",
                    "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=164484201",
                },
                {
                    "별표제목": "명칭과 용도를 함께 표시하여야 하는 식품첨가물",
                    "별표번호": "0004",
                    "별표키": "000400",
                    "별표구분": "별표",
                    "별표가지번호": "00",
                    "별표서식파일링크": "/LSW/flDownload.do?flSeq=164484225",
                    "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=164484233",
                },
                {
                    "별표제목": "표시사항별 세부표시기준",
                    "별표번호": "0001",  # 별지 그룹의 1번째 — 별표 그룹의 0001 과 겹친다
                    "별표키": "000100",
                    "별표구분": "별지",
                    "별표가지번호": "00",
                    "별표서식파일링크": "/LSW/flDownload.do?flSeq=164484263",
                    "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=164484323",
                },
                {
                    "별표제목": "용기·포장의 주표시면 및 정보표시면 구분",
                    "별표번호": "0001",  # 별도 그룹의 1번째 — 역시 0001 과 겹친다
                    "별표키": "000100",
                    "별표구분": "별도",
                    "별표가지번호": "00",
                    "별표서식파일링크": "/LSW/flDownload.do?flSeq=164484325",
                    "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=164484331",
                },
            ]
        },
    }
}


class TestRealAdmrulDetailResponse(unittest.TestCase):
    """실제 API 응답으로 확인된 필드 함정에 대한 회귀 테스트."""

    def test_title_prefers_admrul_name_over_department(self):
        # 담당부서기관명이 먼저 나와도 행정규칙명을 골라야 한다
        self.assertEqual(detail_title(REAL_ADMRUL_DETAIL), "식품등의 표시기준")

    def test_byl_key_overrides_decoy_byl_number(self):
        # 별표번호("0001")는 그룹 내 순번일 뿐이다. 별표키("000100")가 진짜 번호다.
        recs = extract_attachments(REAL_ADMRUL_DETAIL)
        refs_by_title = {r["title"]: r["ref"] for r in recs}
        self.assertEqual(refs_by_title["인삼의 유래 기본문안"], BylRef("별표", 1, None))
        self.assertEqual(
            refs_by_title["명칭과 용도를 함께 표시하여야 하는 식품첨가물"], BylRef("별표", 4, None)
        )

    def test_same_decoy_number_across_kinds_not_confused(self):
        # 별표/별지/별도 세 그룹 모두 별표번호="0001" 이지만 종류가 다르므로
        # 별개의 항목으로 구분되어야 한다 (겹쳐서 하나로 뭉개지면 안 된다)
        recs = extract_attachments(REAL_ADMRUL_DETAIL)
        refs = {r["ref"] for r in recs}
        self.assertIn(BylRef("별표", 1, None), refs)
        self.assertIn(BylRef("별지", 1, None), refs)
        self.assertIn(BylRef("별도", 1, None), refs)

    def test_byl_filter_finds_annex_4_by_true_number(self):
        recs = extract_attachments(REAL_ADMRUL_DETAIL)
        got = apply_byl_filter(recs, "별표 4")
        self.assertTrue(got)
        self.assertTrue(all(r["title"] == "명칭과 용도를 함께 표시하여야 하는 식품첨가물" for r in got))

    def test_kind_field_name_differs_from_admbyl(self):
        # admrul 상세는 종류 필드명이 '별표구분' (admbyl 은 '별표종류')
        recs = extract_attachments(REAL_ADMRUL_DETAIL)
        kinds = {r["ref"].kind for r in recs if r["ref"]}
        self.assertEqual(kinds, {"별표", "별지", "별도"})


class TestNestedLinkInheritsOuterContext(unittest.TestCase):
    """admrul 상세(lawService) 응답의 실제 구조: 별표번호·별표명이 있는 바깥
    항목과, 실제 파일 링크가 있는 안쪽 리스트가 분리되어 있다. 실제 실행에서
    모든 별표가 '(번호 미상)' 으로 나온 원인의 회귀 테스트."""

    NESTED_DETAIL = {
        "행정규칙": {
            "행정규칙명": "식품등의 표시기준",
            "별표": [
                {
                    "별표번호": "4",
                    "별표가지번호": "0",
                    "별표명": "명칭과 용도를 함께 표시하여야 하는 식품첨가물",
                    "별표파일": [
                        {"파일명": "명칭과 용도를 함께 표시하여야 하는 식품첨가물", "파일링크": "/LSW/flDownload.do?flSeq=1", "파일종류": "HWP"},
                        {"파일명": "명칭과 용도를 함께 표시하여야 하는 식품첨가물", "파일PDF링크": "/LSW/flDownload.do?flSeq=2"},
                    ],
                },
                {
                    "별표번호": "1",
                    "별표가지번호": "0",
                    "별표명": "한국인 영양섭취기준",
                    "별표파일": [
                        {"파일명": "한국인 영양섭취기준", "파일링크": "/LSW/flDownload.do?flSeq=3"},
                    ],
                },
            ],
        }
    }

    def test_inner_link_inherits_outer_number(self):
        recs = extract_attachments(self.NESTED_DETAIL)
        refs = {r["ref"] for r in recs}
        self.assertNotIn(None, refs, "안쪽 링크가 바깥 별표번호를 물려받지 못함")
        self.assertIn(BylRef("별표", 4, None), refs)
        self.assertIn(BylRef("별표", 1, None), refs)

    def test_inner_link_keeps_specific_title(self):
        recs = extract_attachments(self.NESTED_DETAIL)
        titles = {r["title"] for r in recs}
        self.assertIn("명칭과 용도를 함께 표시하여야 하는 식품첨가물", titles)
        self.assertIn("한국인 영양섭취기준", titles)

    def test_three_links_all_resolved(self):
        recs = extract_attachments(self.NESTED_DETAIL)
        self.assertEqual(len(recs), 3)
        self.assertTrue(all(r["ref"] is not None for r in recs))

    def test_flat_structure_still_works(self):
        # 회귀 방지: admbyl(평면 구조) 픽스처가 여전히 정상 동작해야 한다
        recs = extract_attachments(ADMBYL_SEARCH)
        self.assertTrue(all(r["ref"] is not None for r in recs))


class TestBylFilter(unittest.TestCase):
    # 픽스처 링크 구성: 별표1 = HTML+파일(2), 별표4 = HTML+파일+PDF(3),
    #                  별표4의2 = 파일(1)  → 총 6건
    def setUp(self):
        self.recs = extract_attachments(ADMBYL_SEARCH)

    def test_fixture_shape(self):
        self.assertEqual(len(self.recs), 6)

    def test_selects_only_requested_annex(self):
        got = apply_byl_filter(self.recs, "별표 4")
        self.assertEqual(len(got), 3)  # HTML + 파일 + PDF
        self.assertTrue(all(r["ref"] == BylRef("별표", 4, None) for r in got))

    def test_branch_not_swept_in(self):
        got = apply_byl_filter(self.recs, "별표 4")
        self.assertNotIn(BylRef("별표", 4, 2), {r["ref"] for r in got})

    def test_branch_selectable(self):
        got = apply_byl_filter(self.recs, "별표 4의2")
        self.assertEqual([r["ref"] for r in got], [BylRef("별표", 4, 2)])

    def test_bare_number_matches_any_kind(self):
        got = apply_byl_filter(self.recs, "1")
        self.assertTrue(got)
        self.assertTrue(all(r["ref"] == BylRef("별표", 1, None) for r in got))

    def test_multiple_specs(self):
        got = apply_byl_filter(self.recs, "별표 1, 별표 4")
        self.assertEqual(len(got), 5)  # 2 + 3

    def test_no_filter_returns_all(self):
        self.assertEqual(len(apply_byl_filter(self.recs, None)), 6)

    def test_missing_annex_exits_with_available_list(self):
        with self.assertRaises(SystemExit) as cm:
            apply_byl_filter(self.recs, "별표 99")
        msg = str(cm.exception)
        self.assertIn("별표 99", msg)
        self.assertIn("별표 4", msg)  # 있는 목록을 안내해야 한다

    def test_html_links_excluded_leaves_downloadable_only(self):
        files = [r for r in self.recs if r["fmt"] != "HTML"]
        got = apply_byl_filter(files, "별표 4")
        self.assertEqual(len(got), 2)  # 파일 + PDF (HTML 열람 링크 제외)


class TestCollectAttachments(unittest.TestCase):
    def test_detail_path(self):
        c = FakeClient(detail=DETAIL_WITH_LINKS)
        recs = collect_attachments(c, ns(seq="123"))
        self.assertEqual(len(recs), 1)
        self.assertEqual(c.service_calls, [("123", "admrul")])
        self.assertEqual(c.search_calls, [])  # 폴백 불필요

    def test_falls_back_to_admbyl_when_detail_has_no_links(self):
        c = FakeClient(detail=DETAIL_NO_LINKS, search_payload=ADMBYL_SEARCH)
        recs = collect_attachments(c, ns(seq="123"))
        self.assertEqual(len(recs), 6)
        self.assertEqual(len(c.search_calls), 1)
        self.assertEqual(c.search_calls[0][1], "admbyl")
        # search=2 = '해당법령검색' — 별표명이 아니라 소속 고시명으로 찾아야 한다
        self.assertEqual(c.search_calls[0][3], 2)

    def test_via_admbyl_skips_detail(self):
        c = FakeClient(search_payload=ADMBYL_SEARCH)
        recs = collect_attachments(c, ns(via="admbyl", query="식품등의 표시기준"))
        self.assertEqual(len(recs), 6)
        self.assertEqual(c.service_calls, [])

    def test_query_resolves_seq_then_fetches_detail(self):
        c = FakeClient(detail=DETAIL_WITH_LINKS, search_payload=ADMRUL_SEARCH)
        collect_attachments(c, ns(query="식품등의 표시기준"))
        self.assertEqual(c.service_calls, [("2100000279602", "admrul")])


class TestDetailTitle(unittest.TestCase):
    def test_finds_name(self):
        self.assertEqual(detail_title(DETAIL_WITH_LINKS), "식품등의 표시기준")

    def test_none_when_absent(self):
        self.assertIsNone(detail_title({"a": {"b": 1}}))


class TestFileHelpers(unittest.TestCase):
    def test_sniff_pdf(self):
        self.assertEqual(sniff_ext(b"%PDF-1.7 rest"), ".pdf")

    def test_sniff_hwp_ole2(self):
        self.assertEqual(sniff_ext(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1more"), ".hwp")

    def test_sniff_hwpx_zip(self):
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/hwp+zip")
            z.writestr("Contents/content.hpf", "x")
        self.assertEqual(sniff_ext(buf.getvalue()), ".hwpx")

    def test_sniff_docx_zip(self):
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", "<xml/>")
        self.assertEqual(sniff_ext(buf.getvalue()), ".docx")

    def test_sniff_unknown(self):
        self.assertEqual(sniff_ext(b"plain text"), "")

    def test_safe_filename_strips_separators(self):
        self.assertEqual(safe_filename("별표 4/영양: 정보*"), "별표 4_영양_ 정보_")

    def test_safe_filename_never_empty(self):
        self.assertEqual(safe_filename("///"), "attachment")


class TestDownloadNaming(unittest.TestCase):
    """같은 별표의 HWP/PDF 가 서로를 덮어쓰면 안 된다."""

    class DLClient:
        def __init__(self, blob=b"%PDF-1.7 x", headers=None):
            self.blob = blob
            self.headers = headers or {"Content-Type": "application/pdf"}

        def download(self, url):
            import lawapi

            return lawapi.Response(self.blob, self.headers, False, url)

    def test_same_name_gets_uniquified(self):
        from law_fetch import download_record

        recs = [
            r
            for r in extract_attachments(ADMBYL_SEARCH)
            if r["ref"] == BylRef("별표", 4, None) and r["fmt"] != "HTML"
        ]
        self.assertEqual(len(recs), 2)  # 파일 + PDF 두 링크

        client = self.DLClient()
        with tempfile.TemporaryDirectory() as td:
            taken: set = set()
            paths = [download_record(client, r, Path(td), i, taken) for i, r in enumerate(recs)]
            self.assertNotEqual(paths[0], paths[1], "두 파일이 같은 경로로 저장됨(덮어쓰기)")
            self.assertTrue(all(p.exists() for p in paths))
            self.assertEqual(len(list(Path(td).iterdir())), 2)

    def test_content_disposition_filename_used(self):
        from law_fetch import download_record

        client = self.DLClient(
            headers={"Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="별표4.pdf"'}
        )
        rec = extract_attachments(ADMBYL_SEARCH)[0]
        with tempfile.TemporaryDirectory() as td:
            p = download_record(client, rec, Path(td), 0, set())
            self.assertEqual(p.name, "별표4.pdf")

    def test_extension_sniffed_when_no_header(self):
        from law_fetch import download_record

        rec = extract_attachments(ADMBYL_SEARCH)[0]
        with tempfile.TemporaryDirectory() as td:
            p = download_record(self.DLClient(), rec, Path(td), 0, set())
            self.assertEqual(p.suffix, ".pdf")


class TestPickBestMatch(unittest.TestCase):
    """법제처 검색은 부분일치라 원하는 고시가 첫 줄에 안 온다.
    실제 실행에서 '식품등의 표시기준' 검색이 '식품등의 부당한 표시 또는 광고의
    내용 기준' 을 집어온 상황의 회귀 테스트."""

    REAL = [
        {"행정규칙명": "식품등의 부당한 표시 또는 광고의 내용 기준", "행정규칙일련번호": "69549"},
        {"행정규칙명": "식품등의 표시기준", "행정규칙일련번호": "36814"},
        {"행정규칙명": "식품등의 표시 또는 광고 심의 및 이의신청 기준", "행정규칙일련번호": "66910"},
        {"행정규칙명": "유전자변형식품등의 표시기준", "행정규칙일련번호": "44603"},
    ]

    def test_exact_name_wins_over_first_result(self):
        from law_fetch import pick_best_match

        chosen, why = pick_best_match(self.REAL, "식품등의 표시기준")
        self.assertEqual(item_seq(chosen), "36814")
        self.assertEqual(why, "정확히 일치")

    def test_whitespace_insensitive(self):
        from law_fetch import pick_best_match

        chosen, _ = pick_best_match(self.REAL, "식품등의  표시기준 ")
        self.assertEqual(item_seq(chosen), "36814")

    def test_prefix_match_when_no_exact(self):
        from law_fetch import pick_best_match

        chosen, why = pick_best_match(self.REAL, "식품등의 부당한")
        self.assertEqual(item_seq(chosen), "69549")
        self.assertEqual(why, "접두 일치")

    def test_substring_match(self):
        from law_fetch import pick_best_match

        chosen, why = pick_best_match(self.REAL, "유전자변형")
        self.assertEqual(item_seq(chosen), "44603")
        self.assertEqual(why, "접두 일치")

    def test_falls_back_to_first_with_reason(self):
        from law_fetch import pick_best_match

        chosen, why = pick_best_match(self.REAL, "존재하지않는이름")
        self.assertEqual(item_seq(chosen), "69549")
        self.assertIn("일치 없음", why)


class TestProbeModule(unittest.TestCase):
    """'미설치'와 '의존성 누락'을 구분해야 한다 — 이미 설치한 사람에게
    '설치하세요'라고 하면 헛돌게 된다(실제로 mammoth 에서 겪은 상황)."""

    def test_importable_module(self):
        from law_fetch import probe_module

        ok, detail = probe_module("json")
        self.assertTrue(ok)
        self.assertEqual(detail, "")

    def test_missing_module(self):
        from law_fetch import probe_module

        def boom(name):
            raise ImportError(f"No module named {name!r}", name=name)

        ok, detail = probe_module("nope_xyz", _import=boom)
        self.assertFalse(ok)
        self.assertIn("미설치", detail)

    def test_missing_transitive_dependency(self):
        from law_fetch import probe_module

        def boom(_name):
            raise ImportError("No module named 'cobble'", name="cobble")

        ok, detail = probe_module("mammoth", _import=boom)
        self.assertFalse(ok)
        self.assertIn("설치됨", detail)
        self.assertIn("cobble", detail)

    def test_pip_name_differs_from_import_name(self):
        from law_fetch import probe_module

        def boom(_name):
            raise ImportError("No module named 'PIL'", name="PIL")

        _, detail = probe_module("pytesseract", _import=boom)
        self.assertIn("pip install pillow", detail)  # 'pip install PIL' 은 틀린 명령
        self.assertNotIn("pip install PIL", detail)

    def test_non_import_error_reported(self):
        from law_fetch import probe_module

        def boom(_name):
            raise RuntimeError("초기화 실패")

        ok, detail = probe_module("weird", _import=boom)
        self.assertFalse(ok)
        self.assertIn("RuntimeError", detail)


class TestKbFileRoundTrip(unittest.TestCase):
    """export 가 쓰는 지식베이스 파일 형식이 food-code-analyzer 와 호환되는지."""

    def test_merged_file_matches_schema(self):
        from parsers import articles_to_kb_entries, merge_kb, parse_articles

        arts = parse_articles("제1조(목적) 이 고시는 ...을 목적으로 한다.")
        entries = articles_to_kb_entries(arts, "식품공전", "테스트 고시")
        merged, _, _ = merge_kb({"metadata": {"name": "식품공전"}, "entries": []}, entries)

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "food-codex.json"
            p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            back = json.loads(p.read_text(encoding="utf-8"))

        self.assertIn("metadata", back)
        self.assertIn("entries", back)
        for e in back["entries"]:
            self.assertIn("term", e)
            self.assertIn("definition", e)
            self.assertIn("category", e)


if __name__ == "__main__":
    unittest.main(verbosity=2)
