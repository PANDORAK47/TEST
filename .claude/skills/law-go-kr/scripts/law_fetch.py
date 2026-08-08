#!/usr/bin/env python3
"""법제처 국가법령정보 OPEN API(DRF) CLI.

법령·행정규칙을 검색하고, 별표·서식 첨부파일을 번호로 콕 집어 내려받아
파싱하고, 조문 본문을 구조화해 출력하거나 food-code-analyzer 지식베이스로
내보낸다.

계층:
  lawapi.py   — HTTP(재시도·타임아웃·캐시·오류 구분)
  parsers.py  — 별표 참조·조문 트리·출력 포맷(순수 함수, 테스트됨)
  law_fetch.py— CLI 와 오케스트레이션(이 파일)

전제:
  export LAW_GO_KR_OC=your_oc_id     # open.law.go.kr 에서 발급
  실행 환경이 www.law.go.kr 로 아웃바운드 HTTPS 가능해야 한다.

주요 사용법:
  # 검색 → 행정규칙일련번호 확인
  law_fetch.py search "식품등의 표시기준"

  # 별표 목록 보기 (번호 인식 결과까지)
  law_fetch.py annexes --query "식품등의 표시기준"

  # '별표 4' 만 내려받아 파싱
  law_fetch.py fetch --query "식품등의 표시기준" --byl "별표 4" --parse

  # 조문 본문을 구조화해 마크다운으로
  law_fetch.py articles --query "식품위생법" --target law --article 1-5 --format markdown

  # 지식베이스로 내보내기
  law_fetch.py export --query "식품의 기준 및 규격" --codex food --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lawapi import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    DEFAULT_RETRIES,
    DEFAULT_TTL,
    HOST,
    Cache,
    LawApiError,
    LawClient,
    NetworkBlockedError,
)
from parsers import (  # noqa: E402
    articles_to_kb_entries,
    byl_matches,
    byl_ref_from_item,
    extract_articles,
    filter_articles,
    merge_kb,
    parse_byl_specs,
    render,
)

# korean-doc-parser 스킬의 파서(같은 skills/ 아래에 있다고 가정)
PARSER = Path(__file__).resolve().parents[2] / "korean-doc-parser" / "scripts" / "parse_doc.py"

# --codex 축약어 → food-code-analyzer 데이터 파일
CODEX_FILES = {
    "food": "food-codex.json",
    "additives": "food-additives.json",
    "health": "health-food-codex.json",
    "equipment": "equipment-packaging.json",
}
CODEX_CATEGORY = {
    "food": "식품공전",
    "additives": "식품첨가물공전",
    "health": "건강기능식품공전",
    "equipment": "기구 및 용기·포장공전",
}

# import 이름과 pip 패키지 이름이 다른 것들 — 안내에 틀린 명령을 주지 않도록
PIP_NAME = {
    "PIL": "pillow",
    "docx": "python-docx",
    "fitz": "pymupdf",
    "cv2": "opencv-python",
    "yaml": "PyYAML",
    "sklearn": "scikit-learn",
    "hwp_hwpx_parser": "hwp-hwpx-parser",
}

LINK_KEY_RE = re.compile(r"(링크|파일|다운로드)", re.I)
PATH_RE = re.compile(r"^(https?://|/)")


def probe_module(mod: str, _import=None) -> tuple[bool, str]:
    """모듈이 실제로 import 되는지 확인한다.

    '미설치'와 '설치됐지만 전이 의존성 누락'을 구분한다 — 이미 설치한 사람에게
    '설치하세요'라고 안내하면 헛돌게 되기 때문이다. pip 패키지명이 import 명과
    다른 경우(PIL→pillow 등)도 올바른 명령을 안내한다.

    _import 는 테스트에서 주입하기 위한 훅이다.
    """
    importer = _import or __import__
    try:
        importer(mod)
        return True, ""
    except ImportError as exc:
        missing = (getattr(exc, "name", "") or "").split(".")[0]
        if missing and missing != mod.split(".")[0]:
            return False, f"설치됨, 의존성 '{missing}' 누락 → pip install {PIP_NAME.get(missing, missing)}"
        return False, f"미설치 → pip install {PIP_NAME.get(mod, mod)}"
    except Exception as exc:
        return False, f"import 실패: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# 응답에서 항목 뽑기
# ---------------------------------------------------------------------------


def _title_of(node: dict) -> str | None:
    for k, v in node.items():
        if isinstance(v, str) and v.strip() and ("별표명" in k or "제목" in k or k.endswith("명")):
            return v.strip()
    return None


def extract_attachments(payload) -> list[dict]:
    """검색/상세 JSON에서 첨부(별표·서식) 레코드를 수집한다.

    반환: [{"title", "url", "ref": BylRef|None, "fmt": "PDF"|"HWP/기타"}]
    필드명이 API 버전마다 달라, (키에 링크/파일 포함) & (값이 URL/경로) 를 후보로 본다.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            title = _title_of(node)
            ref = byl_ref_from_item(node)
            for k, v in node.items():
                if isinstance(v, str) and LINK_KEY_RE.search(k) and PATH_RE.match(v.strip()):
                    url = urljoin(HOST, v.strip())
                    if url in seen:
                        continue
                    seen.add(url)
                    out.append(
                        {
                            "title": title or k,
                            "url": url,
                            "ref": ref,
                            "fmt": "PDF" if "PDF" in k.upper() else "HWP/기타",
                        }
                    )
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return out


def search_items(payload) -> list[dict]:
    """검색 응답에서 결과 항목 리스트를 뽑는다(최상위 키가 target 마다 다름)."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict) or not payload:
        return []
    root = next(iter(payload.values()))
    if isinstance(root, dict):
        for v in root.values():
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict) and any("명" in k for k in v):
                return [v]
    return []


def _field(item: dict, *needles, default=None):
    for k, v in item.items():
        if any(n in k for n in needles) and v not in (None, "", []):
            return v
    return default


def item_name(item: dict) -> str:
    return str(_field(item, "명", default="?"))


def item_seq(item: dict) -> str | None:
    for k, v in item.items():
        if ("일련번호" in k or k.endswith("ID")) and v:
            return str(v)
    return None


def detail_title(detail) -> str | None:
    found = []

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if isinstance(v, str) and k.endswith("명") and v.strip():
                    found.append(v.strip())
                else:
                    walk(v)
        elif isinstance(n, list):
            for i in n:
                walk(i)

    walk(detail)
    return found[0] if found else None


# ---------------------------------------------------------------------------
# 오케스트레이션
# ---------------------------------------------------------------------------


def make_client(args) -> LawClient:
    return LawClient(
        oc=getattr(args, "oc", None),
        cache_dir=Path(getattr(args, "cache_dir", DEFAULT_CACHE_DIR)),
        ttl=-1 if getattr(args, "ttl", DEFAULT_TTL) is None else args.ttl,
        use_cache=not getattr(args, "no_cache", False) and not getattr(args, "refresh", False),
        timeout=(getattr(args, "connect_timeout", 10.0), getattr(args, "read_timeout", 60.0)),
        retries=getattr(args, "retries", DEFAULT_RETRIES),
        verbose=not getattr(args, "quiet", False),
    )


def pick_best_match(items: list[dict], query: str) -> tuple[dict, str]:
    """검색 결과 중 질의에 가장 맞는 항목을 고른다.

    법제처 검색은 부분일치라 '식품등의 표시기준' 으로 찾아도
    '식품등의 부당한 표시 또는 광고의 내용 기준' 이 먼저 올 수 있다.
    무조건 items[0] 을 쓰면 엉뚱한 고시를 조용히 집어온다.

    반환: (선택된 항목, 선택 근거)
    """
    def norm(s: str) -> str:
        return re.sub(r"\s+", "", s or "")

    q = norm(query)
    for label, pred in (
        ("정확히 일치", lambda n: n == q),
        ("접두 일치", lambda n: n.startswith(q)),
        ("부분 일치", lambda n: q in n),
    ):
        for it in items:
            if pred(norm(item_name(it))):
                return it, label
    return items[0], "일치 없음 — 첫 결과"


def resolve_seq(client: LawClient, args) -> str:
    """--query 가 있으면 검색해 가장 잘 맞는 결과의 일련번호를 쓴다."""
    if getattr(args, "query", None):
        items = search_items(client.search(args.query, args.target, args.display))
        if not items:
            sys.exit(f"'{args.query}' 검색 결과가 없습니다. --target 을 확인하세요(law/admrul/ordin).")

        chosen, why = pick_best_match(items, args.query)
        seq = item_seq(chosen)
        if not getattr(args, "quiet", False):
            print(f"[선택] {seq}  {item_name(chosen)}  ({why})", file=sys.stderr)
            if why != "정확히 일치" and len(items) > 1:
                print(
                    f"  이름이 정확히 같은 결과가 없어 {len(items)}건 중에서 골랐습니다.\n"
                    "  의도한 고시가 아니면 `search` 로 확인 후 일련번호를 직접 지정하세요:",
                    file=sys.stderr,
                )
                for it in items[:5]:
                    print(f"    {item_seq(it)}\t{item_name(it)}", file=sys.stderr)
        if seq is None:
            sys.exit("검색 결과에서 일련번호를 찾지 못했습니다.")
        return seq
    if not getattr(args, "seq", None):
        sys.exit("일련번호(seq) 또는 --query 중 하나가 필요합니다.")
    return args.seq


def collect_attachments(client: LawClient, args) -> list[dict]:
    """--via 에 따라 별표 목록을 모은다. detail 이 비면 admbyl 로 폴백."""
    if args.via == "admbyl":
        query = args.query or args.seq
        return extract_attachments(client.search(query, "admbyl", display=100))

    seq = resolve_seq(client, args)
    detail = client.service(seq, args.target)
    records = extract_attachments(detail)
    if not records:
        fallback = args.query or detail_title(detail) or str(seq)
        print(f"[폴백] 본문에 별표 링크가 없어 admbyl 로 직접 조회: {fallback}", file=sys.stderr)
        records = extract_attachments(client.search(fallback, "admbyl", display=100))
    return records


def apply_byl_filter(records: list[dict], spec: str | None) -> list[dict]:
    """--byl 지정에 맞는 별표만 남긴다. 못 찾으면 있는 목록을 보여주고 종료."""
    if not spec:
        return records
    specs = parse_byl_specs(spec)
    kept = [r for r in records if any(byl_matches(s, r["ref"]) for s in specs)]
    if not kept:
        available = sorted({r["ref"].label() for r in records if r["ref"]})
        sys.exit(
            f"'{spec}' 에 해당하는 별표를 찾지 못했습니다.\n"
            f"  인식된 별표: {', '.join(available) if available else '(번호를 식별할 수 없음)'}\n"
            "  전체 목록은 `annexes` 명령으로 확인하세요."
        )
    return kept


def safe_filename(name: str) -> str:
    """경로 구분자·금지문자를 치환한다. 알맹이가 없으면 기본 이름을 쓴다."""
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", name or "").strip()
    return cleaned if cleaned.strip("_. ") else "attachment"


_MAGIC = [(b"%PDF", ".pdf"), (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".hwp"), (b"PK\x03\x04", ".zip")]


def sniff_ext(blob: bytes) -> str:
    import io
    import zipfile

    for magic, ext in _MAGIC:
        if blob.startswith(magic):
            if ext != ".zip":
                return ext
            try:
                names = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
            except Exception:
                return ".zip"
            if "mimetype" in names or any(n.startswith("Contents/") for n in names):
                return ".hwpx"
            if any(n.startswith("word/") for n in names):
                return ".docx"
            return ".zip"
    return ""


def download_record(
    client: LawClient, rec: dict, outdir: Path, idx: int, taken: set[str] | None = None
) -> Path:
    resp = client.download(rec["url"])
    blob = resp.content
    ctype = resp.headers.get("Content-Type", "").lower()
    if blob[:16].lstrip().startswith((b"<!DOCTYPE", b"<html", b"<?xml")) and "html" in ctype:
        print(
            f"  [경고] 파일이 아니라 HTML 뷰어 페이지가 반환됨: {rec['url']}",
            file=sys.stderr,
        )

    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", cd)
    from urllib.parse import unquote

    base = unquote(m.group(1)).strip('"') if m else ""
    if not base:
        label = rec["ref"].label() if rec["ref"] else f"attachment_{idx}"
        base = f"{label}_{safe_filename(rec['title'])[:40]}"
    if not Path(base).suffix:
        base += sniff_ext(blob[:16]) or ""

    outdir.mkdir(parents=True, exist_ok=True)
    name = safe_filename(base)
    # 한 번의 실행 안에서 이름이 겹치면(예: 같은 별표의 HWP/PDF) 덮어쓰지 않는다.
    if taken is not None and name in taken:
        stem, suffix = Path(name).stem, Path(name).suffix
        n = 2
        while f"{stem}({n}){suffix}" in taken:
            n += 1
        name = f"{stem}({n}){suffix}"
    if taken is not None:
        taken.add(name)

    path = outdir / name
    path.write_bytes(blob)
    return path


def parse_file(path: Path) -> str:
    if not PARSER.exists():
        return f"[파서 없음: {PARSER}] korean-doc-parser 스킬을 함께 설치하세요."
    r = subprocess.run([sys.executable, str(PARSER), str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        return f"[파싱 실패] {r.stderr.strip()}"
    return r.stdout


# ---------------------------------------------------------------------------
# 서브커맨드
# ---------------------------------------------------------------------------


def cmd_search(args):
    client = make_client(args)
    items = search_items(client.search(args.query, args.target, args.display))
    if not items:
        print("검색 결과 없음")
        return
    if args.format == "json":
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    for it in items:
        print(f"{item_seq(it) or '?'}\t{item_name(it)}")


def cmd_annexes(args):
    client = make_client(args)
    records = collect_attachments(client, args)
    if not records:
        print("별표·서식 첨부가 없습니다.")
        return
    records = apply_byl_filter(records, args.byl)
    if args.format == "json":
        print(
            json.dumps(
                [{**r, "ref": r["ref"].to_dict() if r["ref"] else None} for r in records],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    for r in records:
        label = r["ref"].label() if r["ref"] else "(번호 미상)"
        print(f"{label}\t[{r['fmt']}]\t{r['title']}\t{r['url']}")


def cmd_attachments(args):
    """하위 호환: 일련번호로 첨부 링크만 나열."""
    client = make_client(args)
    records = extract_attachments(client.service(args.seq, args.target))
    if not records:
        print("첨부파일(별표/서식) 링크를 찾지 못함. 본문 조문만 있을 수 있습니다.")
        return
    for r in records:
        print(f"{r['title']}\t{r['url']}")


def cmd_fetch(args):
    client = make_client(args)
    records = apply_byl_filter(collect_attachments(client, args), args.byl)
    if not records:
        print("첨부파일 링크 없음")
        return

    outdir = Path(args.outdir)
    results = []
    taken: set[str] = set()
    for i, rec in enumerate(records):
        path = download_record(client, rec, outdir, i, taken)
        label = rec["ref"].label() if rec["ref"] else "(번호 미상)"
        print(f"[다운로드] {label} {rec['title']} -> {path}", file=sys.stderr)
        entry = {"별표": label, "제목": rec["title"], "파일": str(path), "형식": rec["fmt"]}
        if args.parse:
            text = parse_file(path)
            if args.grep:
                hits = [ln for ln in text.splitlines() if args.grep in ln]
                text = "\n".join(hits) if hits else f"('{args.grep}' 미포함)"
            entry["본문"] = text
        results.append(entry)

    if args.format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif args.parse:
        for e in results:
            print(f"\n===== {e['별표']} {e['제목']} ({Path(e['파일']).name}) =====")
            print(e.get("본문", ""))


def cmd_articles(args):
    client = make_client(args)
    seq = resolve_seq(client, args)
    detail = client.service(seq, args.target)
    articles = filter_articles(extract_articles(detail), args.article)
    if not articles:
        sys.exit(
            "조문을 추출하지 못했습니다.\n"
            "  이 고시는 본문이 비어 있고 내용이 별표 첨부파일에 있을 수 있습니다.\n"
            "  `annexes` 또는 `fetch --byl` 로 별표를 확인하세요."
        )
    sys.stdout.write(render(articles, args.format, title=detail_title(detail)))


def cmd_export(args):
    client = make_client(args)
    seq = resolve_seq(client, args)
    detail = client.service(seq, args.target)
    articles = filter_articles(extract_articles(detail), args.article)
    if not articles:
        sys.exit("조문을 추출하지 못해 내보낼 내용이 없습니다.")

    category = args.category or CODEX_CATEGORY.get(args.codex, args.codex)
    source = detail_title(detail) or f"법제처 {seq}"
    entries = articles_to_kb_entries(articles, category, source)

    out_path = (
        Path(args.out)
        if args.out
        else Path(__file__).resolve().parents[2]
        / "food-code-analyzer"
        / "data"
        / CODEX_FILES[args.codex]
    )
    existing = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            sys.exit(f"기존 지식베이스가 올바른 JSON 이 아닙니다: {out_path}")

    merged, added, updated = merge_kb(existing, entries)
    merged.setdefault("metadata", {}).update({"name": category, "source": source})

    if args.dry_run:
        print(f"[미리보기] {out_path}")
        print(f"  추가 {added}건 / 갱신 {updated}건")
        for e in entries[:5]:
            print(f"  - {e['standard'] or ''} {e['term']}: {e['definition'][:60]}...")
        if len(entries) > 5:
            print(f"  ... 외 {len(entries) - 5}건")
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[저장] {out_path} — 추가 {added}건 / 갱신 {updated}건")


def cmd_doctor(args):
    """설치·인증·네트워크·파싱 의존성을 한 번에 점검한다.

    이 스킬은 막히는 지점이 여럿이라(egress 차단, OC 미설정, IP 미등록,
    파서 누락) 어디서 걸렸는지 바로 짚어주는 편이 낫다.
    """
    rows: list[tuple[str, str, str]] = []  # (항목, 상태, 설명)

    def add(label, ok, detail=""):
        rows.append((label, {True: "OK", False: "FAIL", None: "WARN"}[ok], detail))

    # 1. 파이썬 / requests
    add("Python", True, f"{sys.version.split()[0]}")
    try:
        import requests as _rq

        add("requests", True, _rq.__version__)
    except ImportError:
        add("requests", False, "pip install requests")

    # 2. 인증키
    oc = (getattr(args, "oc", None) or __import__("os").environ.get("LAW_GO_KR_OC", "")).strip()
    add("OC 인증키", bool(oc), f"{oc[:2]}***" if oc else "export LAW_GO_KR_OC=<발급받은 아이디>")

    # 3. 네트워크 도달
    net_ok = False
    try:
        import requests as _rq

        r = _rq.get(HOST, timeout=(5, 15))
        net_ok = r.ok
        add("law.go.kr 접속", net_ok, f"HTTP {r.status_code}")
    except Exception as exc:
        add(
            "law.go.kr 접속",
            False,
            f"{type(exc).__name__} — egress 정책이 www.law.go.kr 을 막고 있을 수 있음",
        )

    # 4. API 인증(실호출) — 여기가 IP 등록 여부를 가르는 지점
    if oc and net_ok:
        try:
            client = LawClient(oc=oc, use_cache=False, retries=1, verbose=False)
            items = search_items(client.search("식품", "admrul", display=1))
            add("API 인증", bool(items), f"검색 결과 {len(items)}건" if items else "결과 0건")
        except NetworkBlockedError:
            add("API 인증", False, "네트워크 차단")
        except LawApiError as exc:
            first = str(exc).splitlines()[0]
            add("API 인증", False, f"{first} → OC 키 또는 호출 IP 등록 확인")
    else:
        add("API 인증", None, "OC 또는 네트워크가 준비되지 않아 건너뜀")

    # 5. 문서 파서
    add("korean-doc-parser", PARSER.exists(), str(PARSER) if PARSER.exists() else f"없음: {PARSER}")
    for mod, why in (
        ("hwp_hwpx_parser", ".hwp/.hwpx"),
        ("pymupdf", ".pdf"),
        ("docx", ".docx"),
        ("mammoth", ".doc"),
    ):
        ok, detail = probe_module(mod)
        add(f"  {mod}", ok, why if ok else f"{why} 파싱 불가 — {detail}")
    for mod in ("paddleocr", "pytesseract"):
        ok, detail = probe_module(mod)
        add(f"  {mod}", True if ok else None, "스캔 PDF OCR" if ok else f"선택 사항 — {detail}")

    # 6. 캐시 쓰기 가능 여부
    cache_root = Path(getattr(args, "cache_dir", DEFAULT_CACHE_DIR))
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        probe = cache_root / ".write-probe"
        probe.write_text("x")
        probe.unlink()
        add("캐시 디렉터리", True, str(cache_root))
    except OSError as exc:
        add("캐시 디렉터리", False, f"{cache_root} 쓰기 불가 — {exc}")

    if args.format == "json":
        print(json.dumps([{"항목": a, "상태": b, "설명": c} for a, b, c in rows], ensure_ascii=False, indent=2))
    else:
        mark = {"OK": "✅", "FAIL": "❌", "WARN": "⚠️ "}
        width = max(len(a) for a, _, _ in rows)
        for label, status, detail in rows:
            print(f"{mark[status]} {label.ljust(width)}  {detail}")

    failed = [a.strip() for a, s, _ in rows if s == "FAIL"]
    if failed:
        print(f"\n문제 {len(failed)}건: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)
    print("\n모든 점검을 통과했습니다.", file=sys.stderr)


def cmd_cache(args):
    cache = Cache(Path(args.cache_dir), ttl=args.ttl if args.ttl is not None else DEFAULT_TTL)
    if args.action == "clear":
        print(f"캐시 {cache.clear()}개 파일 삭제: {cache.root}")
        return
    if not cache.root.exists():
        print(f"캐시 없음: {cache.root}")
        return
    files = [p for p in cache.root.iterdir() if p.is_file() and not p.name.endswith(".hdr")]
    total = sum(p.stat().st_size for p in cache.root.iterdir() if p.is_file())
    print(f"위치: {cache.root}\n항목: {len(files)}개\n용량: {total / 1024:.1f} KiB\nTTL: {cache.ttl}s")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def add_common(p, *, need_target=True):
    p.add_argument("--oc", help="법제처 OPEN API 인증키(기본: $LAW_GO_KR_OC)")
    if need_target:
        p.add_argument("--target", default="admrul", help="law | admrul | ordin (기본 admrul)")
        p.add_argument("--display", type=int, default=20)
    p.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    p.add_argument("--no-cache", action="store_true", help="캐시를 읽지도 쓰지도 않음")
    p.add_argument("--refresh", action="store_true", help="캐시를 무시하고 새로 받아 갱신")
    p.add_argument("--ttl", type=int, default=DEFAULT_TTL, help=f"캐시 유효기간(초, 기본 {DEFAULT_TTL})")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    p.add_argument("--connect-timeout", type=float, default=10.0)
    p.add_argument("--read-timeout", type=float, default=60.0)
    p.add_argument("--quiet", "-q", action="store_true", help="진행 로그 숨김")


def main():
    ap = argparse.ArgumentParser(
        description="법제처 국가법령정보 OPEN API 클라이언트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            '  law_fetch.py search "식품등의 표시기준"\n'
            '  law_fetch.py annexes --query "식품등의 표시기준"\n'
            '  law_fetch.py fetch --query "식품등의 표시기준" --byl "별표 4" --parse\n'
            '  law_fetch.py articles --query "식품위생법" --target law --article 1-5 --format markdown\n'
            '  law_fetch.py export --query "식품의 기준 및 규격" --codex food --dry-run\n'
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="법령/행정규칙 검색")
    p.add_argument("query")
    add_common(p)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("annexes", help="별표·서식 목록(번호 인식 포함)")
    p.add_argument("seq", nargs="?")
    p.add_argument("--query")
    p.add_argument("--byl", help="'별표 4', '4', '별지 1', '별표 4,별표 5'")
    p.add_argument("--via", choices=["detail", "admbyl"], default="detail")
    add_common(p)
    p.set_defaults(func=cmd_annexes)

    p = sub.add_parser("attachments", help="[하위호환] 일련번호로 첨부 링크 나열")
    p.add_argument("seq")
    add_common(p)
    p.set_defaults(func=cmd_attachments)

    p = sub.add_parser("fetch", help="별표 첨부 다운로드(+파싱)")
    p.add_argument("seq", nargs="?")
    p.add_argument("--query", help="이름으로 검색해 첫 결과를 사용")
    p.add_argument("--byl", help="특정 별표만: '별표 4', '4', '별표 4,별표 5'")
    p.add_argument("--via", choices=["detail", "admbyl"], default="detail")
    p.add_argument("--outdir", default="./law_attachments")
    p.add_argument("--parse", action="store_true", help="다운로드 후 텍스트 파싱")
    p.add_argument("--grep", help="파싱 결과에서 키워드 포함 줄만 출력")
    add_common(p)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("articles", help="조문 본문을 조/항/호/목으로 구조화")
    p.add_argument("seq", nargs="?")
    p.add_argument("--query")
    p.add_argument("--article", help="'3', '3의2', '1-5', '1,3,5'")
    add_common(p)
    p.set_defaults(func=cmd_articles)

    p = sub.add_parser("export", help="조문을 food-code-analyzer 지식베이스로 내보내기")
    p.add_argument("seq", nargs="?")
    p.add_argument("--query")
    p.add_argument("--codex", choices=sorted(CODEX_FILES), default="food")
    p.add_argument("--category", help="entry 의 category 값(기본: 공전명)")
    p.add_argument("--article", help="특정 조문만 내보내기")
    p.add_argument("--out", help="출력 JSON 경로(기본: food-code-analyzer/data/<codex>.json)")
    p.add_argument("--dry-run", action="store_true", help="쓰지 않고 미리보기만")
    add_common(p)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("doctor", help="설치·인증·네트워크·의존성 한 번에 점검")
    p.add_argument("--oc")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("cache", help="캐시 정보 확인/삭제")
    p.add_argument("action", choices=["info", "clear"], nargs="?", default="info")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    p.set_defaults(func=cmd_cache)

    args = ap.parse_args()
    if args.cmd in ("fetch", "annexes", "articles", "export"):
        if not getattr(args, "seq", None) and not getattr(args, "query", None):
            ap.error(f"{args.cmd} 는 seq 또는 --query 중 하나가 필요합니다")

    try:
        args.func(args)
    except NetworkBlockedError as e:
        print(f"\n[차단] {e}", file=sys.stderr)
        sys.exit(2)
    except LawApiError as e:
        print(f"\n[오류] {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
