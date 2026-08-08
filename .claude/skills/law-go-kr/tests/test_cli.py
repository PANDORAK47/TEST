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

ADMBYL_SEARCH = {
    "AdmbylSearch": {
        "admbyl": [
            {
                "별표종류": "별표",
                "별표번호": "4",
                "별표가지번호": "0",
                "별표명": "영양성분 표시대상 식품",
                "별표서식파일링크": "/DRF/flDownload.do?flSeq=111",
                "별표서식PDF파일링크": "/DRF/flDownload.do?flSeq=112",
                "행정규칙명": "식품등의 표시기준",
            },
            {
                "별표종류": "별표",
                "별표번호": "4",
                "별표가지번호": "2",
                "별표명": "영양성분 표시대상 추가 기준",
                "별표서식파일링크": "/DRF/flDownload.do?flSeq=113",
            },
            {
                "별표종류": "별지",
                "별표번호": "1",
                "별표가지번호": "0",
                "별표명": "표시사항별 세부표시기준",
                "별표서식파일링크": "/DRF/flDownload.do?flSeq=114",
            },
        ]
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

    def search(self, query, target="admrul", display=20, page=1):
        self.search_calls.append((query, target, display))
        return self._search

    def service(self, seq, target="admrul"):
        self.service_calls.append((seq, target))
        return self._detail


def ns(**kw):
    base = dict(via="detail", query=None, seq=None, target="admrul", display=20, byl=None)
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


class TestExtractAttachments(unittest.TestCase):
    def test_finds_all_links(self):
        recs = extract_attachments(ADMBYL_SEARCH)
        # 별표4(HWP+PDF) + 별표4의2 + 별지1 = 4건
        self.assertEqual(len(recs), 4)

    def test_marks_pdf_format(self):
        recs = extract_attachments(ADMBYL_SEARCH)
        fmts = {r["fmt"] for r in recs}
        self.assertEqual(fmts, {"PDF", "HWP/기타"})

    def test_refs_from_explicit_fields(self):
        recs = extract_attachments(ADMBYL_SEARCH)
        refs = {r["ref"] for r in recs}
        self.assertIn(BylRef("별표", 4, None), refs)
        self.assertIn(BylRef("별표", 4, 2), refs)
        self.assertIn(BylRef("별지", 1, None), refs)

    def test_urls_absolutized(self):
        recs = extract_attachments(ADMBYL_SEARCH)
        self.assertTrue(all(r["url"].startswith("https://www.law.go.kr/") for r in recs))

    def test_ref_from_title_when_no_fields(self):
        recs = extract_attachments(DETAIL_WITH_LINKS)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["ref"], BylRef("별표", 4, None))

    def test_no_links(self):
        self.assertEqual(extract_attachments(DETAIL_NO_LINKS), [])


class TestBylFilter(unittest.TestCase):
    def setUp(self):
        self.recs = extract_attachments(ADMBYL_SEARCH)

    def test_selects_only_requested_annex(self):
        got = apply_byl_filter(self.recs, "별표 4")
        self.assertEqual(len(got), 2)  # HWP + PDF
        self.assertTrue(all(r["ref"] == BylRef("별표", 4, None) for r in got))

    def test_branch_not_swept_in(self):
        got = apply_byl_filter(self.recs, "별표 4")
        self.assertNotIn(BylRef("별표", 4, 2), {r["ref"] for r in got})

    def test_branch_selectable(self):
        got = apply_byl_filter(self.recs, "별표 4의2")
        self.assertEqual([r["ref"] for r in got], [BylRef("별표", 4, 2)])

    def test_bare_number_matches_both_kinds(self):
        got = apply_byl_filter(self.recs, "1")
        self.assertEqual([r["ref"] for r in got], [BylRef("별지", 1, None)])

    def test_multiple_specs(self):
        got = apply_byl_filter(self.recs, "별표 4, 별지 1")
        self.assertEqual(len(got), 3)

    def test_no_filter_returns_all(self):
        self.assertEqual(len(apply_byl_filter(self.recs, None)), 4)

    def test_missing_annex_exits_with_available_list(self):
        with self.assertRaises(SystemExit) as cm:
            apply_byl_filter(self.recs, "별표 99")
        msg = str(cm.exception)
        self.assertIn("별표 99", msg)
        self.assertIn("별표 4", msg)  # 있는 목록을 안내해야 한다


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
        self.assertEqual(len(recs), 4)
        self.assertEqual(len(c.search_calls), 1)
        self.assertEqual(c.search_calls[0][1], "admbyl")

    def test_via_admbyl_skips_detail(self):
        c = FakeClient(search_payload=ADMBYL_SEARCH)
        recs = collect_attachments(c, ns(via="admbyl", query="식품등의 표시기준"))
        self.assertEqual(len(recs), 4)
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

        recs = [r for r in extract_attachments(ADMBYL_SEARCH) if r["ref"] == BylRef("별표", 4, None)]
        self.assertEqual(len(recs), 2)  # HWP + PDF 두 링크

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
