#!/usr/bin/env python3
"""법제처 국가법령정보 OPEN API(DRF) HTTP 계층.

이 모듈은 네트워크만 담당한다:
  - 지수 백오프 재시도(일시적 오류에만)
  - connect / read 타임아웃 분리
  - 디스크 캐시(TTL) — 같은 질의를 반복해도 API를 다시 때리지 않는다
  - 실패 원인 구분(프록시 차단 / DNS / 타임아웃 / 인증키 / API 오류)

별표 선별·조문 파싱·출력 포맷은 parsers.py 와 law_fetch.py 가 담당한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

HOST = "https://www.law.go.kr"
SEARCH_URL = HOST + "/DRF/lawSearch.do"
SERVICE_URL = HOST + "/DRF/lawService.do"

DEFAULT_CACHE_DIR = Path(
    os.environ.get("LAW_GO_KR_CACHE", Path.home() / ".cache" / "law-go-kr")
)
DEFAULT_TTL = 24 * 3600          # 하루. 고시는 자주 안 바뀐다.
DEFAULT_TIMEOUT = (10.0, 60.0)   # (connect, read) — 첨부 다운로드는 느릴 수 있다
DEFAULT_JSON_TIMEOUT = (10.0, 20.0)  # 검색·상세조회는 작은 JSON 이라 짧게 잡는다
DEFAULT_RETRIES = 4

# 재시도해도 의미가 있는 상태코드(일시적 장애·레이트리밋)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# 프록시/방화벽 차단으로 읽히는 예외 메시지 조각
_BLOCK_HINTS = (
    "connect tunnel failed",
    "tunnel connection failed",
    "proxyerror",
    "403",
    "forbidden",
    "connection refused",
    "network is unreachable",
)


class LawApiError(RuntimeError):
    """법제처 API 호출 관련 최상위 예외."""


class NetworkBlockedError(LawApiError):
    """egress 정책/프록시/방화벽이 law.go.kr 접근을 막은 경우."""


class ApiResponseError(LawApiError):
    """서버는 응답했지만 내용이 기대와 다른 경우(인증키 오류, HTML 반환 등)."""


def _looks_blocked(exc: Exception) -> bool:
    msg = f"{type(exc).__name__} {exc}".lower()
    return any(h in msg for h in _BLOCK_HINTS)


def _blocked_message(url: str, exc: Exception) -> str:
    return (
        f"law.go.kr 접근이 차단되었습니다 ({type(exc).__name__}).\n"
        f"  요청: {url}\n"
        "  원인: 실행 환경의 egress(아웃바운드) 정책이 www.law.go.kr 을 막고 있습니다.\n"
        "  해결:\n"
        "    1) Claude Code on the web → Environments → Custom network 에\n"
        "       www.law.go.kr, open.law.go.kr 을 허용 목록에 추가한 뒤 새 세션 시작\n"
        "    2) 또는 law.go.kr 접근이 되는 로컬/서버에서 run_local.sh 실행"
    )


def check_api_error(data, url: str = "") -> None:
    """법제처가 HTTP 200 으로 돌려주는 오류 봉투를 예외로 승격한다.

    정상 응답은 target 이름을 최상위 키로 쓴다(`{"AdmRulSearch": {...}}`,
    `{"행정규칙": {...}}`). 반면 오류는 다음처럼 온다:

        {"result": "사용자 정보 검증에 실패하였습니다.",
         "msg": "OPEN API 호출 시 사용자 검증을 위하여 ... 등록해 주세요."}

    이걸 그냥 통과시키면 상위에서 '검색 결과 없음' 으로 보여, 고칠 수 있는
    인증 문제가 조용히 묻힌다.
    """
    if not isinstance(data, dict):
        return
    result = data.get("result")
    if not isinstance(result, str) or not result.strip():
        return

    msg = str(data.get("msg", "")).strip()
    hint = ""
    if any(w in result + msg for w in ("검증", "IP", "등록", "인증", "권한")):
        hint = (
            "\n  참고: 법제처는 'OC 키가 틀린 경우'와 'IP 미등록'에 똑같은 메시지를\n"
            "        돌려준다. 메시지만으로는 둘을 구분할 수 없으니 아래를 모두 확인할 것.\n"
            "  해결:\n"
            "    1) open.law.go.kr 로그인 → OPEN API 신청 정보에서\n"
            "       '서버 IP / 도메인' 에 호출하는 장비의 아웃바운드 IP 가 등록됐는지\n"
            "    2) OC 값이 신청 시 아이디와 정확히 같은지\n"
            "    3) 원격/클라우드 실행 환경은 아웃바운드 IP 가 유동이라 등록이 잘 안 맞는다.\n"
            "       고정 IP 서버나 로컬 PC 에서 실행하는 편이 확실하다."
        )
    where = f"\n  요청: {url}" if url else ""
    raise ApiResponseError(f"법제처 API 오류: {result}" + (f"\n  {msg}" if msg else "") + hint + where)


class Response:
    """캐시 히트와 실제 응답을 같은 모양으로 다루기 위한 최소 래퍼."""

    __slots__ = ("content", "headers", "from_cache", "url", "cache_key")

    def __init__(self, content: bytes, headers: dict, from_cache: bool, url: str, cache_key: str = ""):
        self.content = content
        self.headers = headers
        self.from_cache = from_cache
        self.url = url
        self.cache_key = cache_key

    def text(self, encoding: str = "utf-8") -> str:
        return self.content.decode(encoding, errors="replace")


class Cache:
    """URL+파라미터 해시를 파일명으로 쓰는 단순 디스크 캐시.

    본문은 `<key>` 에, 응답 헤더는 `<key>.hdr` 에 저장한다.
    TTL 은 파일 mtime 기준. ttl < 0 이면 만료 없음.
    """

    def __init__(self, root: Path = DEFAULT_CACHE_DIR, ttl: int = DEFAULT_TTL, enabled: bool = True):
        self.root = Path(root)
        self.ttl = ttl
        self.enabled = enabled

    @staticmethod
    def key(url: str, params: dict) -> str:
        # OC(인증키)는 캐시 키에서 제외 — 같은 질의는 키가 달라도 같은 결과다.
        stable = sorted((k, str(v)) for k, v in params.items() if k != "OC")
        return hashlib.sha256(f"{url}?{urlencode(stable)}".encode()).hexdigest()[:32]

    def get(self, key: str) -> Response | None:
        if not self.enabled:
            return None
        body = self.root / key
        if not body.exists():
            return None
        if self.ttl >= 0 and (time.time() - body.stat().st_mtime) > self.ttl:
            return None
        hdr_path = self.root / f"{key}.hdr"
        headers = {}
        if hdr_path.exists():
            try:
                headers = json.loads(hdr_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                headers = {}
        return Response(body.read_bytes(), headers, from_cache=True, url="")

    def evict(self, key: str) -> None:
        """오류 응답이 캐시에 남지 않도록 지운다."""
        for p in (self.root / key, self.root / f"{key}.hdr"):
            try:
                p.unlink()
            except OSError:
                pass

    def put(self, key: str, content: bytes, headers: dict) -> None:
        if not self.enabled:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / key).write_bytes(content)
        keep = {
            k: v
            for k, v in headers.items()
            if k.lower() in ("content-disposition", "content-type")
        }
        (self.root / f"{key}.hdr").write_text(
            json.dumps(keep, ensure_ascii=False), encoding="utf-8"
        )

    def clear(self) -> int:
        if not self.root.exists():
            return 0
        n = 0
        for p in self.root.iterdir():
            if p.is_file():
                p.unlink()
                n += 1
        return n


def resolve_oc(explicit: str | None = None) -> str:
    oc = (explicit or os.environ.get("LAW_GO_KR_OC", "")).strip()
    if not oc:
        raise LawApiError(
            "법제처 OPEN API 인증키(OC)가 없습니다.\n"
            "  open.law.go.kr 에서 OPEN API 를 신청해 받은 아이디를 설정하세요:\n"
            "    export LAW_GO_KR_OC=your_oc_id\n"
            "  또는 --oc 옵션으로 직접 전달하세요."
        )
    return oc


class LawClient:
    """법제처 DRF API 클라이언트."""

    def __init__(
        self,
        oc: str | None = None,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        ttl: int = DEFAULT_TTL,
        use_cache: bool = True,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        json_timeout: tuple[float, float] = DEFAULT_JSON_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        verbose: bool = True,
    ):
        if requests is None:
            raise LawApiError("requests 패키지가 필요합니다: pip install requests")
        self.oc = resolve_oc(oc)
        self.cache = Cache(cache_dir, ttl, use_cache)
        self.timeout = timeout          # 첨부 다운로드용(느릴 수 있음)
        self.json_timeout = json_timeout  # 검색·상세조회용(작은 JSON, 짧게)
        self.retries = retries
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; law-go-kr-skill/2.0)",
                "Accept": "application/json, text/html, */*",
            }
        )

    # ---------- 저수준 ----------

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr)

    def request(self, url: str, params: dict, timeout: tuple[float, float] | None = None) -> Response:
        """GET 요청 + 캐시 + 재시도. 성공하면 Response, 실패하면 예외.

        timeout 을 안 주면 다운로드 기본값(self.timeout, read 60s)을 쓴다.
        JSON 메타데이터 호출(get_json)은 짧은 self.json_timeout 을 넘겨서,
        API 가 응답 없이 걸려 있을 때 재시도 5회 × 60s(최악 ~5분)가 아니라
        재시도 5회 × 20s(최악 ~100초) 안에 실패를 알 수 있게 한다. 첨부
        다운로드는 파일이 커서 시간이 걸릴 수 있으므로 긴 타임아웃을 유지한다.
        """
        key = Cache.key(url, params)
        hit = self.cache.get(key)
        if hit is not None:
            hit.url = url
            hit.cache_key = key
            self._log(f"  [캐시] {url}")
            return hit

        effective_timeout = timeout or self.timeout
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self.session.get(
                    url, params=params, timeout=effective_timeout, allow_redirects=True
                )
            except Exception as exc:  # requests 예외 계층이 버전마다 달라 광범위하게 잡는다
                if requests is not None and isinstance(
                    exc, getattr(requests.exceptions, "ProxyError", ())
                ):
                    raise NetworkBlockedError(_blocked_message(url, exc)) from exc
                if _looks_blocked(exc):
                    raise NetworkBlockedError(_blocked_message(url, exc)) from exc
                last_err = exc  # 타임아웃·일시적 연결 실패 → 재시도
            else:
                if r.status_code in RETRYABLE_STATUS:
                    last_err = ApiResponseError(f"HTTP {r.status_code} (일시적 오류)")
                elif not r.ok:
                    raise ApiResponseError(
                        f"HTTP {r.status_code} — {url}\n"
                        "  인증키(OC)가 유효하지 않거나 해당 API 사용 권한이 없을 수 있습니다."
                    )
                else:
                    resp = Response(r.content, dict(r.headers), False, url, key)
                    self.cache.put(key, resp.content, resp.headers)
                    return resp

            if attempt < self.retries:
                delay = 2**attempt + random.uniform(0, 0.5)
                self._log(
                    f"  [재시도 {attempt + 1}/{self.retries}] {type(last_err).__name__}"
                    f" — {delay:.1f}s 후 재시도"
                )
                time.sleep(delay)

        raise LawApiError(
            f"{self.retries + 1}회 시도 후에도 실패했습니다: {url}\n  마지막 오류: {last_err}"
        )

    def get_json(self, url: str, params: dict) -> dict:
        """JSON 응답을 파싱한다.

        법제처는 인증 실패도 HTTP 200 + JSON 오류 봉투로 돌려주므로, 파싱에
        성공해도 내용을 한 번 더 검사한다. 오류면 캐시에서 지우고 예외를 던진다.
        """
        resp = self.request(url, params, timeout=self.json_timeout)
        text = resp.text().lstrip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            if resp.cache_key:
                self.cache.evict(resp.cache_key)
            head = text[:200].replace("\n", " ")
            if text.startswith(("<!DOCTYPE", "<html", "<HTML")):
                raise ApiResponseError(
                    "JSON 대신 HTML 페이지가 반환되었습니다.\n"
                    f"  요청: {resp.url}\n"
                    "  흔한 원인:\n"
                    "    - OC 인증키가 승인되지 않았거나 오타\n"
                    "    - 신청한 API 목록에 해당 target 이 없음\n"
                    "    - 등록한 도메인/IP 밖에서 호출\n"
                    f"  응답 앞부분: {head}"
                ) from None
            raise ApiResponseError(
                f"JSON 파싱 실패 — {resp.url}\n  응답 앞부분: {head}"
            ) from None

        try:
            check_api_error(data, resp.url)
        except ApiResponseError:
            if resp.cache_key:  # 오류 응답이 24시간 캐시에 눌러앉지 않도록
                self.cache.evict(resp.cache_key)
            raise
        return data

    # ---------- 고수준 ----------

    def search(
        self,
        query: str,
        target: str = "admrul",
        display: int = 20,
        page: int = 1,
        search: int | None = None,
    ) -> dict:
        """목록 조회.

        `search` 는 검색범위다. target 마다 의미가 다르다(공식 가이드):
          admrul : 1=행정규칙명(기본), 2=본문검색
          admbyl : 1=별표서식명(기본), 2=해당법령검색, 3=별표본문검색
        """
        params = {
            "OC": self.oc,
            "target": target,
            "type": "JSON",
            "query": query,
            "display": display,
            "page": page,
        }
        if search is not None:
            params["search"] = search
        return self.get_json(SEARCH_URL, params)

    def service(self, seq: str, target: str = "admrul") -> dict:
        return self.get_json(
            SERVICE_URL,
            {"OC": self.oc, "target": target, "type": "JSON", "ID": str(seq)},
        )

    def with_oc(self, url: str) -> str:
        """law.go.kr /DRF/ 다운로드 링크에 OC 가 빠져 있으면 붙인다."""
        p = urlparse(url)
        if "law.go.kr" in p.netloc and "/DRF/" in p.path:
            if "OC" not in parse_qs(p.query):
                sep = "&" if p.query else "?"
                return f"{url}{sep}OC={self.oc}"
        return url

    def download(self, url: str) -> Response:
        return self.request(self.with_oc(url), {})
