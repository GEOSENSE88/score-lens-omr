# -*- coding: utf-8 -*-
"""고3 학평(hp_*) 판독기 단위 테스트 — 합성 이미지 기반(실물 스캔·PII 불필요).

커밋된 템플릿 좌표에 마킹을 합성으로 찍어 판독 결과를 검증한다.
실행: python tests_hp.py  (tests_regression.py 가 서브프로세스로도 호출)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

import hp_align
import hp_g3
import hp_id
import name_reader as nr

HERE = Path(__file__).resolve().parent
PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✕ {name} {detail}")


def blank_card() -> np.ndarray:
    return np.full((2550, 3300, 3), 255, np.uint8)


def mark(img, x, y, r=17, color=(20, 20, 20)):
    cv2.circle(img, (int(x), int(y)), r, color, -1)


TMPL = json.loads((HERE / "templates" / "korean_g3hp.json").read_text(encoding="utf-8"))
NAME_G = next(g for g in TMPL["grids"] if g["name"] == "name")
ID_G = next(g for g in TMPL["grids"] if g["name"] == "id")
MATH = json.loads((HERE / "templates" / "math_g3hp.json").read_text(encoding="utf-8"))
SA16 = next(g for g in MATH["grids"] if g["name"] == "sa16")


def stamp_syllable(img, s, cho=None, jung=None, jong=None):
    xs, ys = NAME_G["xs"], NAME_G["ys"]
    if cho is not None:
        mark(img, xs[3 * s], ys[nr.CHO.index(cho)])
    if jung is not None:
        mark(img, xs[3 * s + 1], ys[nr.JUNG.index(jung)])
    if jong is not None:
        mark(img, xs[3 * s + 2], ys[nr.JONG_COL.index(jong)])


def t_name():
    print("[성명 판독]")
    img = blank_card()
    stamp_syllable(img, 0, "ㄱ", "ㅝ", "ㄴ")   # 권
    stamp_syllable(img, 1, "ㅅ", "ㅔ")          # 세
    stamp_syllable(img, 2, "ㅈ", "ㅣ", "ㄴ")   # 진
    r = hp_id.read_name(hp_g3.pencil_gray(img), NAME_G)
    check("정상 3음절 '권세진'", r["name"] == "권세진" and r["ok"], str(r))

    img = blank_card()                          # 중성 누락 — 조용한 생성 금지
    stamp_syllable(img, 0, "ㄱ", "ㅝ", "ㄴ")
    xs, ys = NAME_G["xs"], NAME_G["ys"]
    mark(img, xs[3], ys[nr.CHO.index("ㅅ")])    # 2음절 초성만
    r = hp_id.read_name(hp_g3.pencil_gray(img), NAME_G)
    check("중성 누락 → '?' + ok=False", "?" in r["name"] and not r["ok"], str(r))
    check("중성 누락 사유 기록", any("중성" in i for i in r["issues"]), str(r))

    img = blank_card()                          # 자모 중복 마킹 의심
    stamp_syllable(img, 0, "ㄱ", "ㅏ")
    mark(img, NAME_G["xs"][0], NAME_G["ys"][nr.CHO.index("ㄴ")])   # 초성 2개
    r = hp_id.read_name(hp_g3.pencil_gray(img), NAME_G)
    check("자모 중복 → issue", not r["ok"] and any("중복" in i for i in r["issues"]), str(r))

    r = hp_id.read_name(hp_g3.pencil_gray(blank_card()), NAME_G)
    check("빈 성명 → '' + ok", r["name"] == "" and r["ok"], str(r))


def t_id():
    print("[수험번호 판독]")
    img = blank_card()
    digits = [1, 9, 7, 1, 8, 3, 0, 1, 0, 2]     # 학교19718+학년3+반01+번호02
    for c, d in enumerate(digits):
        mark(img, ID_G["xs"][c], ID_G["ys"][d])
    r = hp_id.read_id(hp_g3.pencil_gray(img), ID_G)
    check("school=19718", r["school"] == 19718, str(r))
    check("grade=3 반=1 번호=2", (r["grade"], r["ban"], r["num"]) == (3, 1, 2), str(r))

    mark(img, ID_G["xs"][8], ID_G["ys"][7])     # 번호 십의자리 중복
    r = hp_id.read_id(hp_g3.pencil_gray(img), ID_G)
    check("중복 열 → 번호 None", r["num"] is None, str(r))


def t_short_answer():
    print("[수학 단답형]")
    img = blank_card()
    for c, d in enumerate([1, 2, 6]):           # 126
        mark(img, SA16["xs"][c], SA16["ys"][d])
    g = hp_g3.pencil_gray(img)
    check("백십일 126", hp_id.read_short_answer(g, SA16) == 126)
    img = blank_card()
    mark(img, SA16["xs"][2], SA16["ys"][7])     # 일의 자리만 = 7
    check("한 자리 7", hp_id.read_short_answer(hp_g3.pencil_gray(img), SA16) == 7)
    check("전체 공란 → None",
          hp_id.read_short_answer(hp_g3.pencil_gray(blank_card()), SA16) is None)
    img = blank_card()
    mark(img, SA16["xs"][2], SA16["ys"][1])
    mark(img, SA16["xs"][2], SA16["ys"][5])     # 같은 열 2개 → 판독불가
    check("열 중복 → -1", hp_id.read_short_answer(hp_g3.pencil_gray(img), SA16) == -1)


def realistic_card() -> np.ndarray:
    """실물 유사 합성 카드 — 연두 바탕 + 분홍 인쇄 원 격자 + 검정 마킹."""
    img = np.full((2550, 3300, 3), (235, 250, 240), np.uint8)   # 옅은 바탕색
    for gy in range(400, 2400, 100):
        for gx in range(1800, 3100, 60):
            cv2.circle(img, (gx, gy), 18, (180, 180, 250), 2)   # 분홍 인쇄 원
    for k in range(40):
        cv2.circle(img, (1830 + (k % 5) * 240, 430 + (k // 5) * 240), 16, (25, 25, 25), -1)
    return img


def t_normalize_scan():
    print("[스캔 정규화·흑백 감지]")
    img = realistic_card()
    _, is_gray = hp_g3.normalize_scan(img)
    check("컬러 스캔 → gray 아님", not is_gray)
    gray3 = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    _, is_gray = hp_g3.normalize_scan(gray3)
    check("흑백 스캔 감지", is_gray)
    dark = np.clip(img.astype(int) - 60, 0, 255).astype(np.uint8)
    fixed, _ = hp_g3.normalize_scan(dark)
    check("어두운 스캔 스트레치(백색점 복원)", float(np.percentile(fixed, 99)) > 240,
          f"p99={np.percentile(fixed, 99):.0f}")
    blank_dark = np.full((2550, 3300, 3), 195, np.uint8)
    fixed, _ = hp_g3.normalize_scan(blank_dark)
    check("균일 페이지 스트레치 미적용(발산 방지)",
          float(np.percentile(fixed, 50)) > 150, f"p50={np.percentile(fixed, 50):.0f}")


def t_profile_shift():
    print("[x-프로파일 시프트 판별]")
    rng = np.random.default_rng(3)
    refp = np.zeros(3300, np.float32)
    for x in rng.choice(np.arange(100, 3200), 60, replace=False):
        refp[x - 3:x + 3] = rng.uniform(0.2, 0.8)
    for true_k, pitch in ((0, 60.1), (1, 60.1), (-1, 60.1)):
        prof = np.roll(refp, int(round(true_k * pitch)))
        scores = {k: hp_g3._shift_corr(prof, refp, int(round(k * pitch)))
                  for k in (-2, -1, 0, 1, 2)}
        best = max(scores, key=scores.get)
        check(f"시프트 {true_k:+d} 검출", best == true_k, str(scores))


def t_unique_matching():
    print("[정렬 마크 1:1 매칭]")
    ref = np.array([100.0 + 60.2 * k for k in range(30)])
    page = np.sort(np.concatenate([ref + 0.5, np.array([100.4, 100.9, 160.7])]))
    m = hp_align._match_to_ref(page, ref)
    check("매칭 성공", m is not None)
    if m:
        s, b, nmatch, resid, p90 = m
        check("nmatch 부풀림 없음(≤기준 마크 수)", nmatch <= len(ref),
              f"nmatch={nmatch}")
        check("배율≈1·잔차 작음", abs(s - 1) < 0.01 and resid < 2.0,
              f"s={s:.4f} resid={resid:.2f}")


def t_select():
    print("[선택박스 상대우세]")
    sel = {"name": "choice", "options": [["화작", 834, 1180], ["언매", 868, 1330]]}
    img = blank_card()
    mark(img, 834, 1180, r=14, color=(120, 120, 120))    # 연한 점 마킹
    g = hp_g3.pencil_gray(img)
    check("연한 마킹 상대우세 채택", hp_id.read_select(g, sel) == "화작")
    check("빈 박스 → None", hp_id.read_select(hp_g3.pencil_gray(blank_card()), sel) is None)


def t_decide_cell():
    print("[마킹 판정(지운자국·연한마킹)]")
    fm = 55.0
    check("정상 단일", hp_g3.decide_cell([2, 3, 120, 4, 2], fm) == 3)
    check("지운 자국(120 vs 58) → 진한 쪽", hp_g3.decide_cell([2, 58, 120, 4, 2], fm) == 3)
    check("진짜 이중(110 vs 95) → -1", hp_g3.decide_cell([2, 95, 110, 4, 2], fm) == -1)
    check("연한 점(30, 2위 4) → 채택", hp_g3.decide_cell([2, 4, 30, 3, 2], fm) == 3)
    check("애매(24 vs 12) → 0", hp_g3.decide_cell([2, 12, 24, 3, 2], fm) == 0)
    check("전부 공란 → 0", hp_g3.decide_cell([1, 2, 1, 2, 1], fm) == 0)


def main() -> int:
    for t in (t_name, t_id, t_short_answer, t_normalize_scan,
              t_profile_shift, t_unique_matching, t_select, t_decide_cell):
        t()
    print(f"\ntests_hp: PASS {PASS} / FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
