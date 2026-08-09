# law-go-kr — 배포 패키지

법제처 국가법령정보 OPEN API(DRF)로 법령·행정규칙을 검색하고, 별표·서식을
번호로 지정해 내려받아 파싱하는 Claude Code 스킬의 배포용 압축 파일 모음.

## 현재 버전

| 버전 | 파일 | 체크섬 |
|---|---|---|
| **1.0.3** | `law-go-kr-1.0.3.zip` | `law-go-kr-1.0.3.zip.sha256` |

버전 번호는 `law-go-kr/VERSION` 파일에도 들어 있다. 이전 버전 zip은 지우지
않고 그대로 둔다 — 배포된 버전의 내용은 이후에 덮어쓰지 않는다(불변).
버그를 고치면 새 버전 번호로 다시 패키징한다.

## 설치 (다른 PC)

### 1) 다운로드 후 무결성 확인 (선택)

```bash
shasum -a 256 -c law-go-kr-1.0.3.zip.sha256
# 또는 Linux: sha256sum -c law-go-kr-1.0.3.zip.sha256
```

### 2) 압축 해제 → Claude Code 스킬 폴더에 배치

```bash
mkdir -p ~/.claude/skills
unzip law-go-kr-1.0.3.zip -d ~/.claude/skills/
# 결과: ~/.claude/skills/law-go-kr/
```

### 3) 의존성 설치

`law-go-kr` 자체는 `requests` 만 있으면 검색·다운로드가 되지만, 받은
HWP/HWPX/PDF 파일을 텍스트로 파싱하려면 **`korean-doc-parser` 스킬**이
별도로 필요하다 (같은 리포지토리의 `.claude/skills/korean-doc-parser/`).

```bash
pip3 install --user requests
# korean-doc-parser 도 설치했다면:
pip3 install --user -r ~/.claude/skills/korean-doc-parser/requirements.txt
```

macOS Homebrew 파이썬처럼 "externally-managed-environment" 오류가 나면
`--break-system-packages` 를 추가한다.

### 4) 법제처 OPEN API 인증키 설정

```bash
export LAW_GO_KR_OC=your_oc_id   # open.law.go.kr 에서 발급
```

**⚠️ 키 발급만으로는 안 된다 — 호출하는 장비의 IP를 반드시 등록해야 한다.**

1. [open.law.go.kr](https://open.law.go.kr) 로그인 → **OPEN API 신청 현황**에서
   본인의 신청 내역을 연다.
2. **서버 IP / 도메인** 항목에 이 스크립트를 실행할 장비의 **아웃바운드 IP**를
   등록한다. (내 PC의 공인 IP는 `curl ifconfig.me` 등으로 확인)
3. 클라우드/원격 서버는 아웃바운드 IP가 유동적이라 등록이 잘 맞지 않는 경우가
   많다 — 가능하면 고정 IP 환경(회사 PC, 고정 IP 서버)에서 실행할 것.
4. IP를 등록하지 않으면 **키 자체는 맞아도** 다음과 같은 오류가 난다. 이
   메시지는 "키가 틀림"과 "IP 미등록"을 구분해주지 않으므로 둘 다 확인해야
   한다.
   ```
   법제처 API 오류: 사용자 정보 검증에 실패하였습니다.
   ```
5. 팀원 여러 명이 각자 다른 장비에서 쓴다면, **각자 자기 키를 발급받고
   각자의 장비 IP를 등록**해야 한다 — 한 사람의 키/IP 등록을 공유해서 쓸 수
   없다.

> 테스트 목적이라면 공식 가이드가 공개한 샌드박스 키 `OC=test` 를 IP 등록
> 없이 바로 쓸 수 있다. 다만 이 키는 검증·데모용이므로 실사용에는 본인 키를
> 발급받는 것을 권장한다.

### 5) 설치 확인

```bash
python3 ~/.claude/skills/law-go-kr/scripts/law_fetch.py doctor
```

모든 항목이 초록불(✅)이면 준비 완료. 자세한 사용법은 압축 안의
`law-go-kr/SKILL.md` 참고.

## 패키지 구조

```
law-go-kr-1.0.3.zip
└── law-go-kr/
    ├── SKILL.md          사용법·API 스펙·한계 전체 문서
    ├── VERSION           배포 버전 번호
    ├── scripts/
    │   ├── law_fetch.py  CLI 진입점
    │   ├── lawapi.py     HTTP 계층(재시도·캐시·오류 구분)
    │   ├── parsers.py    파싱 계층(순수 함수)
    │   └── run_local.sh  원클릭 실행 스크립트
    └── tests/            네트워크 없이 도는 테스트 147개
```

## 버전 이력

| 버전 | 날짜 | 비고 |
|---|---|---|
| 1.0.3 | 2026-08-08 | **[중요] admrul/ordin 조문 파싱 버그 5건 수정 (편향 없는 독립 리뷰로 발견, 3개 부처+2개 target 실데이터로 검증).** (1) 조문내용이 문자열이 아니라 리스트로 오는 경우 통째로 무시되던 것 — 3개 부처 admrul 문서에서 재현. (2) 「식품등의 표시기준」처럼 "제N조"가 아니라 Ⅰ/1/가 개요식 본문을 "본문이 비어있다"고 잘못 보고하던 것 — 이제 원문 그대로 반환. (3) 자치법규(ordin)가 완전히 다른 응답 구조라 항/호가 통째로 뭉개지고 부칙 텍스트가 가짜 조문으로 섞이던 것. (4) "1의2." 같은 가지번호 호가 앞 호에 잘못 흡수되던 것. (5) 21번째 이후 항(㉑~)이 인식되지 않던 것. 추가로 검색 결과가 조용히 잘리던 것에 총건수 안내와 `--page` 지원을 더함 |
| 1.0.2 | 2026-08-08 | **[중요] 실제 법령 데이터 손실 버그 수정.** (1) `export` 가 같은 제목의 조문을 하나로 뭉개던 문제 — 「식품위생법」 벌칙 조항(제93~98조) 6개 중 5개가 조용히 사라지는 것으로 실증됨. term+category+조문번호로 중복 판정하도록 수정. (2) `ordin`(자치법규) 검색이 제목 대신 분류 라벨("제5장 맑은도시")을 표시하던 문제 수정. (3) API 응답 지연 시 최악 대기시간을 ~5분에서 ~100초로 단축(JSON 조회와 파일 다운로드의 타임아웃 분리). (4) `export --out`에 디렉터리를 주면 크래시하던 것을 안내 메시지로 수정 |
| 1.0.1 | 2026-08-08 | 실제 API 전 기능 테스트로 조문 구조화 버그 3건 수정: (1) "제1장 총칙" 같은 장 표제가 가짜 조문으로 잡히던 것, (2) 항/호 내용에 번호가 중복 출력되던 것("① ①", "1. 1."), (3) 항이 단일 dict로 올 때(정의 조항 등) 호 항목이 통째로 유실되던 것. 「식품위생법」 제2조 실제 응답으로 검증 |
| 1.0.0 | 2026-08-08 | 첫 배포. 3계층 재구축, 별표 번호 지정(`--byl`), 조문 구조화, `doctor` 진단, 실제 API 전 구간 검증 완료 |

## 새 버전 만들기 (관리자용)

```bash
# 1) .claude/skills/law-go-kr/VERSION 갱신
# 2) 압축
STAGE=$(mktemp -d) && mkdir -p "$STAGE/law-go-kr"
cp -R .claude/skills/law-go-kr/{SKILL.md,VERSION,scripts,tests} "$STAGE/law-go-kr/"
find "$STAGE" -name "__pycache__" -exec rm -rf {} +
V=$(cat .claude/skills/law-go-kr/VERSION)
(cd "$STAGE" && zip -rq -X "$OLDPWD/dist/law-go-kr/law-go-kr-$V.zip" law-go-kr)
sha256sum "dist/law-go-kr/law-go-kr-$V.zip" > "dist/law-go-kr/law-go-kr-$V.zip.sha256"
rm -rf "$STAGE"
```
