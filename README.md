# Wizardry 8 한국어 패치

Wizardry 8과 Fan Patch 1.28을 위한 비공식 한국어 패치입니다.

## 최신 릴리스

- 원본 1.24: `Wizardry8_KoreanPatch_1.24_JournalFix_v0.2.8.zip`
- Fan Patch 1.28 build 6735: `Wizardry8_KoreanPatch_1.28_JournalFix_v0.2.8.zip`
- 한국어 패치 v0.2.8 — 저널 표기/글꼴 수정

두 ZIP은 서로 다른 게임 버전용입니다. 파일을 섞어 설치하지 마세요.

## 설치

1. 정품 Wizardry 8을 설치합니다.
2. 사용 중인 게임 버전에 맞는 [최신 릴리스](../../releases)의 ZIP 하나를 받습니다.
3. 압축 안의 내용을 Wizardry 8 게임 폴더에 덮어씁니다.
4. Fan Patch 1.28 사용자는 런처 언어를 **ENG**로 두고 `Wiz8_v128.exe`를 실행합니다.

대화의 영문 토픽은 클릭하거나 더블클릭해 입력할 수 있습니다. 기존 세이브에 이미
기록된 `소문`, `잘 지내십니까`는 자동 변환되지 않으므로 삭제 후 영문으로 다시 등록하세요.

`KOR` 외부 로캘은 사용하지 않습니다. 수정된 `Wiz8_v128.exe`가 NPC 스크립트의
한국어 문자열 변환 로캘과 버퍼를 직접 처리합니다. Fan Patch 추가 옵션 문자열은
`Wiz8.dll` 내부에 직접 적용했습니다.

권장 `wiz8.ini` 설정:

```ini
Language=2
Fan_Patch_1_28_Localization=
DontShowDialogLauncher=0
```

## v0.2.8 주요 변경

- 저널의 종족/고유명사 `Mook` 표기를 한국어 음역 `묵` 대신 `Mook`로 통일
- Mook 관련 저널 16개 슬롯 수정
- `Data/JOURNAL/journal_font.sti`의 한글 글리프 1,196개를 갈무리9 기반으로 교체하고 기준선 재조정
- 비한글 글리프 2,103개는 기존 데이터 유지
- `FACT.DBS` 저널 필드 외 변경 0건, STI 3,299개 프레임 재해독, ZIP CRC/SHA-256 검증 완료

릴리스 ZIP SHA-256:

- 1.24: `34D876B606AAD2F6610B20701DE45A35600A306F07F4F995D96BE3C1B8ED7DC4`
- 1.28: `7D2A892DB90CE75E51C6B122A78DFBFDF4A2D55A70939E2DD9DD07C379DDF254`

## 포함 범위

- 본편 UI와 지역 메시지
- 플레이어 음성 자막
- NPC 대화 스크립트
- 퀘스트 저널 기록 333문장
- 아이템 이름 819개
- 아이템 및 주문 설명
- Fan Patch 1.28 build 6735 추가 옵션
- 갈무리9 기반 한글 비트맵 글꼴과 한국어 문자 매핑
- 8×7 초소형 글꼴은 프리텐다드 사용

## 글꼴 고지

- Noto Sans KR: Copyright 2014-2021 Adobe, SIL Open Font License 1.1
- Pretendard: SIL Open Font License 1.1
- 릴리스에는 게임용으로 래스터화한 비트맵 글리프만 포함됩니다.

## 주의

- 설치 전 게임 폴더를 백업하세요.
- 한국어 패치가 설치된 폴더에서 Fan Patch 자동 업데이트를 실행하지 마세요.
- 게임 본편과 Fan Patch는 이 저장소 및 릴리스에 포함되지 않습니다.
- 이 프로젝트는 Sir-Tech, GOG 또는 Fan Patch 제작진과 관계없는 비공식 작업입니다.
