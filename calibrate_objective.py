# -*- coding: utf-8 -*-
"""
객관식 OMR 답안지 좌표 자동 보정기 (score_lens / omr_scorer)

새 답안지 양식(예: 고1·2 전국연합 학력평가)의 좌표 템플릿을, 실제 스캔 몇 장에서
자동으로 뽑아 준다. 손으로 픽셀을 재던 작업을 명령 한 줄로 대체한다.

동작
  1) 각 스캔 페이지를 3300x2550(REF)로 정규화하고 빨강 마스크 → 마킹칸 블롭 검출.
  2) 페이지별 상단 타이밍마크 앵커를 역변환해 블롭 좌표를 REF 프레임으로 정규화.
     (english_core 등이 읽을 때 forward 앵커를 적용하므로, 저장 좌표는 REF 프레임.)
  3) spec 의 블록별 (x 범위, 행수, y 범위) 안에서 5개 열 x / 행 y0·pitch 를 추정.
  4) 여러 페이지 중앙값으로 합의 → 단일 안정 템플릿. (단일 페이지 이상치 자동 배제.)

입력 spec(JSON)
  {
    "name": "science_g12",
    "subject": "통합과학",
    "objective": [
      {"q0": 1,  "nrows": 20, "x_lo": 1240, "x_hi": 1560, "y_lo": 300, "y_hi": 2350},
      {"q0": 21, "nrows": 5,  "x_lo": 1710, "x_hi": 2030, "y_lo": 300, "y_hi": 900}
    ]
  }
  * x_lo/x_hi 는 그 블록 5개 열(①~⑤)을 넉넉히 감싸는 범위. y_lo/y_hi 는 그 블록 행 범위.
  * 대략 눈대중으로 넣어도 된다 — 클러스터링이 실제 칸 중심을 잡는다.

사용
  python calibrate_objective.py --spec specs/science_g12.json \
      --scan "D:/scan/통합과학.pdf" --out templates/science_g12.json
  python calibrate_objective.py --spec specs/english_g12.json \
      --scan work/20260604165440_english --out templates/english_g12.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

import omr_core as oc


def _inverse_anchor(gray: np.ndarray, ref=oc.REF_TOP_ANCHOR):
    """이 페이지의 (page_x, page_y) → REF 프레임 (x, y) 역변환 함수."""
    a = oc.detect_top_anchor(gray)
    if a is None:
        return lambda x, y: (x, y)
    xl, xr, yb = a
    rxl, rxr, ryb = ref
    sx = (xr - xl) / (rxr - rxl) if (rxr - rxl) else 1.0
    dy = yb - ryb
    return lambda x, y: (rxl + (x - xl) / sx, y - dy)


def _blobs_ref(path: str) -> np.ndarray:
    """한 페이지의 마킹칸 블롭을 REF 프레임 좌표로 반환."""
    img = oc.load_page(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    inv = _inverse_anchor(gray)
    blobs = oc.detect_blobs(oc.red_mask(img))
    if len(blobs) == 0:
        return blobs
    return np.array([inv(x, y) for x, y in blobs])


def _grid_from_page(blobs: np.ndarray, spec: dict) -> dict | None:
    """한 페이지 블롭에서 블록별 (xs[5], y0, pitch) 추정. 실패 시 None."""
    if len(blobs) == 0:
        return None
    out = {}
    for blk in spec["objective"]:
        sub = blobs[(blobs[:, 0] > blk["x_lo"]) & (blobs[:, 0] < blk["x_hi"])
                    & (blobs[:, 1] > blk["y_lo"]) & (blobs[:, 1] < blk["y_hi"])]
        if len(sub) < 5:
            return None
        cols = sorted(oc._cluster(sub[:, 0], 30))
        if len(cols) < 5:
            return None
        # 가장 촘촘한 연속 5열(①~⑤). 잡음 열이 끼면 폭이 가장 작은 5열 창을 고른다.
        best = min((cols[i:i + 5] for i in range(len(cols) - 4)),
                   key=lambda w: w[-1] - w[0])
        rows = sorted(oc._cluster(sub[:, 1], 40))
        if len(rows) < 2:
            return None
        out[blk["q0"]] = dict(
            xs=[float(x) for x in best],
            y0=float(min(rows)),
            pitch=float(np.median(np.diff(rows))),
            nrows=blk["nrows"],
        )
    return out


def _iter_pages(scan: Path, work: Path) -> list[Path]:
    if scan.is_dir():
        return sorted(scan.glob("*.png"))
    if scan.suffix.lower() == ".pdf":
        import fitz
        work.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(scan)
        mat = fitz.Matrix(300 / 72, 300 / 72)
        paths = []
        for i, page in enumerate(doc, 1):
            p = work / f"{scan.stem}_p{i:03d}.png"
            page.get_pixmap(matrix=mat, alpha=False).save(p)
            paths.append(p)
        doc.close()
        return paths
    raise SystemExit(f"스캔 경로는 PNG 폴더 또는 PDF 여야 합니다: {scan}")


def calibrate(spec: dict, pages: list[Path], sample: int | None = 15) -> dict:
    paths = list(pages)
    if sample and len(paths) > sample:
        step = len(paths) / sample
        paths = [paths[int(i * step)] for i in range(sample)]

    grids = []
    for p in paths:
        try:
            g = _grid_from_page(_blobs_ref(str(p)), spec)
        except Exception:
            g = None
        if g:
            grids.append(g)
    if not grids:
        raise RuntimeError("보정 실패: 유효 페이지 없음. spec 의 x/y 범위를 확인하세요.")

    objective = []
    for blk in spec["objective"]:
        q0 = blk["q0"]
        present = [g[q0] for g in grids if q0 in g]
        xs = [float(np.median([g["xs"][i] for g in present])) for i in range(5)]
        y0 = float(np.median([g["y0"] for g in present]))
        pitch = float(np.median([g["pitch"] for g in present]))
        objective.append(dict(q0=q0, nrows=blk["nrows"],
                              xs=[round(x, 1) for x in xs],
                              y0=round(y0, 1), pitch=round(pitch, 1)))

    tmpl = dict(
        name=spec.get("name", "calibrated"),
        description=f"Auto-calibrated from {len(grids)} pages by calibrate_objective.py",
        objective=objective,
    )
    if "subject" in spec:
        tmpl["subject"] = spec["subject"]
    tmpl["_n_pages"] = len(grids)
    return tmpl


def main() -> int:
    ap = argparse.ArgumentParser(description="객관식 OMR 좌표 자동 보정 → 템플릿 JSON")
    ap.add_argument("--spec", type=Path, required=True, help="구조 spec JSON")
    ap.add_argument("--scan", type=Path, required=True, help="스캔 PNG 폴더 또는 PDF")
    ap.add_argument("--out", type=Path, required=True, help="출력 템플릿 JSON")
    ap.add_argument("--sample", type=int, default=15, help="보정에 쓸 페이지 표본 수")
    args = ap.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    pages = _iter_pages(args.scan, Path("work") / f"_calib_{args.spec.stem}")
    if not pages:
        print(f"[오류] 스캔에서 페이지를 찾지 못함: {args.scan}")
        return 1
    tmpl = calibrate(spec, pages, sample=args.sample)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(tmpl, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"보정 완료: {tmpl['_n_pages']}페이지 합의 → {args.out}")
    for blk in tmpl["objective"]:
        print(f"  문항 {blk['q0']}~{blk['q0'] + blk['nrows'] - 1}: "
              f"xs={blk['xs']} y0={blk['y0']} pitch={blk['pitch']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
