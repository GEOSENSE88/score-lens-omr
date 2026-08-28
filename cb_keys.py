# -*- coding: utf-8 -*-
"""충북교육청 모의평가 정답키 등록 — EBSi 미제공 시험용 수동 등록 도구.

충북 배포 정답으로 keys/<exam_id>_<과목>_<선택>.json 을 만든다.

    # 국어 (공통 1~34 + 선택 35~45, 화작·언매 각각)
    python cb_keys.py --exam 202608083 --subject 국어 --elective 화법과작문 \\
        --answers "3 2 5 4 1 ... (45개, 공백 구분)" [--points "2 3 2 ..."]

    배점 생략 시 국어/영어 표준(전 2점 + 3점 문항 자동 배분 불가 → 균등 불가라
    반드시 지정 권장). 총점이 100(한국사 50)이 되는지 검증한다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_BY_SUBJECT = {"국어": 100, "영어": 100, "수학": 100, "한국사": 50}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exam", required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--elective", default=None)
    ap.add_argument("--answers", required=True, help="정답 나열(공백 구분)")
    ap.add_argument("--points", default=None, help="배점 나열(공백 구분, 정답과 동일 개수)")
    ap.add_argument("--keys-dir", type=Path, default=HERE / "keys")
    a = ap.parse_args()

    ans = a.answers.split()
    pts = a.points.split() if a.points else ["2"] * len(ans)
    if len(ans) != len(pts):
        print(f"[오류] 정답 {len(ans)}개 vs 배점 {len(pts)}개")
        return 1
    answers = {str(i + 1): int(v) for i, v in enumerate(ans)}
    points = {str(i + 1): float(p) if "." in p else int(p) for i, p in enumerate(pts)}
    total = sum(points.values())
    want = MAX_BY_SUBJECT.get(a.subject)
    if want and total != want:
        print(f"[경고] 배점 합계 {total} ≠ 만점 {want} — 배점을 확인하세요.")

    el = a.elective or a.subject
    name = f"{a.exam}_{a.subject}_{el}.json" if a.elective \
        else f"{a.exam}_{a.subject}_xip_api.json"
    payload = dict(exam_id=a.exam, subject=a.subject, elective=el, complete=True,
                   item_count=len(answers), max_score=total,
                   answers=answers, points=points,
                   source="충북교육청 배포 정답 수동 등록 (cb_keys.py)")
    out = a.keys_dir / name
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    ph = a.keys_dir / f"{a.exam}_시험자리.json"
    if ph.exists():
        ph.unlink()
        print("(시험자리 placeholder 제거)")
    print(f"{len(answers)}문항, 만점 {total} → {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
