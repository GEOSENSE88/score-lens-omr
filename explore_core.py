# -*- coding: utf-8 -*-
"""Inquiry OMR reader for the 2027 June evaluation-style answer sheet."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

import omr_core as oc


def load_template(path: str | Path | None = None) -> dict:
    path = Path(path) if path else Path(__file__).with_name("templates") / "explore_eval_2027_06.json"
    tmpl = json.loads(path.read_text(encoding="utf-8"))
    tmpl["selections"] = [dict(selection) for selection in tmpl["selections"]]
    return tmpl


DEFAULT_TEMPLATE = load_template()


def _dark(gray, x, y, xf, r=14):
    tx, ty = xf(x, y)
    return oc._darkness(gray, tx, ty, r)


def _choice(gray, xs, cy, xf, fill_min=55.0, margin=18.0):
    d = np.array([_dark(gray, x, cy, xf) for x in xs])
    i = int(np.argmax(d))
    others = np.delete(d, i)
    if d[i] < fill_min or (d[i] - others.mean()) < margin:
        return 0
    if (np.max(others) if len(others) else 0) >= fill_min and (d[i] - np.max(others)) < 10:
        return -1
    return i + 1


def _digit(gray, x, y0, pitch, xf, fill_min=70.0):
    d = np.array([_dark(gray, x, y0 + pitch * r, xf, r=14) for r in range(10)])
    i = int(np.argmax(d))
    return i if d[i] >= fill_min else -1


def read_selection(gray, selection: dict, xf) -> dict:
    code_cfg = selection["subject_code"]
    tens = _digit(gray, code_cfg["xs"][0], code_cfg["y0"], code_cfg["pitch"], xf)
    ones = _digit(gray, code_cfg["xs"][1], code_cfg["y0"], code_cfg["pitch"], xf)
    code = tens * 10 + ones if tens >= 0 and ones >= 0 else None

    ans_cfg = selection["answers"]
    answers: dict[int, int] = {}
    for r in range(ans_cfg["nrows"]):
        q = ans_cfg["q0"] + r
        answers[q] = _choice(gray, ans_cfg["xs"], ans_cfg["y0"] + ans_cfg["pitch"] * r, xf)
    return {"code": code, "answers": answers}


def process_page(path: str, template: dict | None = None) -> dict:
    template = template or DEFAULT_TEMPLATE
    img = oc.load_page(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    xf = oc.make_anchor_transform(gray)
    selections = {}
    for selection in template["selections"]:
        selections[selection["name"]] = read_selection(gray, selection, xf)
    return dict(path=path, subject="탐구", selections=selections)
