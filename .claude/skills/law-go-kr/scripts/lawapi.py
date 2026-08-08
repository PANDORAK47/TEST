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


class Response:
    """캐시 히트와 실제 응답을 같은 모양으로 다루기 위한 최소 래퍼."""

    __slots__ = ("content", "headers", "from_cache", "url")

    def __init__(self, content: bytes, headers: dict, from_cache: bool, url: str):
        self.content = content
        self.headers = headers
        self.from_cache = from_cache
        self.url = url

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
        retries: int = DEFAULT_RETRIES,
        verbose: bool = True,
    ):
        if requests is None:
            raise LawApiError("requests 패키지가 필요합니다: pip install requests")
        self.oc = resolve_oc(oc)
        self.cache = Cache(cache_dir, ttl, use_cache)
        self.timeout = timeout
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

    def request(self, url: str, params: dict) -> Response:
        """GET 요청 + 캐시 + 재시도. 성공하면 Response, 실패하면 예외."""
        key = Cache.key(url, params)
        hit = self.cache.get(key)
        if hit is not None:
            hit.url = url
            self._log(f"  [캐시] {url}")
            return hit

        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                r = self.session.get(
                    url, params=params, timeout=self.timeout, allow_redirects=True
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
                    resp = Response(r.content, dict(r.headers), False, url)
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
        """JSON 응답을 파싱한다. HTML 이 오면 인증 문제로 간주하고 안내한다."""
        resp = self.request(url, params)
        text = resp.text().lstrip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
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

    # ---------- 고수준 ----------

    def search(self, query: str, target: str = "admrul", display: int = 20, page: int = 1) -> dict:
        return self.get_json(
            SEARCH_URL,
            {
                "OC": self.oc,
                "target": target,
                "type": "JSON",
                "query": query,
                "display": display,
                "page": page,
            },
        )

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
