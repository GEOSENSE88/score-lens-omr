# Score Lens OMR 프로그램

학교 운영용 진입점.

- **원클릭 (가장 쉬움)**: 바탕화면 **"산남고 OMR 채점실"** 바로가기 더블클릭
  → 서버가 없으면 자동으로 켜고(최소화된 "OMR server" 창) 브라우저를 엽니다.
  최소화 창을 닫으면 서버가 종료됩니다. (원본: `start_omr.bat`)
- **웹 수동 실행**: `python web_app.py` → `http://127.0.0.1:5050`
- **CLI**: `python omr_program.py` — 스크립트/자동화용

## 동료와 공유 (교내망)

서버를 켜면 화면 우측 상단에 **동료 공유 주소와 접속 코드**가 표시됩니다
(본인 PC 화면에서만 보임). 같은 학교망의 동료는 그 주소로 접속해 코드를
입력하면 사용할 수 있습니다.

- 본인 PC(localhost)는 코드 없이 바로 사용.
- 접속 코드는 `access_code.txt` 를 열어 원하는 값으로 바꿀 수 있음(서버 재시작).
- 동료가 접속이 안 되면: Windows 방화벽이 차단한 것 —
  처음 서버를 켤 때 뜨는 방화벽 허용 창에서 **허용**을 누르거나,
  [Windows 보안 → 방화벽 → 앱 허용] 에서 Python 을 개인 네트워크에 허용.
- 서버가 켜져 있는 동안만 접속 가능. 인터넷(외부망) 공개는 학생 성적
  개인정보 특성상 권장하지 않음.

## 브라우저에서 실행 (산남고 OMR 채점실)

```powershell
python web_app.py
```

1. **시험 정보** — 학년(고1·고2·고3) 선택, 시험 ID(EBSi irecord), 학년도(대교협 양식),
   성명 명단 CSV(선택 — 없으면 국어 판독 성명을 나머지 과목에 자동 연결).
2. **답안지 업로드** — 과목 카드 클릭 또는 PDF 드래그앤드롭.
   고1·2 과목은 좌표 템플릿(`templates/*_g12.json`)이 생기면 자동 활성화되고,
   그 전에는 "보정 대기" 배지가 표시됩니다.
3. **채점 진행/결과** — 진행 타임라인을 보여주고, 완료되면
   **통합성적표.xlsx(대교협 + UNIV 학년별 + 과목별 정오표)** 를 최상단에,
   과목별 점수표/정오표를 그 아래에 내려받기로 제공합니다.

검증: 2026-07-03, 6월 고3 실스캔(국어·영어·한국사 각 3페이지)으로
업로드→채점→통합성적표 생성→다운로드 전 과정 end-to-end 확인.

## CLI 한 과목 실행

```powershell
python omr_program.py score --subject 영어 --pdf D:\scan\20260604165440.pdf --irecord 202606043 --names output\20260604165616_점수표.csv
```

## CLI 여러 과목 일괄 실행

```powershell
python omr_program.py batch --manifest examples\mock_exam_manifest.json
```

manifest 예시는 `examples/mock_exam_manifest.json`. 시험 ID, 이름 매칭용 국어 결과
CSV, 과목별 PDF 경로만 바꾸면 됩니다. CLI 채점 후 통합성적표는
`python consolidate.py --grade 3 ...` 로 별도 생성합니다(README_G12.md 참고).

## 현재 범위

- 고3: 국어·수학·영어·한국사·탐구 판독/채점 + 통합성적표 자동 생성 (검증 완료)
- 고1·2: 6과목(국어·수학·영어·한국사·통합사회·통합과학) 배선 완료,
  좌표 템플릿 보정 대기 (README_G12.md 의 scan-day 절차)
- 정답/배점은 `keys/` 폴더의 JSON 사용 (ebsi_keys.py / math_keys.py 등으로 생성)
- 출력: 과목별 XLSX/CSV + run_log.txt + 통합성적표.xlsx

## 회귀 테스트 (코드 수정 후 반드시)

```powershell
python tests_regression.py          # 오프라인 32종 (실스캔·가상카드·에지케이스)
python tests_regression.py --web    # + 웹 e2e (web_app.py 실행 중일 때)
```

검증 범위: 정답키 exam_id 격리 / 고3 한국사 31명 실스캔 재현 / 고3 통합성적표
기준값 / 고1 가상카드 36건 / 중복·미마킹·결시 에지케이스 / 좌표 보정기 재현성 /
학년별 워크북 레이아웃 / 웹 업로드→통합성적표.

## 다음 개발 단위

- 수동 검수 화면: 미마킹·중복마킹·학번 이상치만 빠르게 확인/수정
- 고1·2 첫 실물 스캔 보정(README_G12.md) 후 웹에서 활성화 확인
