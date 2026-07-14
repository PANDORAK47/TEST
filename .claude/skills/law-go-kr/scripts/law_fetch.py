#!/usr/bin/env python3
"""법제처 국가법령정보 OPEN API(DRF) 클라이언트.

법령/행정규칙을 검색하고, 본문에 딸린 별표·서식(첨부파일: HWP/HWPX/PDF)을
찾아 내려받은 뒤, 필요하면 korean-doc-parser 스킬로 파싱한다.

전제:
  - 환경변수 LAW_GO_KR_OC 에 법제처 OPEN API 인증키(OC, 보통 이메일 아이디)를 설정.
    open.law.go.kr 에서 발급.  (코드/리포에는 키를 남기지 않는다.)
  - 실행 환경이 www.law.go.kr 로 아웃바운드 HTTPS 가능해야 한다.

사용법:
  export LAW_GO_KR_OC=your_oc_id

  # 1) 검색 → 행정규칙일련번호(ID) 확인
  python3 law_fetch.py search "식품의 기준 및 규격" --target admrul

  # 2) 첨부파일(별표) 목록만 보기
  python3 law_fetch.py attachments 2100000279602 --target admrul

  # 3) 첨부파일 전부 다운로드
  python3 law_fetch.py fetch 2100000279602 --target admrul --outdir ./out

  # 4) 다운로드 + 파싱(텍스트 추출) + 키워드 필터
  python3 law_fetch.py fetch 2100000279602 --target admrul --outdir ./out \
          --parse --grep 과자류

  # 이름으로 바로 검색→다운로드
  python3 law_fetch.py fetch --query "식품의 기준 및 규격" --target admrul --parse
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

HOST = "https://www.law.go.kr"
SEARCH_URL = HOST + "/DRF/lawSearch.do"
SERVICE_URL = HOST + "/DRF/lawService.do"

# korean-doc-parser 스킬의 파서 경로(같은 리포 기준)
PARSER = (
    Path(__file__).resolve().parents[2]
    / "korean-doc-parser"
    / "scripts"
    / "parse_doc.py"
)


def _oc() -> str:
    oc = os.environ.get("LAW_GO_KR_OC", "").strip()
    if not oc:
        sys.exit(
            "환경변수 LAW_GO_KR_OC 가 없습니다.  open.law.go.kr 에서 OPEN API 키(OC)를 발급받아\n"
            "  export LAW_GO_KR_OC=your_oc_id\n"
            "로 설정한 뒤 다시 실행하세요."
        )
    return oc


def _session():
    if requests is None:
        sys.exit("requests 패키지가 필요합니다: pip install requests")
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; law-go-kr-skill/1.0)",
            "Accept": "application/json, */*",
        }
    )
    return s


def _get(sess, url, params, want_json=True):
    r = sess.get(url, params=params, timeout=30, allow_redirects=True)
    r.raise_for_status()
    if want_json:
        # 법제처가 text/html 로 JSON을 주는 경우가 있어 직접 파싱
        try:
            return r.json()
        except Exception:
            return json.loads(r.text)
    return r


def search(sess, query, target="admrul", display=20):
    data = _get(
        sess,
        SEARCH_URL,
        {"OC": _oc(), "target": target, "type": "JSON", "query": query, "display": display},
    )
    # 응답 최상위 키는 target 별로 다름 → 첫 dict 값에서 리스트를 찾는다
    root = next(iter(data.values())) if isinstance(data, dict) else data
    items = []
    if isinstance(root, dict):
        for v in root.values():
            if isinstance(v, list):
                items = v
                break
            if isinstance(v, dict) and any(k for k in v if "명" in k):
                items = [v]
                break
    return items


def get_detail(sess, seq, target="admrul"):
    return _get(
        sess,
        SERVICE_URL,
        {"OC": _oc(), "target": target, "type": "JSON", "ID": str(seq)},
    )


LINK_KEY_RE = re.compile(r"(링크|파일|다운로드)", re.I)
PATH_RE = re.compile(r"^(https?://|/)")


def extract_attachment_links(detail):
    """상세 JSON을 재귀 탐색하여 첨부파일(별표/서식) 다운로드 링크를 수집한다.

    필드명이 버전마다 다르므로 (키에 '링크/파일' 포함) & (값이 URL/경로) 인
    항목을 모두 후보로 모은다.  (제목, url) 튜플 리스트를 반환.
    """
    found = []
    seen = set()

    def title_near(container):
        if isinstance(container, dict):
            for k, v in container.items():
                if isinstance(v, str) and ("제목" in k or "명" in k) and v.strip():
                    return v.strip()
        return None

    def walk(node, parent=None):
        if isinstance(node, dict):
            t = title_near(node)
            for k, v in node.items():
                if isinstance(v, str) and LINK_KEY_RE.search(k) and PATH_RE.match(v.strip()):
                    url = urljoin(HOST, v.strip())
                    if url not in seen:
                        seen.add(url)
                        found.append((t or k, url))
                else:
                    walk(v, node)
        elif isinstance(node, list):
            for item in node:
                walk(item, node)

    walk(detail)
    return found


def _filename_from_response(resp, fallback):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", cd)
    name = unquote(m.group(1)).strip('"') if m else ""
    if name:
        return name
    return fallback


_MAGIC = [
    (b"%PDF", ".pdf"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".hwp"),  # OLE2 (hwp5/doc)
    (b"PK\x03\x04", ".zip"),  # hwpx/docx (zip) → 내부 확인
]


def _sniff_ext(head_bytes, blob):
    for magic, ext in _MAGIC:
        if head_bytes.startswith(magic):
            if ext == ".zip":
                try:
                    zf = zipfile.ZipFile(io.BytesIO(blob))
                    names = set(zf.namelist())
                    if "mimetype" in names or any(n.startswith("Contents/") for n in names):
                        return ".hwpx"
                    if any(n.startswith("word/") for n in names):
                        return ".docx"
                except Exception:
                    pass
                return ".zip"
            return ext
    return ""


def download(sess, url, outdir, idx=0):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    resp = _get(sess, url, {}, want_json=False)
    blob = resp.content
    ext = _sniff_ext(blob[:16], blob)
    base = _filename_from_response(resp, f"attachment_{idx}")
    if not Path(base).suffix and ext:
        base += ext
    # 파일명에 경로/이상문자 제거
    base = re.sub(r"[\\/:*?\"<>|]+", "_", base)
    path = outdir / base
    path.write_bytes(blob)
    return path


def parse_file(path):
    if not PARSER.exists():
        return f"[파서 없음: {PARSER}]"
    r = subprocess.run(
        [sys.executable, str(PARSER), str(path)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return f"[파싱 실패] {r.stderr.strip()}"
    return r.stdout


def cmd_search(args):
    sess = _session()
    items = search(sess, args.query, args.target, args.display)
    if not items:
        print("검색 결과 없음")
        return
    for it in items:
        name = next((v for k, v in it.items() if "명" in k and isinstance(v, str)), "?")
        seq = next(
            (v for k, v in it.items() if ("일련번호" in k or k.endswith("ID")) and v),
            "?",
        )
        print(f"{seq}\t{name}")


def cmd_attachments(args):
    sess = _session()
    detail = get_detail(sess, args.seq, args.target)
    links = extract_attachment_links(detail)
    if not links:
        print("첨부파일(별표/서식) 링크를 찾지 못함. 원문에 본문 조문만 있을 수 있음.")
        return
    for title, url in links:
        print(f"{title}\t{url}")


def _resolve_seq(sess, args):
    if getattr(args, "query", None):
        items = search(sess, args.query, args.target, args.display)
        if not items:
            sys.exit("검색 결과 없음")
        it = items[0]
        seq = next(
            (v for k, v in it.items() if ("일련번호" in k or k.endswith("ID")) and v),
            None,
        )
        name = next((v for k, v in it.items() if "명" in k and isinstance(v, str)), "?")
        print(f"[선택] {seq}  {name}", file=sys.stderr)
        return seq
    return args.seq


def cmd_fetch(args):
    sess = _session()
    seq = _resolve_seq(sess, args)
    detail = get_detail(sess, seq, args.target)
    links = extract_attachment_links(detail)
    if not links:
        print("첨부파일 링크 없음")
        return
    for i, (title, url) in enumerate(links):
        path = download(sess, url, args.outdir, i)
        print(f"[다운로드] {title} -> {path}", file=sys.stderr)
        if args.parse:
            text = parse_file(path)
            if args.grep:
                lines = [ln for ln in text.splitlines() if args.grep in ln]
                text = "\n".join(lines) if lines else f"(‘{args.grep}’ 미포함)"
            print(f"\n===== {title} ({path.name}) =====")
            print(text)


def main():
    ap = argparse.ArgumentParser(description="법제처 국가법령정보 OPEN API 클라이언트")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="법령/행정규칙 검색")
    p.add_argument("query")
    p.add_argument("--target", default="admrul", help="law | admrul | ordin ... (기본 admrul)")
    p.add_argument("--display", type=int, default=20)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("attachments", help="첨부파일(별표/서식) 링크 목록")
    p.add_argument("seq", help="행정규칙일련번호/법령ID")
    p.add_argument("--target", default="admrul")
    p.set_defaults(func=cmd_attachments)

    p = sub.add_parser("fetch", help="첨부파일 다운로드(+파싱)")
    p.add_argument("seq", nargs="?", help="행정규칙일련번호/법령ID")
    p.add_argument("--query", help="이름으로 검색해 첫 결과를 사용")
    p.add_argument("--target", default="admrul")
    p.add_argument("--display", type=int, default=20)
    p.add_argument("--outdir", default="./law_attachments")
    p.add_argument("--parse", action="store_true", help="다운로드 후 텍스트 파싱")
    p.add_argument("--grep", help="파싱 결과에서 해당 키워드 포함 줄만 출력")
    p.set_defaults(func=cmd_fetch)

    args = ap.parse_args()
    if args.cmd == "fetch" and not args.seq and not args.query:
        ap.error("fetch 는 seq 또는 --query 중 하나가 필요합니다")
    args.func(args)


if __name__ == "__main__":
    main()
