# OMR Lens · 모의고사 OMR 스캔·채점

모의고사·학력평가 **OMR 답안지 스캔(PDF)** 을 올리면 자동으로 판독·채점해
**대교협 통합양식 · UNIV 학년별 양식 · 과목별 정오표**를 한 엑셀로 떨궈 주는
고등학교 교사용 도구입니다. EBSi에서 정답과 예상 등급컷을 자동으로 가져와
**예상 등급·표준점수·백분위**까지 채워 줍니다.

> ⚠️ 표준점수·백분위·등급은 EBSi **예상 등급컷 기반 추정치**이며 공식 성적표를
> 대체하지 않습니다. 이 저장소에는 학생 데이터·정답키가 포함되어 있지 않습니다.

## 무엇을 하나

- **판독**: 스캔 PDF → 상단 타이밍마크 정렬 → 빨강 인쇄칸 검출 → 마킹 판독
  (객관식 5지선다, 수학 단답형 백·십·일, 수험번호, 성명). 중복·미마킹 구분.
- **채점**: EBSi 정답·배점으로 원점수 산출, 과목별 정오표 생성.
- **통합**: 여러 과목을 학생 단위로 합쳐 대교협/UNIV 제출용 양식으로 출력.
- **예상 성적**: EBSi 등급컷으로 예상 등급·표준점수·백분위 산출.
- **웹 UI**: 학년 선택 → 답안지 드래그&드롭 → 진행 표시 → 결과 다운로드.
  교내망 공유(접속 코드) 지원.

지원 범위: 고3(국·수·영·한국사·탐구) · 고1·2(국·수·영·한국사·통합사회·통합과학).

## 빠른 시작

```bash
pip install -r requirements.txt
python web_app.py            # http://127.0.0.1:5050
```

브라우저에서 학년을 고르고, "EBSi에서 정답 가져오기"로 시험을 등록한 뒤,
과목별 스캔 PDF를 올리면 채점 결과 엑셀을 내려받습니다.
자세한 사용법은 [README_PROGRAM.md](README_PROGRAM.md), 고1·2 확장은
[README_G12.md](README_G12.md), 서버 상시 배포는 [deploy/DEPLOY.md](deploy/DEPLOY.md) 참고.

### 실행 모드
- **PC/교내망**: `python web_app.py` — 본인 PC는 무인증, 동료는 접속 코드
- **서버(리버스 프록시 뒤)**: `OMR_BEHIND_PROXY=1` — 전원 접속 코드 + 무차별
  대입 잠금 + 보안 쿠키. gunicorn + systemd + nginx 는 `deploy/` 참고.

## 구조

| 파일 | 역할 |
|---|---|
| `omr_core.py` | 판독 엔진 (색마스크·격자검출·앵커정렬·darkness) |
| `run_objective.py` | 범용 객관식(+수학 단답) 채점 파이프라인 |
| `run.py` / `run_math.py` / `run_*.py` | 과목별 채점 실행기 |
| `calibrate_objective.py` | 새 답안지 좌표 자동 보정 |
| `consolidate.py` | 과목별 결과 → 통합 성적 워크북 |
| `report_export.py` | 대교협·UNIV·정오표 엑셀 생성 |
| `exam_registry.py` | EBSi 시험 자동 등록 (정답키·등급컷) |
| `grade_cuts.py` | 등급컷 → 예상 등급·표점·백분위 |
| `web_app.py` | Flask 웹앱 (교내망 공유) |
| `synth_omr.py` | 가상 OMR 카드 생성 (테스트용) |
| `tests_regression.py` | 회귀 테스트 스위트 |

정답키(`keys/`)·스캔(`input/`,`work/`)·결과(`output/`)는 학생 개인정보와
EBSi 데이터라 저장소에서 제외됩니다. 정답키는 웹의 "EBSi에서 정답 가져오기"
또는 `python exam_registry.py register <연> <월> <학년>` 으로 생성합니다.

## 테스트

```bash
python tests_regression.py          # 오프라인 (일부는 로컬 데이터 필요 시 자동 건너뜀)
python tests_regression.py --web    # + 웹 e2e (web_app.py 실행 중일 때)
```

## 라이선스

MIT License — [LICENSE](LICENSE) 참고. EBSi에서 가져오는 정답·등급컷 등
제3자 데이터의 권리는 각 권리자에게 있으며, 본 소프트웨어에 포함되지 않습니다.
