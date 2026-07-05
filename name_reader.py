# -*- coding: utf-8 -*-
"""
성명 OMR 그리드 판독 → 한글 이름 텍스트
score_lens / omr_scorer

평가원 답안지 '성명' 란: 음절마다 3칸(초성 자음 | 중성 모음 | 종성 자음).
각 칸은 세로로 자모를 나열하고 학생이 해당 자모를 마킹한다.
초성·중성은 반드시, 종성은 받침 있을 때만(없으면 칸 전체 공란).
판독한 자모를 유니코드 한글로 합성한다.
"""
from __future__ import annotations
import numpy as np
import cv2

import omr_core as oc

# 자모 목록 (평가원 성명란 세로 순서)
CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"            # 초성 19
JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"        # 중성 21
# 종성칸은 초성과 다른 21줄(겹받침 포함). page1 실측으로 확정한 세로 순서:
JONG_COL = "ㄱㄲㄴㄵㄷㄹㄺㄻㄼㄽㅁㅂㅅㅆㅇㅈㅊㅋㅌㅍㅎ"   # 21
# 자음 → 유니코드 종성 인덱스(0=받침없음). ㄸㅃㅉ 는 종성 불가.
JONG_INDEX = {"ㄱ":1,"ㄲ":2,"ㄳ":3,"ㄴ":4,"ㄵ":5,"ㄶ":6,"ㄷ":7,"ㄹ":8,
              "ㄺ":9,"ㄻ":10,"ㄼ":11,"ㄽ":12,"ㄾ":13,"ㄿ":14,"ㅀ":15,
              "ㅁ":16,"ㅂ":17,"ㅄ":18,"ㅅ":19,"ㅆ":20,"ㅇ":21,"ㅈ":22,
              "ㅊ":23,"ㅋ":24,"ㅌ":25,"ㅍ":26,"ㅎ":27}

# 성명 그리드 기준 좌표 (3300x2550, page1 실측 보정)
NAME_GRID = dict(
    col0=1055.0, col_pitch=60.3,   # 9열: col0 + 60.3*i (음절s: 초성3s, 중성3s+1, 종성3s+2)
    row0=680.0, row_pitch=49.9,    # 자모 행
    n_syllables=4,
)


def _pick(gray, x, ys, fill_min):
    d = np.array([oc._darkness(gray, x, y, rad=12) for y in ys])
    idx = int(np.argmax(d))
    return idx, float(d[idx])


def compose(cho_i: int, jung_i: int, jong_i: int) -> str:
    return chr(0xAC00 + (cho_i * 21 + jung_i) * 28 + jong_i)


def read_name(gray, grid: dict = NAME_GRID, fill_min: float = 90.0) -> str:
    col0, cp = grid["col0"], grid["col_pitch"]
    r0, rp = grid["row0"], grid["row_pitch"]
    cho_ys = [r0 + rp * k for k in range(len(CHO))]
    jung_ys = [r0 + rp * k for k in range(len(JUNG))]
    jong_ys = [r0 + rp * k for k in range(len(JONG_COL))]
    name = []
    for s in range(grid["n_syllables"]):
        x_cho = col0 + cp * (3 * s)
        x_jung = col0 + cp * (3 * s + 1)
        x_jong = col0 + cp * (3 * s + 2)
        ci, cd = _pick(gray, x_cho, cho_ys, fill_min)
        if cd < fill_min:
            break                         # 초성 공란 → 이름 끝
        ji, jd = _pick(gray, x_jung, jung_ys, fill_min)
        ki, kd = _pick(gray, x_jong, jong_ys, fill_min)
        jong_i = JONG_INDEX.get(JONG_COL[ki], 0) if kd >= fill_min else 0
        name.append(compose(ci, ji if jd >= fill_min else 0, jong_i))
    return "".join(name)


def read_name_from_page(path: str) -> str:
    img = oc.load_page(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return read_name(gray)


if __name__ == "__main__":
    import sys
    print(read_name_from_page(sys.argv[1]))
