# -*- coding: utf-8 -*-
"""
수학영역 OMR 판독 엔진 (평가원 ② 수학 답안지)
score_lens / omr_scorer

구성: 객관식(공통 1-15, 선택 23-28, 5지선다) + 단답형(16-22, 29-30, 백·십·일 숫자) + 선택과목(확통/미적/기하).
좌표는 실제 스캔 답안지(확통)에서 마킹 역산 + 정답키 대조로 검증 (23/30 일치, 오답은 최고난도).
omr_core 의 load_page / _darkness 재사용.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import cv2

import omr_core as oc

_BUILTIN_TEMPLATE = {
    "objective": [
        {"q0": 1, "nrows": 8, "xs": [1134, 1194, 1255, 1315, 1375], "y0": 378.0, "pitch": 99.0},
        {"q0": 9, "nrows": 7, "xs": [1545, 1609, 1673, 1737, 1801], "y0": 378.0, "pitch": 99.0},
        {"q0": 23, "nrows": 6, "xs": [2446, 2503, 2564, 2625, 2683], "y0": 1523.0, "pitch": 99.0},
    ],
    "short_digit_dx": 61,
    "short": {
        16: {"sib": 2166, "y0": 369, "pitch": 99.5},
        17: {"sib": 2379, "y0": 369, "pitch": 99.5},
        18: {"sib": 2613, "y0": 369, "pitch": 99.5},
        19: {"sib": 2844, "y0": 369, "pitch": 99.5},
        20: {"sib": 3052, "y0": 369, "pitch": 99.5},
        21: {"sib": 1953, "y0": 1514, "pitch": 100.0},
        22: {"sib": 2166, "y0": 1514, "pitch": 100.0},
        29: {"sib": 2857, "y0": 1514, "pitch": 100.0},
        30: {"sib": 3061, "y0": 1514, "pitch": 100.0},
    },
    "subject_marks": {"확률과통계": [898, 1137], "미적분": [898, 1280], "기하": [898, 1423]},
}


def _normalize_template(tmpl: dict) -> dict:
    out = dict(tmpl)
    out["objective"] = [dict(block) for block in out.get("objective", [])]
    out["short"] = {int(q): dict(v) for q, v in out.get("short", {}).items()}
    out["subject_marks"] = {name: tuple(pos) for name, pos in out.get("subject_marks", {}).items()}
    out.setdefault("short_digit_dx", 61)
    return out


def load_template(path: str | Path | None = None) -> dict:
    path = Path(path) if path else Path(__file__).with_name("templates") / "math_eval_2027_06.json"
    if path.exists():
        return _normalize_template(json.loads(path.read_text(encoding="utf-8")))
    return _normalize_template(_BUILTIN_TEMPLATE)


DEFAULT_TEMPLATE = load_template()


def _idnt(x, y):
    return (x, y)


def _dark(gray, x, y, xf, r=14):
    tx, ty = xf(x, y)
    return oc._darkness(gray, tx, ty, r)


def _choice(gray, xs, cy, xf=_idnt, fill_min=55.0, margin=18.0):
    d = np.array([_dark(gray, x, cy, xf) for x in xs])
    i = int(np.argmax(d))
    others = np.delete(d, i)
    if d[i] < fill_min or (d[i] - others.mean()) < margin:
        return 0
    if (np.max(others) if len(others) else 0) >= fill_min and (d[i] - np.max(others)) < 10:
        return -1                       # 중복
    return i + 1


def _digit(gray, cx, y0, pitch, xf=_idnt, fill_min=70.0):
    d = [_dark(gray, cx, y0 + pitch * r, xf) for r in range(10)]
    return int(np.argmax(d)) if max(d) >= fill_min else 0


def _short_value(gray, sib, y0, pitch, xf=_idnt, digit_dx=61):
    b = _digit(gray, sib - digit_dx, y0, pitch, xf)
    s = _digit(gray, sib, y0, pitch, xf)
    o = _digit(gray, sib + digit_dx, y0, pitch, xf)
    return b * 100 + s * 10 + o


def read_objective(gray, xf=_idnt, template: dict | None = None) -> dict:
    template = template or DEFAULT_TEMPLATE
    ans = {}
    for block in template["objective"]:
        for r in range(block["nrows"]):
            ans[block["q0"] + r] = _choice(gray, block["xs"], block["y0"] + block["pitch"] * r, xf)
    return ans


def read_short(gray, xf=_idnt, template: dict | None = None) -> dict:
    template = template or DEFAULT_TEMPLATE
    digit_dx = template["short_digit_dx"]
    return {
        q: _short_value(gray, spec["sib"], spec["y0"], spec["pitch"], xf, digit_dx)
        for q, spec in template["short"].items()
    }


def read_subject(gray, xf=_idnt, fill_min=38.0, margin=10.0, template: dict | None = None):
    template = template or DEFAULT_TEMPLATE
    marks = template.get("subject_marks") or {}
    if len(marks) < 2:
        return None                         # 선택과목 없는 양식(예: 고1·2 수학)
    sc = {nm: max(_dark(gray, x + dx, y + dy, xf, 15)
                  for dx in (-18, -9, 0, 9, 18) for dy in (-20, -10, 0, 10, 20))
          for nm, (x, y) in marks.items()}
    ordered = sorted(sc.values(), reverse=True)
    best = max(sc, key=sc.get)
    if sc[best] >= fill_min and (sc[best] - ordered[1]) >= margin:
        return best
    return None


def process_page(path: str, template: dict | None = None) -> dict:
    template = template or DEFAULT_TEMPLATE
    img = oc.load_page(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    xf = oc.make_anchor_transform(gray)   # 상단 타이밍 마크로 스캔 정렬 보정
    ans = read_objective(gray, xf, template)
    ans.update(read_short(gray, xf, template))
    return dict(path=path, subject=read_subject(gray, xf, template=template), answers=ans)
