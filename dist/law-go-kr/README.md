# law-go-kr — 배포 패키지

법제처 국가법령정보 OPEN API(DRF)로 법령·행정규칙을 검색하고, 별표·서식을
번호로 지정해 내려받아 파싱하는 Claude Code 스킬의 배포용 압축 파일 모음.

## 현재 버전

| 버전 | 파일 | 체크섬 |
|---|---|---|
| **1.0.1** | `law-go-kr-1.0.1.zip` | `law-go-kr-1.0.1.zip.sha256` |

버전 번호는 `law-go-kr/VERSION` 파일에도 들어 있다. 이전 버전 zip은 지우지
않고 그대로 둔다 — 배포된 버전의 내용은 이후에 덮어쓰지 않는다(불변).
버그를 고치면 새 버전 번호로 다시 패키징한다.

## 설치 (다른 PC)

### 1) 다운로드 후 무결성 확인 (선택)

```bash
shasum -a 256 -c law-go-kr-1.0.1.zip.sha256
# 또는 Linux: sha256sum -c law-go-kr-1.0.1.zip.sha256
```

### 2) 압축 해제 → Claude Code 스킬 폴더에 배치

```bash
mkdir -p ~/.claude/skills
unzip law-go-kr-1.0.1.zip -d ~/.claude/skills/
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

### 5) 설치 확인

```bash
python3 ~/.claude/skills/law-go-kr/scripts/law_fetch.py doctor
```

모든 항목이 초록불(✅)이면 준비 완료. 자세한 사용법은 압축 안의
`law-go-kr/SKILL.md` 참고.

## 패키지 구조

```
law-go-kr-1.0.1.zip
└── law-go-kr/
    ├── SKILL.md          사용법·API 스펙·한계 전체 문서
    ├── VERSION           배포 버전 번호
    ├── scripts/
    │   ├── law_fetch.py  CLI 진입점
    │   ├── lawapi.py     HTTP 계층(재시도·캐시·오류 구분)
    │   ├── parsers.py    파싱 계층(순수 함수)
    │   └── run_local.sh  원클릭 실행 스크립트
    └── tests/            네트워크 없이 도는 테스트 121개
```

## 버전 이력

| 버전 | 날짜 | 비고 |
|---|---|---|
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
