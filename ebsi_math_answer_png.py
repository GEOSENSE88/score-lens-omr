# -*- coding: utf-8 -*-
"""EBSi 수학 ``math_main_ans_*.png`` 정답표 파서.

EBSi가 해설 PDF보다 정답 이미지를 먼저 게시하는 시간대에도 수학 키를
생성할 수 있게 한다. 표의 선을 먼저 찾고, 작은 글자 템플릿은 2026년
6월·9월 및 2025년 6월·9월 EBSi 공식 정답표에서 추출했다.
"""
from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path

import cv2
import numpy as np


# 비트맵 템플릿을 zlib+base64로 압축했다. 원본 정답표 전체를 배포하지
# 않고 원문자 1~5와 숫자 0~9의 형태만 보존한다.
_MODEL_B64 = (
    "eNrtW11v2zYU/S9+VQB2K7NpBfpwryiQKiBIHvbCDHmZajHJkPalG4cV+e87V5Jt5cPJsrpJvDIJzFDi4T2k7hdJ+fPi428Xq+7T+Z+rxZvPi+8Wb35d0PDTZvhQmiinFZnImjqiJeMzlhwNh55qyzlFwt8lGSo81fQu3F0j1CrvMsHpmtF1LAk3pM+thEGe/LxdHCUaiUai8QgaLsdHDyEN4Z42AR1xYGqYStIlh19CbXGTvNwn3PeoQbAWwaHY1ILc27QEjVxoSC/4Q59xK2GQl2gkGolGopFoJBqJxtejcXq0+P7B/JwV8gWXI3sIVW0LCoAXEE8VxFN4J5xnWQfEXkJmARLIOlrJOtQ+kp9EI9HYaS8ZNN2IvSyXsBdouycn7Tv8w2IhJQSz2EslNfmtiaxI9pDMZ2ovZptoJBqJRqKRaCQaiUai8aU0kJ+/fnj/vJddQcnBvK4tX2xyMA3xBjkY59cysik/Q1+h8kPu1sd9bE4mGolGopFovHAaj3XpS/yTwYkb2XKhTmhE0DBw6UBalpalQxdt+JqRJdFINBKNROOZaSAj1Q9npK04Y3HNLoIMHDyx7AP6WHi+AJlKagG1wqCmMro0ShfivitPNXC0l8iSaCQaL4TGLrO1YnAeZmvkoAdcvRHs+0CeLQSXni75DAvJVsFslYLZshz0WDno8dLFXrxHopFoJBqJRqKRaBwYDWSkx//iHYYQy+ENSC9vTsqrkVbOonsJ90q2aJu7NxnMXvc6Eo1EY5e9sIKmu1xWcEJjOimQzmAvsoJzu1Zw0rLc10Iy0Ug0Eo1E46XSOL06Wrw/D+ef5MtFr6bAj777YD6Qtib4hoPGoh0LeCzfxxL18bql48jZ0NM0oueBIhNqA4LLquHoHYeu4FBZCn5bDtdXjQz9W4Qirdt8dUySQQTUVpMKxk71e8qt7IODGsk2eMBlVOuxTtfLclafS13CeGA4LtIJ8e9TvdxdPjsUOVSFGfAZX1S2nivEtXJsJ/7iFhTqYiqqt02kvNbFbejBTdN/gs5e7R78VONornFD225UIycvkK+1q1Xhpos7JOja2ZTNHGpk/syU2U89sdLusKGcQyewerFOWqOtmYxiPP/hD2Q9nQyOQuVGxeckPHuTafCKhZur7NBLG4T2/KkbjubOUFLWW22XkCHX4/gNiAGKqKt2RNwXBd3Mga8Pg/CLgs7PIgdv4CTosvRFf0jw/Du4hqI2WKg3RuXzIDpzr52oXSu7AiaSItPRz5Ed1Z05i3BCsROom9o52hN0S9hsCPNEGHmjUoC2Kt5PGCGhJ+7oJCINbTo+i6VjpZzKe0DvJfwI6GyHrc/xANYDGh+KCi6TCzeTGo7u2jNW0ajNaEZ3lYkvkJO3OE3Sq7E0w/VZPMiRGUI91p1DO3pRF1mK6LnU21b05FBM1g/TZA3+Gb7OFWM7DPB1hEfsM9bekSTCsUJp7a2c+RuBYrJ+nCZLZUbpaZqXogZ+MtcwewAbRYlz2ZAxRho7BcFaUKOqTa3LTYyiPUBBO18/40yylb/ikG+aIe+sO9ShJ7Wl6Bu6FHOuGgq5HZexN5ePQ57aDPnqsRgj9Et7cxbWOJRaNiL3A127Wmun1ZglvxTfJdqO+jpzxvVe372Q+59D8XR/2rWvMJXNivqPnK1VE/EKDt/oezcHngS6HnE3rbX9duQywpnPEl+W3wmtOS4dy8nNUHblZLHlZiWyE/oFUp8Kenp19Q8KDsXq"
)


def _decode_model():
    packed = zlib.decompress(base64.b64decode(_MODEL_B64))
    raw = json.loads(packed)
    shapes = {"objective": (28, 28), "digit": (20, 16)}
    model = {}
    for family, labels in raw.items():
        size = int(np.prod(shapes[family]))
        model[family] = {
            label: [
                np.unpackbits(np.frombuffer(base64.b64decode(bits), dtype=np.uint8), count=size)
                .reshape(shapes[family])
                for bits in samples
            ]
            for label, samples in labels.items()
        }
    return model


_MODEL = _decode_model()


def _table_rects(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
    lines = (gray < 100).astype("uint8")
    _, _, stats, _ = cv2.connectedComponentsWithStats(lines, 8)
    rects = [tuple(map(int, row[:4])) for row in stats[1:]
             if row[2] >= 490 and row[3] > 20]
    rects.sort(key=lambda r: r[1])
    if len(rects) != 4 or rects[0][3] < 120 or any(r[3] < 50 for r in rects[1:]):
        raise ValueError(f"EBSi 수학 정답표 격자를 인식하지 못했습니다: {rects}")
    return rects


def _cell(gray: np.ndarray, rect: tuple[int, int, int, int],
          row: int, col: int, rows: int) -> np.ndarray:
    x, y, w, h = rect
    xs = np.rint(np.linspace(x, x + w - 1, 11)).astype(int)
    ys = np.rint(np.linspace(y, y + h - 1, rows + 1)).astype(int)
    return gray[ys[row] + 3:ys[row + 1] - 2, xs[col] + 3:xs[col + 1] - 2]


def _objective_shape(cell: np.ndarray) -> np.ndarray:
    ink = (cell < 128).astype("uint8")
    yy, xx = np.where(ink)
    if not len(xx):
        raise ValueError("객관식 정답 칸이 비어 있습니다")
    ink = ink[yy.min():yy.max() + 1, xx.min():xx.max() + 1]
    h, w = ink.shape
    scale = min(26 / w, 26 / h)
    nw, nh = round(w * scale), round(h * scale)
    resized = cv2.resize(ink, (nw, nh), interpolation=cv2.INTER_NEAREST)
    out = np.zeros((28, 28), dtype="uint8")
    y0, x0 = (28 - nh) // 2, (28 - nw) // 2
    out[y0:y0 + nh, x0:x0 + nw] = resized
    return out


def _short_shapes(cell: np.ndarray) -> list[np.ndarray]:
    ink = (cell < 160).astype("uint8")
    yy, xx = np.where(ink)
    if not len(xx):
        raise ValueError("단답형 정답 칸이 비어 있습니다")
    x0, x1 = int(xx.min()), int(xx.max()) + 1
    width = x1 - x0
    # 수능형 수학 단답은 0~999. EBSi 표의 숫자 간격을 이용해 자릿수를
    # 먼저 결정하면 '44'처럼 인접 글자가 붙어도 분리할 수 있다.
    count = 1 if width <= 10 else 2 if width <= 21 else 3
    cuts = np.rint(np.linspace(x0, x1, count + 1)).astype(int)
    out = []
    for i in range(count):
        part = ink[:, cuts[i]:cuts[i + 1]]
        py, px = np.where(part)
        if not len(px):
            raise ValueError("단답형 숫자 분리에 실패했습니다")
        part = part[py.min():py.max() + 1, px.min():px.max() + 1]
        h, w = part.shape
        scale = min(14 / w, 18 / h)
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        resized = cv2.resize(part, (nw, nh), interpolation=cv2.INTER_NEAREST)
        norm = np.zeros((20, 16), dtype="uint8")
        y0, xx0 = (20 - nh) // 2, (16 - nw) // 2
        norm[y0:y0 + nh, xx0:xx0 + nw] = resized
        out.append(norm)
    return out


def _classify(shape: np.ndarray, family: str) -> int:
    ranked = []
    for label, samples in _MODEL[family].items():
        best = min(float(np.mean(shape != sample)) for sample in samples)
        ranked.append((best, label))
    ranked.sort()
    best, label = ranked[0]
    margin = ranked[1][0] - best
    # 오독으로 잘못된 키를 쓰는 것보다 새 레이아웃을 명시적으로 거부한다.
    if best > 0.32 or margin < 0.008:
        raise ValueError(
            f"EBSi 정답 글자를 안전하게 판별하지 못했습니다"
            f" ({family}, score={best:.3f}, margin={margin:.3f})")
    return int(label)


def _elective_order(gray: np.ndarray,
                    rects: list[tuple[int, int, int, int]]) -> list[str]:
    widths = []
    for rect in rects[1:]:
        y = rect[1]
        heading = gray[max(0, y - 85):y - 35, :] < 160
        yy, xx = np.where(heading)
        if not len(xx):
            raise ValueError("EBSi 수학 선택과목 제목을 찾지 못했습니다")
        widths.append(int(xx.max() - xx.min() + 1))
    ranked = sorted(range(3), key=lambda i: widths[i])
    names = [""] * 3
    names[ranked[0]] = "기하"
    names[ranked[1]] = "미적분"
    names[ranked[2]] = "확률과 통계"
    sw, mw, lw = (widths[i] for i in ranked)
    if not (mw >= sw * 1.2 and lw >= mw * 1.35):
        raise ValueError(f"EBSi 수학 선택과목 제목 폭이 예상과 다릅니다: {widths}")
    return names


def parse_answer_image(source: str | Path | bytes) -> dict[str, dict[int, int]]:
    """EBSi 수학 정답 PNG를 ``{공통/선택: {문항: 정답}}``로 변환한다."""
    if isinstance(source, bytes):
        gray = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    else:
        # cv2.imread는 Windows의 한글 경로를 열지 못하는 버전이 있다.
        try:
            payload = Path(source).read_bytes()
        except OSError as exc:
            raise ValueError(f"EBSi 수학 정답 PNG를 열 수 없습니다: {exc}") from exc
        gray = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("EBSi 수학 정답 PNG를 열 수 없습니다")
    if gray.shape[1] != 600:
        scale = 600 / gray.shape[1]
        gray = cv2.resize(gray, (600, round(gray.shape[0] * scale)), interpolation=cv2.INTER_AREA)

    rects = _table_rects(gray)
    result: dict[str, dict[int, int]] = {"공통": {}}

    q = 1
    for row in range(3):
        for col in (1, 3, 5, 7, 9):
            result["공통"][q] = _classify(
                _objective_shape(_cell(gray, rects[0], row, col, 5)), "objective")
            q += 1
    for col in (1, 3, 5, 7, 9):
        digits = [_classify(s, "digit") for s in
                  _short_shapes(_cell(gray, rects[0], 3, col, 5))]
        result["공통"][q] = int("".join(map(str, digits)))
        q += 1
    for col in (1, 3):
        digits = [_classify(s, "digit") for s in
                  _short_shapes(_cell(gray, rects[0], 4, col, 5))]
        result["공통"][q] = int("".join(map(str, digits)))
        q += 1

    for rect, elective in zip(rects[1:], _elective_order(gray, rects)):
        answers = {}
        q = 23
        for row, cols in ((0, (5, 7, 9)), (1, (1, 3, 5))):
            for col in cols:
                answers[q] = _classify(
                    _objective_shape(_cell(gray, rect, row, col, 2)), "objective")
                q += 1
        for col in (7, 9):
            digits = [_classify(s, "digit") for s in
                      _short_shapes(_cell(gray, rect, 1, col, 2))]
            answers[q] = int("".join(map(str, digits)))
            q += 1
        result[elective] = answers

    if set(result["공통"]) != set(range(1, 23)) or any(
            set(result.get(el, {})) != set(range(23, 31))
            for el in ("확률과 통계", "미적분", "기하")):
        raise ValueError("EBSi 수학 정답표에서 30문항을 모두 읽지 못했습니다")
    return result
