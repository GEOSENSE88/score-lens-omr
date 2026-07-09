# -*- coding: utf-8 -*-
"""고3 학평(시도교육청 전국연합) 답안지 객관식 좌표 보정 — 앵커 비의존.

디지털 렌더(auto_orient 로 3300x2550 고정)라 스캔 이동이 없어 앵커 보정이 오히려
불안정하다. 여러 페이지의 마킹칸 블롭을 REF 좌표에서 그대로 합쳐(union) 블록별
5열 x / y0 / pitch 를 중앙값으로 잡는다. 마킹(검정)으로 빠진 칸은 다른 페이지가
채워주므로 희소 블록(예: 영어 41~45)도 안정적으로 잡힌다.

    python hp_calibrate.py --spec specs/english_g3hp.json --scan eng.pdf \
        --out templates/english_g3hp.json
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

import cv2
import numpy as np
import fitz

import omr_core as oc


def _oriented_pages(pdf: Path, limit: int | None = None):
    doc = fitz.open(str(pdf))
    n = doc.page_count if limit is None else min(limit, doc.page_count)
    for i in range(n):
        pix = doc[i].get_pixmap(dpi=300)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR) if pix.n == 4 else cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        yield oc.auto_orient(img)
    doc.close()


def _pick5(xs_sorted: list[float]) -> list[float]:
    """열 후보 중 ①~⑤ 5열을 고른다: 가장 촘촘한(폭 최소) 연속 5열 창."""
    if len(xs_sorted) < 5:
        return xs_sorted
    return list(min((xs_sorted[i:i + 5] for i in range(len(xs_sorted) - 4)),
                    key=lambda w: w[-1] - w[0]))


def calibrate(spec: dict, pdf: Path) -> dict:
    # 블록별·페이지별로 (xs[5], y0, pitch) 를 뽑아 블록마다 독립적으로 중앙값 합의.
    # (희소 블록은 5열이 다 잡힌 페이지만 모아 쓴다 → 페이지 전체를 버리지 않음)
    # 페이지별로 블록에서 정확히 5열이 잡히면 xs 기록, 행은 (min행 y0, median pitch).
    # 블록마다 중앙값 합의 → 안정적. 희소블록 xs 는 옵션간격으로 외삽.
    # xs: 페이지별 5열 검출 누적. ys: 페이지별 '행 중심' 누적 → nrows 개로 군집.
    per = {blk["q0"]: {"colsets": [], "cols": [], "rowc": []} for blk in spec["objective"]}
    for img in _oriented_pages(pdf):
        blobs = oc.detect_blobs(oc.red_mask(img))
        if len(blobs) == 0:
            continue
        for blk in spec["objective"]:
            sub = blobs[(blobs[:, 0] > blk["x_lo"]) & (blobs[:, 0] < blk["x_hi"])
                        & (blobs[:, 1] > blk["y_lo"]) & (blobs[:, 1] < blk["y_hi"])]
            if len(sub) < 2:
                continue
            cols = sorted(oc._cluster(sub[:, 0], 30))
            per[blk["q0"]]["cols"].extend(cols)
            per[blk["q0"]]["rowc"].extend(oc._cluster(sub[:, 1], 40))  # 행 중심들
            xs = _pick5(cols)
            if len(cols) >= 5 and len(xs) == 5 and (xs[-1] - xs[0]) < 400:
                per[blk["q0"]]["colsets"].append(xs)

    good_pitches = [np.median(np.diff(x)) for p in per.values() for x in p["colsets"]]
    opt_pitch = float(np.median(good_pitches)) if good_pitches else 59.0

    def rows_to_n(centers, nrows):
        cl = sorted(oc._cluster(sorted(centers), 30))
        while len(cl) > nrows:                       # 과분할 → 최근접 병합
            i = int(np.argmin(np.diff(cl))); cl[i] = (cl[i] + cl[i + 1]) / 2; del cl[i + 1]
        if len(cl) < nrows and len(cl) >= 2:         # 부족 → min~max 등간격
            y0, y1 = cl[0], cl[-1]
            cl = [y0 + (y1 - y0) * k / (nrows - 1) for k in range(nrows)]
        return [round(float(c), 1) for c in cl]

    objective = []
    for blk in spec["objective"]:
        p = per[blk["q0"]]
        if not p["rowc"]:
            raise RuntimeError(f"블록 q{blk['q0']}: 유효 검출 없음. x/y 범위 확인.")
        if p["colsets"]:
            xs = [round(float(x), 1) for x in np.median(np.array(p["colsets"]), axis=0)]
            npg = len(p["colsets"])
        else:
            colc = sorted(oc._cluster(sorted(p["cols"]), 22))
            xs = [round(min(colc) + opt_pitch * k, 1) for k in range(5)]
            npg = 0
        ys = rows_to_n(p["rowc"], blk["nrows"])
        objective.append(dict(q0=blk["q0"], nrows=blk["nrows"], xs=xs, ys=ys, _pages=npg))
    return dict(name=spec.get("name", "calibrated"),
                subject=spec.get("subject", ""),
                description="Auto-calibrated (anchor-free, per-block median) by hp_calibrate.py",
                objective=objective)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--scan", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    spec = json.loads(a.spec.read_text(encoding="utf-8"))
    tmpl = calibrate(spec, a.scan)
    a.out.write_text(json.dumps(tmpl, ensure_ascii=False, indent=2), encoding="utf-8")
    for blk in tmpl["objective"]:
        ys = blk["ys"]
        print(f"  q{blk['q0']}~{blk['q0']+blk['nrows']-1}: xs={[round(x) for x in blk['xs']]} "
              f"ys={len(ys)}행 y0={round(ys[0])} y_last={round(ys[-1])} "
              f"간격{round((ys[-1]-ys[0])/(len(ys)-1),1) if len(ys)>1 else 0}")
    print("→", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
