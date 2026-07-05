# -*- coding: utf-8 -*-
"""
English OMR reader for the 2027 June evaluation-style answer sheet.

The sheet has 45 multiple-choice questions in three blocks:
1-20, 21-40, and 41-45. Coordinates live in templates/*.json so another
English layout can be tested without touching the reader.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

import omr_core as oc


def _normalize_template(tmpl: dict) -> dict:
    out = dict(tmpl)
    out["objective"] = [dict(block) for block in out["objective"]]
    return out


def load_template(path: str | Path | None = None) -> dict:
    path = Path(path) if path else Path(__file__).with_name("templates") / "english_eval_2027_06.json"
    return _normalize_template(json.loads(path.read_text(encoding="utf-8")))


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
        return -1
    return i + 1


def read_answers(gray, xf=_idnt, template: dict | None = None) -> dict[int, int]:
    template = template or DEFAULT_TEMPLATE
    answers: dict[int, int] = {}
    for block in template["objective"]:
        for r in range(block["nrows"]):
            q = block["q0"] + r
            answers[q] = _choice(gray, block["xs"], block["y0"] + block["pitch"] * r, xf)
    return answers


def process_page(path: str, template: dict | None = None) -> dict:
    template = template or DEFAULT_TEMPLATE
    img = oc.load_page(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    xf = oc.make_anchor_transform(gray)
    return dict(path=path, subject="영어", answers=read_answers(gray, xf, template))
