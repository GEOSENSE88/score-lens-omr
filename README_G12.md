# 고1·2 전국연합 학력평가 OMR 채점

고3 평가원 모평용으로 검증된 판독 엔진의 **고1·2 전국연합 학력평가** 6과목 확장
(국어·수학·영어·한국사·통합사회·통합과학).

## 현재 상태 (2026-07-03): 가상 카드로 전체 파이프라인 검증 완료 ✅

`synth_omr.py` 가 만든 가상 OMR 카드(타이밍마크+빨강 버블+학년/반/번호+수학
백·십·일 단답)로 **보정→판독→채점→통합성적표** 전 구간을 검증했다:
- 좌표 자동 보정: 6과목 템플릿 생성, 설계값 ±1px 재현
- 채점: 6과목×6명 = **36/36 점수 일치** (수학 단답형·앞자리0 생략 마킹·소수 배점 1.5점 포함)
- 웹 업로드→통합성적표_고1.xlsx: UNIV 1학년 시트 **36/36 일치**, 대교협·정오표 정상
- 이 과정에서 실버그 2건 수정: 한글 파일명 업로드 시 과목끼리 덮어쓰기(save_upload),
  cv2 한글 경로 imread 실패(omr_core.load_page)

⚠️ **현재 templates/*_g12.json 과 id_reader 'g12' 좌표는 가상 카드 설계값이다.**
실물 스캔이 오면 아래 scan-day 절차로 좌표만 재보정하면 된다(도구·파이프라인은 검증 끝).

## 지금 준비된 것 (검증 완료)

| 구성 | 파일 | 상태 |
|---|---|---|
| 좌표 자동 보정기 | `calibrate_objective.py` | ✅ 고3 영어 스캔으로 검증 (기존 좌표 ±0.3~3px 재현) |
| 범용 객관식 채점기 | `run_objective.py` | ✅ 고3 한국사 스캔 31명 점수 100% 재현 |
| 6개 카드 구조 정의 | `specs/*_g12.json` | 문항수·블록 확정 / 검색창은 첫 스캔에서 미세조정 |
| 수학 선택과목 없음 대응 | `math_core.py` | ✅ 빈 subject_marks 안전 처리 |
| 수험번호 학년/반/번호 | `id_reader.py` (`g12` 레이아웃) | 구조 준비 / 좌표는 첫 스캔에서 보정 |

## 카드별 구조 (고3와 다른 점)

| 과목 | 문항 | 비고 |
|---|---|---|
| 국어 | 45 | 전부 공통. 화작/언매 선택 없음 → 정답키 단일 45문항 |
| 수학 | 30 | 객관식 1–21 + 단답형 22–30. **선택과목(확통/미적/기하) 없음** |
| 영어 | 45 | 1–20 / 21–40 / 41–45 (고3와 동일 구조) |
| 한국사 | 20 | 단일 블록 (고3와 동일 구조) |
| 통합사회 | 25 | 단일 과목 1–25 (고3 탐구=2선택×20 과 다름) |
| 통합과학 | 25 | 단일 과목 1–25 |

수험번호는 6과목 공통 **학년(1) + 반(2) + 번호(2)** (고3의 학교번호 5자리와 다름).

## Scan-day 절차 (스캔 확보 후)

### 1) 정답키 준비 — 자동 (exam_registry)
웹 화면의 **"EBSi에서 정답 가져오기"** 버튼(연/월/학년 선택) 또는:
```powershell
python exam_registry.py register 2026 6 1    # 고1 6월 — 6과목 전부 자동
```
고1·2는 국·수·영·한국사·통합사회·통합과학 6과목이 XIP API로 전부 자동 생성된다
(수학 단답형 정답 포함, 통합과목 소수 배점 1.5/2/2.5 정규화). 2026-06 고1·고2로 검증 완료.

### 2) 좌표 보정 — 스캔에서 자동
```powershell
# 객관식 5과목 (국어·영어·한국사·통합사회·통합과학)
python calibrate_objective.py --spec specs/english_g12.json  --scan "D:\scan\영어.pdf"     --out templates/english_g12.json
python calibrate_objective.py --spec specs/history_g12.json  --scan "D:\scan\한국사.pdf"   --out templates/history_g12.json
python calibrate_objective.py --spec specs/social_g12.json   --scan "D:\scan\통합사회.pdf" --out templates/social_g12.json
python calibrate_objective.py --spec specs/science_g12.json  --scan "D:\scan\통합과학.pdf" --out templates/science_g12.json
python calibrate_objective.py --spec specs/korean_g12.json   --scan "D:\scan\국어.pdf"     --out templates/korean_g12.json
```
> 보정이 "유효 페이지 없음"이면 해당 `specs/*.json`의 `x_lo/x_hi`(검색창)를 넓혀 재실행.
> 산출 좌표는 `work/`의 판독 오버레이로 눈 검증.

### 3) 채점 — 판독표 생성
```powershell
python run_objective.py "D:\scan\영어.pdf"     --subject 영어     --template templates/english_g12.json --id-layout g12 --irecord <examid> --names output/국어_판독표.csv
python run_objective.py "D:\scan\한국사.pdf"   --subject 한국사   --template templates/history_g12.json --id-layout g12 --irecord <examid> --names output/국어_판독표.csv
python run_objective.py "D:\scan\통합사회.pdf" --subject 통합사회 --template templates/social_g12.json  --id-layout g12 --irecord <examid> --names output/국어_판독표.csv
python run_objective.py "D:\scan\통합과학.pdf" --subject 통합과학 --template templates/science_g12.json --id-layout g12 --irecord <examid> --names output/국어_판독표.csv
python run_objective.py "D:\scan\국어.pdf"     --subject 국어     --template templates/korean_g12.json  --id-layout g12 --irecord <examid>
```

수학은 객관식 좌표는 위 보정기로 뽑고, **단답형 22–30**의 `sib/y0/pitch`만
`22번~30번` 박스에서 실측해 `templates/math_g12.json`의 `short`에 채운 뒤
`run_math.py --template templates/math_g12.json --id-layout g12`로 채점한다.
(선택과목이 없으므로 subject_marks는 비워 둔다.)

## 통합 성적 워크북 export (대교협 / UNIV / 정오표)

채점 후 과목별 CSV 를 하나의 학교업무용 워크북으로 합친다. 시트 구성:
**대교협**(대교협 통합양식 [저장] A:S 배치) + **UNIV**(학년별 레이아웃) + **정오표_<과목>**.
OMR 은 원점수만 채우고 표준점수·백분위·등급은 공란(공식 성적표 후 입력).

- `report_export.py` — 통합 레코드 → 워크북 빌더. 대교협/UNIV(1·2·3학년)/정오표 시트.
- `consolidate.py` — 과목별 채점 CSV 를 학생 단위로 모아 빌더 호출(성명 우선 조인).
  국어·수학 **공통/선택 원점수 분리**는 정답키 배점으로 자동 계산.

```powershell
# 고3 (검증 완료 — 6월 31명 실데이터로 대교협·UNIV3·정오표 생성 확인)
python consolidate.py --grade 3 --keys-dir keys `
  --korean output/..._점수표.csv --math output/..._수학_점수표.csv `
  --english output/..._영어_판독표.csv --history output/..._한국사_판독표.csv `
  --explore output/..._탐구_판독표.csv --out output/통합성적표.xlsx

# 고1·2 (통합사회/통합과학은 --social/--science 로)
python consolidate.py --grade 1 --keys-dir keys `
  --korean ... --math ... --english ... --history ... `
  --social output/..._통합사회_판독표.csv --science output/..._통합과학_판독표.csv `
  --out output/통합성적표_고1.xlsx
```
> 매핑: 고1·2 통합사회→대교협 탐구1/UNIV 사회탐구, 통합과학→탐구2/과학탐구.
> 학생 매칭은 성명 우선 → (반,번호). 반 오독이 있으면 성명 조인이 안전.
> 검증 샘플: `output/_검증_통합성적표_고3.xlsx` (열어서 확인 가능).

## 실물 스캔이 오면 남는 작업 (좌표 재보정만)
1. `calibrate_objective.py --spec specs/*_g12.json` 을 실물 스캔으로 재실행 →
   templates/*_g12.json 교체. spec 검색창이 안 맞으면 x_lo/x_hi 만 조정.
2. `id_reader.py` `g12` 레이아웃 cols/y0/pitch 를 실물 수험번호 격자로 실측 교체.
3. `templates/math_g12.json` 의 short(22~30 sib/y0/pitch) 실측 교체.
4. 몇 장 채점해서 실물 답안·점수와 눈대조.
(정답키·판독·채점·웹·통합성적표는 이미 검증 완료 — 위 좌표 3종만 실물화하면 끝.)
