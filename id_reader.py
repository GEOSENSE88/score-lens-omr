# -*- coding: utf-8 -*-
"""
수험번호 OMR 판독 → 학교(학원)번호 / 반 / 번호  (또는 고1·2: 학년 / 반 / 번호)
score_lens / omr_scorer

좌측 '수험번호' 격자는 숫자열 여러 개(각 열 0~9 세로 마킹)로 구성된다.
열별로 가장 진한 행을 그 자리 숫자로 읽는다.

카드 양식별로 열 개수·좌표가 다르므로 LAYOUTS 로 분리한다.
  - g3   : 고3 평가원 = 학교(학원)번호 5 + 반 2 + 번호 2  (검증: 산남고 전원 19718)
  - g12  : 고1·2 전국연합 = 학년 1 + 반 2 + 번호 2
           ⚠️ cols/y0/pitch 는 실제 고1·2 스캔에서 보정해 채워야 한다(현재 placeholder).
"""
from __future__ import annotations
import numpy as np
import cv2

import omr_core as oc

ID_FILL_MIN = 60.0

# 3300x2550 기준 좌표
LAYOUTS = {
    # 고3 평가원 (page1 실측, 검증 완료)
    "g3": {
        "fields": [("school", 5), ("ban", 2), ("beon", 2)],
        "cols": [151, 212, 272, 332, 394,   # 학교(학원)번호 5
                 512, 574,                   # 반 2
                 634, 694],                  # 번호 2
        "y0": 1185.0,
        "pitch": 99.5,
    },
    # 고1·2 전국연합 (학년1 + 반2 + 번호2)
    # ⚠️ 현재 좌표는 synth_omr.py 가상 카드 설계값 (파이프라인 검증용).
    #    실물 스캔이 오면 실측해 교체할 것.
    "g12": {
        "fields": [("grade", 1), ("ban", 2), ("beon", 2)],
        "cols": [300, 380, 460, 560, 640],
        "y0": 1150.0,
        "pitch": 99.5,
        "_synthetic": True,
    },
}

DEFAULT_LAYOUT = "g3"


def _digit(gray, cx, y0, pitch, fill_min=ID_FILL_MIN, xf=None):
    if xf is None:
        xf = lambda x, y: (x, y)
    d = []
    for r in range(10):
        tx, ty = xf(cx, y0 + pitch * r)
        d.append(oc._darkness(gray, tx, ty, 13))
    return int(np.argmax(d)) if max(d) >= fill_min else -1


def read_id(gray, layout: str | dict = DEFAULT_LAYOUT, xf=None) -> dict:
    """수험번호 판독. layout 은 LAYOUTS 키 또는 layout dict.

    xf: 상단 타이밍마크 앵커 변환(omr_core.make_anchor_transform) — 넘기면
    스캔 이동/배율을 답안 판독과 동일하게 보정한다 (없으면 항등).
    """
    lay = LAYOUTS[layout] if isinstance(layout, str) else layout
    if lay.get("cols") is None:
        raise ValueError(f"수험번호 레이아웃 좌표가 아직 보정되지 않았습니다: {lay}")
    cols, y0, pitch = lay["cols"], lay["y0"], lay["pitch"]
    digs = [_digit(gray, cx, y0, pitch, xf=xf) for cx in cols]

    def join(seq):
        return "".join(str(x) if x >= 0 else "?" for x in seq)

    out, i = {}, 0
    for name, width in lay["fields"]:
        out[name] = join(digs[i:i + width])
        i += width
    # 하위호환: g12 에는 school 이 없으므로 빈 문자열로 채워 downstream(run_*.py) 이 안전하게 동작
    out.setdefault("school", "")
    return out


def read_id_from_page(path: str, layout: str | dict = DEFAULT_LAYOUT) -> dict:
    img = oc.load_page(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return read_id(gray, layout)


if __name__ == "__main__":
    import sys
    lay = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_LAYOUT
    print(read_id_from_page(sys.argv[1], lay))
