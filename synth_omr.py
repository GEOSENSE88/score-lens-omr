# -*- coding: utf-8 -*-
"""
고1·2 전국연합 학력평가 가상 OMR 카드 생성기 (score_lens / omr_scorer)

실물 스캔이 오기 전에 g12 파이프라인(보정→판독→채점→통합성적표)을 끝까지
검증하기 위한 합성 답안지를 만든다.

⚠️ 여기 좌표는 '가상 카드'의 설계값이다. 실물 카드와 다르므로,
   실물 스캔이 오면 calibrate_objective.py 로 좌표만 다시 보정한다
   (이번 검증으로 도구·파이프라인 자체는 전부 확인됨).

카드 공통 (3300x2550, 300DPI A4 가로):
  - 상단 검정 타이밍 마크: omr_core.REF_TOP_ANCHOR (81~3202, y≈67) 에 맞춤
  - 인쇄 마킹칸: 빨강 타원 외곽선 (red_mask / detect_blobs 검출 대상)
  - 수험번호: 학년(1) + 반(2) + 번호(2) = 5열 x 0~9  (id_reader 'g12' 레이아웃)
  - 학생 마킹: 검정 채움 타원 (darkness 판독 대상)
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import cv2
import numpy as np

W, H = 3300, 2550
BG = (235, 248, 252)          # 연노랑 카드지 (BGR)
RED = (60, 60, 205)           # 인쇄 빨강 (r-b>30, r>120 충족)
INK = (25, 25, 25)            # 학생 마킹(컴퓨터용 사인펜)

# ── 수험번호 (전 카드 공통, g12: 학년1+반2+번호2) ────────────────
ID_COLS = [300, 380, 460, 560, 640]
ID_Y0, ID_PITCH = 1150.0, 99.5

# ── 카드별 객관식 블록 설계 (specs/*_g12.json 검색창 안에 배치) ──
def _xs(x0, n=5, pitch=60):
    return [x0 + pitch * i for i in range(n)]

CARDS: dict[str, dict] = {
    "국어": {
        "objective": [
            dict(q0=1,  nrows=20, xs=_xs(1950), y0=420.0, pitch=100.0),
            dict(q0=21, nrows=14, xs=_xs(2400), y0=420.0, pitch=100.0),
            dict(q0=35, nrows=11, xs=_xs(2890), y0=420.0, pitch=100.0),
        ],
    },
    "수학": {
        "objective": [
            dict(q0=1,  nrows=7, xs=_xs(1140), y0=420.0, pitch=99.0),
            dict(q0=8,  nrows=7, xs=_xs(1560), y0=420.0, pitch=99.0),
            dict(q0=15, nrows=7, xs=_xs(1980), y0=420.0, pitch=99.0),
        ],
        "short": {  # 백·십·일 3열 x 0~9 (sib=십의자리 x, dx=61)
            22: dict(sib=2500, y0=420.0, pitch=99.0),
            23: dict(sib=2760, y0=420.0, pitch=99.0),
            24: dict(sib=3020, y0=420.0, pitch=99.0),
            25: dict(sib=1240, y0=1560.0, pitch=99.0),
            26: dict(sib=1600, y0=1560.0, pitch=99.0),
            27: dict(sib=1960, y0=1560.0, pitch=99.0),
            28: dict(sib=2320, y0=1560.0, pitch=99.0),
            29: dict(sib=2680, y0=1560.0, pitch=99.0),
            30: dict(sib=3040, y0=1560.0, pitch=99.0),
        },
        "short_digit_dx": 61,
    },
    "영어": {
        "objective": [
            dict(q0=1,  nrows=20, xs=_xs(1940), y0=420.0, pitch=100.0),
            dict(q0=21, nrows=20, xs=_xs(2410), y0=420.0, pitch=100.0),
            dict(q0=41, nrows=5,  xs=_xs(2870), y0=420.0, pitch=100.0),
        ],
    },
    "한국사": {
        "objective": [
            dict(q0=1, nrows=20, xs=_xs(2190), y0=420.0, pitch=100.0),
        ],
    },
    "통합사회": {
        "objective": [
            dict(q0=1,  nrows=20, xs=_xs(1280), y0=420.0, pitch=100.0),
            dict(q0=21, nrows=5,  xs=_xs(1740), y0=420.0, pitch=100.0),
        ],
    },
    "통합과학": {
        "objective": [
            dict(q0=1,  nrows=20, xs=_xs(1280), y0=420.0, pitch=100.0),
            dict(q0=21, nrows=5,  xs=_xs(1740), y0=420.0, pitch=100.0),
        ],
    },
}


# ── 그리기 ────────────────────────────────────────────────────────
def _blank_card() -> np.ndarray:
    img = np.full((H, W, 3), BG, dtype=np.uint8)
    # 상단 타이밍 마크: x 81~3202 균등 32개, y 40~95 (앵커 y중심 ≈ 67)
    n = 32
    for i in range(n):
        cx = 81 + (3202 - 81) * i / (n - 1)
        cv2.rectangle(img, (int(cx - 11), 40), (int(cx + 11), 95), (0, 0, 0), -1)
    return img


def _bubble(img, cx, cy, filled=False):
    if filled:
        cv2.ellipse(img, (int(cx), int(cy)), (19, 13), 0, 0, 360, INK, -1)
    else:
        cv2.ellipse(img, (int(cx), int(cy)), (19, 13), 0, 0, 360, RED, 2)


def _digit_grid(img, cols, y0, pitch, marks: dict[int, int] | None = None):
    """세로 0~9 숫자열들. marks={열index: 숫자} 는 채움."""
    for ci, cx in enumerate(cols):
        for d in range(10):
            _bubble(img, cx, y0 + pitch * d, filled=(marks or {}).get(ci) == d)


def draw_card(subject: str, *, grade: int, ban: str, beon: str,
              answers: dict[int, int] | None = None) -> np.ndarray:
    """카드 1장 렌더. answers: 객관식 {q:1~5}, 수학 단답 {q:0~999}."""
    spec = CARDS[subject]
    img = _blank_card()
    answers = answers or {}

    # 수험번호 (학년/반/번호)
    id_marks = {0: grade, 1: int(ban[0]), 2: int(ban[1]), 3: int(beon[0]), 4: int(beon[1])}
    _digit_grid(img, ID_COLS, ID_Y0, ID_PITCH, id_marks)

    # 객관식
    for blk in spec["objective"]:
        for r in range(blk["nrows"]):
            q = blk["q0"] + r
            cy = blk["y0"] + blk["pitch"] * r
            stu = answers.get(q, 0)
            for i, cx in enumerate(blk["xs"], 1):
                _bubble(img, cx, cy, filled=(stu == i))

    # 수학 단답형 (백·십·일)
    dx = spec.get("short_digit_dx", 61)
    for q, s in (spec.get("short") or {}).items():
        val = answers.get(q)
        digits = {}
        if val is not None:
            b, si, o = val // 100, (val // 10) % 10, val % 10
            # 실물 규칙: 앞자리 0은 마킹 생략 가능 → 생략형으로 마킹 (판독기 0 처리 검증)
            if b:
                digits[0] = b
            if b or si:
                digits[1] = si
            digits[2] = o
        _digit_grid(img, [s["sib"] - dx, s["sib"], s["sib"] + dx],
                    s["y0"], s["pitch"], digits)
    return img


# ── 학생 데이터 생성 (결정적) ─────────────────────────────────────
def make_students(keys_dir: Path, irecord: str, n: int = 6, grade: int = 1):
    """정답키 기반으로 학생 n명 답안 생성. 반환: [(학생정보, {과목: answers}, {과목: 기대점수})]"""
    rng = random.Random(20260703)
    key_by_subject = {}
    for p in Path(keys_dir).glob(f"{irecord}_*_xip_api.json"):
        k = json.loads(p.read_text(encoding="utf-8"))
        key_by_subject[k["subject"]] = k

    students = []
    for i in range(1, n + 1):
        info = dict(grade=grade, ban="03", beon=f"{i:02d}", name=f"가상{i:02d}")
        per_subj_answers, per_subj_expect = {}, {}
        for subject, key in key_by_subject.items():
            kans = {int(q): int(v) for q, v in key["answers"].items()}
            kpts = {int(q): float(v) for q, v in key["points"].items()}
            answers, expect = {}, 0.0
            for q, correct in kans.items():
                is_short = subject == "수학" and q >= 22
                if rng.random() < 0.7:               # 70% 정답
                    answers[q] = correct
                    expect += kpts[q]
                else:                                 # 오답 (정답과 다른 값)
                    if is_short:
                        wrongv = (correct + rng.randint(1, 200)) % 1000
                        answers[q] = wrongv if wrongv != correct else (correct + 1) % 1000
                    else:
                        answers[q] = (correct % 5) + 1
            per_subj_answers[subject] = answers
            per_subj_expect[subject] = int(expect) if expect.is_integer() else round(expect, 1)
        students.append((info, per_subj_answers, per_subj_expect))
    return students


def render_pdfs(out_dir: Path, keys_dir: Path = Path("keys"),
                irecord: str = "202606041", n: int = 6, grade: int = 1) -> dict:
    """과목별 가상 스캔 PDF 생성. 반환 {subject: pdf_path, '_expected': {...}}"""
    import fitz
    out_dir.mkdir(parents=True, exist_ok=True)
    students = make_students(keys_dir, irecord, n=n, grade=grade)

    result = {}
    expected = {}
    for subject in CARDS:
        doc = fitz.open()
        for info, ans_by_subj, exp_by_subj in students:
            img = draw_card(subject, grade=info["grade"], ban=info["ban"],
                            beon=info["beon"], answers=ans_by_subj.get(subject, {}))
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            page = doc.new_page(width=792, height=612)   # 300dpi → 3300x2550
            page.insert_image(fitz.Rect(0, 0, 792, 612), stream=buf.tobytes())
        pdf = out_dir / f"synth_{subject}.pdf"
        doc.save(pdf)
        doc.close()
        result[subject] = pdf

    for info, _, exp in students:
        expected[(info["ban"], info["beon"])] = dict(name=info["name"], **exp)
    result["_expected"] = expected
    result["_students"] = [s[0] for s in students]

    # 성명 조인용 CSV
    names_csv = out_dir / "synth_names.csv"
    with open(names_csv, "w", newline="", encoding="utf-8-sig") as f:
        import csv as _csv
        w = _csv.writer(f)
        w.writerow(["반", "번호", "성명"])
        for info, _, _ in students:
            w.writerow([info["ban"], info["beon"], info["name"]])
    result["_names_csv"] = names_csv
    return result


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("work/synth_g12")
    res = render_pdfs(out)
    for k, v in res.items():
        if not str(k).startswith("_"):
            print(f"{k}: {v}")
    print("기대 점수:", json.dumps(res["_expected"], ensure_ascii=False, default=str))
