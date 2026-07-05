# -*- coding: utf-8 -*-
"""
④ 채점 모듈 — 판독된 답안 + 정답키(JSON) → 원점수·정오표
score_lens / omr_scorer
"""
from __future__ import annotations
import json
from pathlib import Path


def normalize_elective(name: str | None) -> str:
    """'화법과 작문' / '화법과작문' 등을 공백 제거로 통일."""
    return (name or "").replace(" ", "")


def load_keys(keys_dir: Path, irecord: str | None = None) -> dict:
    """keys/ 의 국어 정답키들을 {정규화된 선택과목: key} 로 로드."""
    out = {}
    for p in Path(keys_dir).glob("*.json"):
        k = json.loads(p.read_text(encoding="utf-8"))
        # 국어 외 과목(수학·탐구 등) 키는 구조가 달라 건너뜀
        if k.get("subject") != "국어":
            continue
        if not isinstance(k.get("answers"), dict):
            continue
        if irecord and str(k.get("exam_id") or k.get("irecord")) != str(irecord):
            continue
        slot = normalize_elective(k.get("elective"))
        exam = str(k.get("exam_id") or k.get("irecord"))
        prev = out.get(slot)
        if prev is not None:
            prev_exam = str(prev.get("exam_id") or prev.get("irecord"))
            if prev_exam != exam:
                raise ValueError(
                    f"[정답키 충돌] 선택과목 '{slot}' 키가 여러 회차에 존재합니다 "
                    f"({prev_exam} / {exam}). --irecord 로 회차를 지정하세요."
                )
        out[slot] = k
    return out


def grade(answers: dict, key: dict) -> dict:
    """answers={문항:선택(0=미마킹,-1=중복)}, key=정답키 JSON → 채점 결과."""
    kans = {int(q): v for q, v in key["answers"].items()}
    kpts = {int(q): v for q, v in key["points"].items()}
    score, correct, wrong, blank, dup, per_q = 0, [], [], [], [], {}
    for q in sorted(kans):
        stu = answers.get(q, 0)
        ok = (stu == kans[q])
        if stu == 0:
            blank.append(q); mark = "·"
        elif stu == -1:
            dup.append(q); mark = "중복"
        elif ok:
            score += kpts[q]; correct.append(q); mark = "O"
        else:
            wrong.append(q); mark = "X"
        per_q[q] = dict(student=stu, answer=kans[q], points=kpts[q], result=mark)
    return dict(
        score=score, max_score=int(key.get("max_score", sum(kpts.values()))),
        correct_count=len(correct), wrong=wrong, blank=blank, dup=dup, per_q=per_q,
    )
