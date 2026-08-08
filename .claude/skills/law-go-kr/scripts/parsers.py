#!/usr/bin/env python3
"""법제처 응답 파싱 계층 — 네트워크에 의존하지 않는 순수 함수 모음.

세 가지를 담당한다:
  1) 별표 참조 파싱·매칭   — "별표 4", "별표4의2", "별지 1", "4" 를 정규화해 선별
  2) 조문 본문 구조화      — 조 / 항 / 호 / 목 계층 트리로 분해
  3) 출력 포맷            — text / json / markdown, food-code-analyzer 내보내기

네트워크를 타지 않으므로 단위 테스트로 전부 검증할 수 있다(tests/ 참고).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict

# ---------------------------------------------------------------------------
# 1. 별표 참조 (별표 / 별지 / 서식 / 별도)
# ---------------------------------------------------------------------------

BYL_KINDS = ("별표", "별지", "서식", "별도")

# "[별표 4]", "별표4의2", "별표 제4호", "별지 제1호서식" 등을 모두 잡는다.
_BYL_REF_RE = re.compile(
    r"(별표|별지|서식|별도)\s*(?:제\s*)?(\d+)\s*(?:의\s*(\d+))?"
)
# 종류 없이 번호만 준 스펙: "4", "4의2"
_BARE_NUM_RE = re.compile(r"^\s*(\d+)\s*(?:의\s*(\d+))?\s*$")


@dataclass(frozen=True)
class BylRef:
    """별표 참조. kind/number 가 None 이면 '지정 안 함'(와일드카드)을 뜻한다."""

    kind: str | None = None
    number: int | None = None
    branch: int | None = None  # 가지번호: "별표 4의2" → 2

    def label(self) -> str:
        if self.number is None:
            return self.kind or "?"
        base = f"{self.kind or '별표'} {self.number}"
        return f"{base}의{self.branch}" if self.branch else base

    def to_dict(self) -> dict:
        return asdict(self)


def parse_byl_ref(text: str) -> BylRef | None:
    """제목 문자열에서 첫 번째 별표 참조를 뽑는다. 없으면 None."""
    if not text:
        return None
    m = _BYL_REF_RE.search(text)
    if not m:
        return None
    kind, num, branch = m.group(1), m.group(2), m.group(3)
    return BylRef(kind, int(num), int(branch) if branch else None)


def parse_byl_spec(spec: str) -> BylRef:
    """사용자가 --byl 로 준 값을 파싱한다.

    "별표 4" / "별표4" / "별표 제4호" / "4" / "4의2" / "별지 1" 을 모두 허용.
    종류를 안 적으면 kind=None(모든 종류 매칭).
    """
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("별표 지정이 비어 있습니다")

    bare = _BARE_NUM_RE.match(spec)
    if bare:
        return BylRef(None, int(bare.group(1)), int(bare.group(2)) if bare.group(2) else None)

    ref = parse_byl_ref(spec)
    if ref is None:
        # 종류만 준 경우: "별표"
        for k in BYL_KINDS:
            if spec.replace(" ", "") == k:
                return BylRef(k, None, None)
        raise ValueError(f"별표 지정을 이해할 수 없습니다: {spec!r} (예: '별표 4', '4', '별지 1')")
    return ref


def parse_byl_specs(spec: str) -> list[BylRef]:
    """쉼표로 구분된 여러 지정을 파싱: '별표 4, 별표 5' → [BylRef, BylRef]"""
    return [parse_byl_spec(part) for part in spec.split(",") if part.strip()]


def byl_matches(spec: BylRef, ref: BylRef | None) -> bool:
    """스펙이 참조와 일치하는지. 스펙에서 None 인 필드는 아무거나 허용한다.

    가지번호는 정확히 비교한다 — '별표 4' 를 요청하면 '별표 4의2' 는 매칭하지 않는다.
    (엉뚱한 별표를 조용히 가져오는 것보다 못 찾았다고 알려주는 편이 낫다.)
    """
    if ref is None:
        return False
    if spec.kind is not None and spec.kind != ref.kind:
        return False
    if spec.number is not None and spec.number != ref.number:
        return False
    return spec.branch == ref.branch


def byl_ref_from_item(item: dict) -> BylRef | None:
    """admbyl 응답 항목에서 별표 참조를 만든다.

    API 가 별표번호/별표가지번호/별표종류 필드를 주면 그걸 쓰고,
    없으면 제목 문자열에서 파싱한다.
    """
    if not isinstance(item, dict):
        return None

    def pick(*needles):
        for k, v in item.items():
            if any(n in k for n in needles) and v not in (None, "", []):
                return v
        return None

    num = pick("별표번호")
    if num is not None:
        try:
            branch = pick("가지번호")
            branch_i = int(branch) if branch not in (None, "", "0") else None
            kind = pick("별표종류") or "별표"
            kind = next((k for k in BYL_KINDS if k in str(kind)), "별표")
            return BylRef(kind, int(num), branch_i)
        except (TypeError, ValueError):
            pass

    title = pick("별표명", "제목", "명")
    return parse_byl_ref(str(title)) if title else None


# ---------------------------------------------------------------------------
# 2. 조문 구조화 (조 / 항 / 호 / 목)
# ---------------------------------------------------------------------------

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_CIRCLED_VALUE = {ch: i + 1 for i, ch in enumerate(CIRCLED)}
_MOK_LETTERS = "가나다라마바사아자차카타파하"

_ART_RE = re.compile(r"^\s*제\s*(\d+)\s*조\s*(?:의\s*(\d+))?\s*(?:\(([^)]*)\))?\s*(.*)$")
_PARA_CIRCLED_RE = re.compile(rf"^\s*([{CIRCLED}])\s*(.*)$")
_PARA_PAREN_RE = re.compile(r"^\s*\((\d{1,2})\)\s*(.*)$")
_HO_RE = re.compile(r"^\s*(\d{1,2})\s*\.\s*(.*)$")
_MOK_RE = re.compile(rf"^\s*([{_MOK_LETTERS}])\s*\.\s*(.*)$")

_TAG_RE = re.compile(r"<[^>]+>")
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


def strip_markup(text: str) -> str:
    """법제처 응답에 섞여 오는 CDATA/HTML 태그/엔티티를 걷어낸다."""
    if not text:
        return ""
    text = _CDATA_RE.sub(r"\1", text)
    text = _TAG_RE.sub("", text)
    for ent, ch in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"), ("&quot;", '"'), ("&nbsp;", " ")):
        text = text.replace(ent, ch)
    return text


def parse_articles(text: str) -> list[dict]:
    """조문 텍스트를 조/항/호/목 트리로 분해한다.

    반환: [{"번호": "1", "가지": None, "제목": "목적", "내용": "...",
            "항": [{"번호": 1, "내용": "...", "호": [{"번호": "1", "내용": "...",
                    "목": [{"번호": "가", "내용": "..."}]}]}]}]

    조 헤더가 하나도 없으면 빈 리스트를 반환한다(호출부에서 원문 폴백 판단).
    """
    text = strip_markup(text)
    articles: list[dict] = []
    cur_art = cur_para = cur_ho = None

    def new_article(num, branch, title, rest):
        nonlocal cur_art, cur_para, cur_ho
        cur_art = {
            "번호": num,
            "가지": branch,
            "제목": title,
            "내용": rest.strip(),
            "항": [],
        }
        cur_para = cur_ho = None
        articles.append(cur_art)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        m = _ART_RE.match(line)
        if m and m.group(1):
            new_article(m.group(1), m.group(2), (m.group(3) or "").strip() or None, m.group(4))
            continue

        if cur_art is None:
            # 조 헤더 이전의 머리말은 버리지 않고 임시 조에 담는다.
            new_article(None, None, None, "")
            cur_art["내용"] = line
            continue

        m = _PARA_CIRCLED_RE.match(line)
        if m:
            cur_para = {"번호": _CIRCLED_VALUE[m.group(1)], "내용": m.group(2).strip(), "호": []}
            cur_ho = None
            cur_art["항"].append(cur_para)
            continue

        m = _PARA_PAREN_RE.match(line)
        if m:
            cur_para = {"번호": int(m.group(1)), "내용": m.group(2).strip(), "호": []}
            cur_ho = None
            cur_art["항"].append(cur_para)
            continue

        m = _HO_RE.match(line)
        if m:
            if cur_para is None:
                cur_para = {"번호": None, "내용": "", "호": []}
                cur_art["항"].append(cur_para)
            cur_ho = {"번호": m.group(1), "내용": m.group(2).strip(), "목": []}
            cur_para["호"].append(cur_ho)
            continue

        m = _MOK_RE.match(line)
        if m and cur_ho is not None:
            cur_ho["목"].append({"번호": m.group(1), "내용": m.group(2).strip()})
            continue

        # 분류 안 되는 줄은 가장 가까운 노드의 내용에 이어 붙인다.
        target = cur_ho or cur_para or cur_art
        key = "내용"
        target[key] = (target.get(key, "") + " " + line).strip()

    # 조 번호가 하나도 없으면 구조화 실패로 본다.
    if all(a["번호"] is None for a in articles):
        return []
    return articles


def _iter_dicts(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_dicts(v)


def extract_articles(detail: dict) -> list[dict]:
    """상세 JSON에서 조문 트리를 뽑는다.

    1순위: '조문단위' 배열(법령 target 의 구조화 응답)
    2순위: 조문 관련 텍스트 blob 을 모아 parse_articles 로 파싱
    """
    units: list[dict] = []
    for d in _iter_dicts(detail):
        for k, v in d.items():
            if "조문단위" in k and isinstance(v, list):
                units.extend(x for x in v if isinstance(x, dict))

    if units:
        out = []
        for u in units:
            def g(*needles, default=""):
                for k, v in u.items():
                    if any(n in k for n in needles) and isinstance(v, str):
                        return strip_markup(v).strip()
                return default

            paras = []
            for k, v in u.items():
                if k.startswith("항") and isinstance(v, list):
                    for p in v:
                        if not isinstance(p, dict):
                            continue
                        pnum = next(
                            (strip_markup(str(x)) for kk, x in p.items() if "번호" in kk), None
                        )
                        pbody = next(
                            (strip_markup(str(x)) for kk, x in p.items() if "내용" in kk), ""
                        )
                        paras.append({"번호": pnum, "내용": pbody.strip(), "호": []})
            out.append(
                {
                    "번호": g("조문번호", default=None) or None,
                    "가지": g("조문가지번호", default=None) or None,
                    "제목": g("조문제목", default=None) or None,
                    "내용": g("조문내용"),
                    "항": paras,
                }
            )
        return out

    blobs = []
    for d in _iter_dicts(detail):
        for k, v in d.items():
            if isinstance(v, str) and ("조문" in k or "내용" in k) and len(v) > 40:
                blobs.append(v)
    return parse_articles("\n".join(blobs)) if blobs else []


def filter_articles(articles: list[dict], spec: str | None) -> list[dict]:
    """--article '3' / '3의2' / '1-5' 로 조문을 선별한다."""
    if not spec:
        return articles
    wanted_exact: set[tuple[str, str | None]] = set()
    ranges: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part and "의" not in part:
            lo, _, hi = part.partition("-")
            try:
                ranges.append((int(lo), int(hi)))
                continue
            except ValueError:
                pass
        num, _, branch = part.partition("의")
        wanted_exact.add((num.strip(), branch.strip() or None))

    def keep(a):
        num, branch = a.get("번호"), a.get("가지")
        if (str(num), str(branch) if branch else None) in wanted_exact:
            return True
        try:
            n = int(num)
        except (TypeError, ValueError):
            return False
        return any(lo <= n <= hi for lo, hi in ranges)

    return [a for a in articles if keep(a)]


# ---------------------------------------------------------------------------
# 3. 출력 포맷
# ---------------------------------------------------------------------------


def para_label(pn) -> str:
    """항 번호를 원문자로 표기한다 — 호('1.')와 눈으로 구분되게.

    JSON 응답은 이미 '①' 로 주는 경우가 있어 그대로 통과시킨다.
    """
    if pn in (None, ""):
        return ""
    if isinstance(pn, str) and pn.strip() and pn.strip()[0] in CIRCLED:
        return pn.strip()[0]
    try:
        n = int(pn)
    except (TypeError, ValueError):
        return str(pn)
    return CIRCLED[n - 1] if 1 <= n <= len(CIRCLED) else f"({n})"


def articles_to_text(articles: list[dict]) -> str:
    lines = []
    for a in articles:
        head = f"제{a['번호']}조" if a.get("번호") else "(조 번호 미상)"
        if a.get("가지"):
            head += f"의{a['가지']}"
        if a.get("제목"):
            head += f"({a['제목']})"
        lines.append(head)
        if a.get("내용"):
            lines.append(f"  {a['내용']}")
        for p in a.get("항", []):
            label = para_label(p.get("번호"))
            prefix = f"  {label} " if label else "  "
            lines.append(f"{prefix}{p.get('내용', '')}".rstrip())
            for h in p.get("호", []):
                lines.append(f"    {h.get('번호')}. {h.get('내용', '')}".rstrip())
                for mk in h.get("목", []):
                    lines.append(f"      {mk.get('번호')}. {mk.get('내용', '')}".rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n" if lines else ""


def articles_to_markdown(articles: list[dict], title: str | None = None) -> str:
    lines = [f"# {title}", ""] if title else []
    for a in articles:
        head = f"제{a['번호']}조" if a.get("번호") else "(조 번호 미상)"
        if a.get("가지"):
            head += f"의{a['가지']}"
        if a.get("제목"):
            head += f" ({a['제목']})"
        lines.append(f"## {head}")
        lines.append("")
        if a.get("내용"):
            lines.append(a["내용"])
            lines.append("")
        for p in a.get("항", []):
            label = para_label(p.get("번호"))
            lines.append(f"{label} {p.get('내용', '')}".strip() if label else p.get("내용", ""))
            for h in p.get("호", []):
                lines.append(f"    - **{h.get('번호')}.** {h.get('내용', '')}".rstrip())
                for mk in h.get("목", []):
                    lines.append(f"        - {mk.get('번호')}. {mk.get('내용', '')}".rstrip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render(payload, fmt: str, title: str | None = None) -> str:
    """조문 트리 또는 임의 데이터를 요청한 포맷으로 직렬화한다."""
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if isinstance(payload, list) and payload and isinstance(payload[0], dict) and "항" in payload[0]:
        return articles_to_markdown(payload, title) if fmt == "markdown" else articles_to_text(payload)
    if fmt == "markdown":
        return f"# {title}\n\n{payload}\n" if title else f"{payload}\n"
    return f"{payload}\n"


# ---------------------------------------------------------------------------
# 4. food-code-analyzer 내보내기
# ---------------------------------------------------------------------------

_DEF_RE = re.compile(r'^\s*(?:\d+[).]\s*)?"?([^"()]{2,40}?)"?\s*(?:이|란|라)\s*(?:함은|한다|은|는)?\s*(.+)$')


def articles_to_kb_entries(articles: list[dict], category: str, source: str) -> list[dict]:
    """조문 트리를 food-code-analyzer 지식베이스 entry 스키마로 변환한다.

    스키마: {"category", "term", "type", "definition", "standard"}
    조 제목을 term 으로, 조문 내용을 definition 으로 쓴다.
    """
    entries = []
    for a in articles:
        term = a.get("제목")
        body = a.get("내용", "").strip()
        if not term and not body:
            continue
        num = f"제{a['번호']}조" if a.get("번호") else ""
        if a.get("가지"):
            num += f"의{a['가지']}"
        parts = [body] if body else []
        for p in a.get("항", []):
            if p.get("내용"):
                parts.append(f"{p.get('번호') or ''} {p['내용']}".strip())
            for h in p.get("호", []):
                if h.get("내용"):
                    parts.append(f"  {h.get('번호')}. {h['내용']}")
        definition = " ".join(parts).strip()
        if not definition:
            continue
        entries.append(
            {
                "category": category,
                "term": term or num or "(제목 없음)",
                "type": "법령조문",
                "definition": definition[:2000],
                "standard": num or None,
                "source": source,
            }
        )
    return entries


def merge_kb(existing: dict, new_entries: list[dict]) -> tuple[dict, int, int]:
    """지식베이스 JSON 에 entry 를 병합한다. (결과, 추가수, 갱신수) 반환.

    term+category 가 같으면 갱신, 없으면 추가.
    """
    data = dict(existing) if existing else {}
    entries = list(data.get("entries", []))
    index = {(e.get("term"), e.get("category")): i for i, e in enumerate(entries)}
    added = updated = 0
    for e in new_entries:
        k = (e.get("term"), e.get("category"))
        if k in index:
            entries[index[k]] = e
            updated += 1
        else:
            index[k] = len(entries)
            entries.append(e)
            added += 1
    data["entries"] = entries
    return data, added, updated
