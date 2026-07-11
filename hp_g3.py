# -*- coding: utf-8 -*-
"""고3 학평(시도교육청 전국연합) 답안지 객관식 판독 — 보정 + 판독.

핵심: 인쇄된 빨강 마킹칸(빈칸)과 검정 마킹(채운 칸)을 **함께** 블롭으로 잡아
매 페이지 격자를 안정적으로 세운다(마킹돼 빨강이 사라져도 위치가 잡힘).
보정은 여러 페이지의 행중심을 '투표'해 실제 행만 남긴다(스퓨리어스 자동 배제).
디지털 렌더(auto_orient 3300x2550 고정)라 앵커 보정 불필요.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

import cv2
import numpy as np
import fitz

import omr_core as oc
import hp_align

FILL_MIN = 55.0   # darkness 절대임계 상한 (실제 마킹 55~180)
FILL_FLOOR = 35.0  # 적응임계 하한 — 이보다 어두워야 마킹으로 인정


def bubble_blobs(img: np.ndarray) -> np.ndarray:
    """빨강(빈칸)+검정(마킹) 합친 마스크에서 마킹칸 블롭 (cx,cy).

    ⚠️ 빨강 마스크는 답란 인쇄(국어 빨강원 등)용 — 분홍(수학)·색배경(수험번호
    영역)에서는 인쇄 원을 못 잡는다. 그리드 보정에는 mark_blobs(마킹만)의
    다페이지 합집합을 쓸 것."""
    b, g, r = cv2.split(img.astype(int))
    red = ((r - b) > 30) & (r > 120)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = ((red | (gray < 95)).astype("uint8")) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mor = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    n, _, st, ct = cv2.connectedComponentsWithStats(mor, 8)
    out = [ct[i] for i in range(1, n)
           if 18 <= st[i][2] <= 62 and 18 <= st[i][3] <= 58 and st[i][4] > 180]
    return np.array(out)


def mark_blobs(img: np.ndarray) -> np.ndarray:
    """학생 마킹(속이 찬 검정 덩어리)만의 블롭 (cx,cy) — 인쇄색·배경색 무관.

    수험번호·과목코드·단답형 그리드 보정용: 전원이 마킹하므로 여러 페이지
    합집합이면 전 열·행 좌표가 복원된다(인쇄 원 검출이 안 되는 영역 대응)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dark = (gray < 110).astype("uint8") * 255
    n, _, st, ct = cv2.connectedComponentsWithStats(dark, 8)
    out = [ct[i] for i in range(1, n)
           if 16 <= st[i][2] <= 62 and 18 <= st[i][3] <= 60
           and st[i][4] > 250 and st[i][4] >= 0.5 * st[i][2] * st[i][3]]
    return np.array(out) if out else np.empty((0, 2))


def top_marks(gray: np.ndarray) -> list[float]:
    """상단 타이밍 마크(검은 네모)의 x중심들. 각 마크 = 그 아래 열의 정확한 x.
    (인쇄 클럭 트랙이라 마킹·드리프트와 무관하게 열 좌표를 정확히 준다)"""
    strip = gray[0:140, :]
    dark = (strip < 90).astype("uint8") * 255
    n, _, st, ct = cv2.connectedComponentsWithStats(dark, 8)
    xs = [ct[i][0] for i in range(1, n)
          if 10 <= st[i][2] <= 62 and 25 <= st[i][3] <= 135 and st[i][4] > 300]
    return sorted(xs)


def _pick5(cols: list[float]) -> list[float]:
    if len(cols) < 5:
        return cols
    return list(min((cols[i:i + 5] for i in range(len(cols) - 4)),
                    key=lambda w: w[-1] - w[0]))


def oriented_pages(pdf: Path, limit: int | None = None):
    doc = fitz.open(str(pdf))
    n = doc.page_count if limit is None else min(limit, doc.page_count)
    for i in range(n):
        pix = doc[i].get_pixmap(dpi=300)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR) if pix.n == 4 else cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        yield oc.auto_orient(img)
    doc.close()


PROF_Y = (260, 2420)   # x-프로파일 측정 세로 구간 (타이밍마크 제외, 카드 본문)


def x_profile(img: np.ndarray) -> np.ndarray:
    """카드 잉크(표선·글자·버블)의 x축 밀도 프로파일 — 격자 시프트 판별용.

    블롭 분할과 무관하게 카드의 전체 인쇄 구조를 쓰므로 흐린 인쇄에 강하다.
    잉크 임계는 배경(중앙값) 상대값 — 전체가 어두운 스캔에서도 동작
    (절대 210 고정이면 배경 205 짜리 어두운 스캔은 전부 잉크 판정돼 붕괴)."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    band = g[PROF_Y[0]:PROF_Y[1], :]
    thr = min(210.0, float(np.median(band)) - 45.0)
    ink = (band < thr).astype(np.float32)
    return ink.mean(axis=0)


def normalize_scan(img: np.ndarray) -> tuple[np.ndarray, bool]:
    """저대비 스캔 자동 보정 + 흑백 스캔 감지 → (이미지, is_gray).

    - 명암 범위가 좁으면(1~99 백분위 < 160) 선형 스트레치 — 정상 스캔은 무변경.
    - 채도 평균이 극히 낮으면 흑백 스캔: 컬러 드롭아웃(인쇄 원 소거)이 불가능해
      판독 신뢰도가 떨어지므로 호출측이 '컬러 재스캔' 플래그를 달아야 한다."""
    # 통계는 4×4 서브샘플로 충분(≈50만 픽셀) — 전체 계산 대비 ~16배 절약
    g = cv2.cvtColor(img[::4, ::4], cv2.COLOR_BGR2GRAY)
    lo, hi = np.percentile(g, 1), np.percentile(g, 99)
    # 어두운 스캔(백색점<235) 또는 저대비 스캔은 선형 스트레치로 정규화 —
    # 정상 스캔(백색점 ~255)은 무변경. 인쇄색 대비가 복원돼야 컬러 드롭아웃이
    # 안정적으로 동작한다(실측: -50 밝기에서 분홍 인쇄가 임계 근처까지 상승).
    # hi-lo<30 인 균일 페이지(빈 페이지 등)는 스트레치가 노이즈만 증폭 → 제외
    if (hi < 235 or hi - lo < 160) and hi - lo >= 30:
        img = np.clip((img.astype(np.float32) - lo) * (255.0 / max(1.0, hi - lo)),
                      0, 255).astype(np.uint8)
    hsv = cv2.cvtColor(img[::4, ::4], cv2.COLOR_BGR2HSV)
    is_gray = float(hsv[..., 1].mean()) < 8.0
    return img, is_gray


def _shift_corr(a: np.ndarray, b: np.ndarray, s: int) -> float:
    """b 를 s 만큼 민 것과 a 의 상관 (a[x] ≈ b[x-s] 가정 검증)."""
    n = min(len(a), len(b))
    aa = a[max(0, s):n + min(0, s)]
    bb = b[max(0, -s):n - max(0, s)]
    m = min(len(aa), len(bb))
    return float(np.corrcoef(aa[:m], bb[:m])[0, 1])


def learn_xprofile(profs: list[np.ndarray], pitch: float) -> np.ndarray:
    """페이지 프로파일들의 합의 — 페이지마다 격자 잠김(±k·pitch)이 섞여 있어도
    앵커 프로파일에 최적 시프트로 정합한 뒤 중앙값을 취한다."""
    anchor = profs[0]
    aligned = []
    for p in profs:
        k = max((-2, -1, 0, 1, 2),
                key=lambda k: _shift_corr(p, anchor, int(round(k * pitch))))
        aligned.append(np.roll(p, -int(round(k * pitch))))
    return np.median(np.array(aligned), axis=0)


def rectify_verified(img: np.ndarray, tmpl: dict) -> tuple[np.ndarray, dict]:
    """마크 기반 rectify + 잉크 x-프로파일 상관으로 격자주기(≈60px) 모호성 해소.

    타이밍마크 pitch == 선지 열 pitch 라, 가장자리 마크가 몇 개 빠진 페이지는
    마크 매칭이 ±1 pitch 밀려 잠길 수 있다(실측: 국어 3-1 p2·p3, 한국사 다수).
    카드의 표선·라벨·버블 구조는 60px 주기가 아니므로, 기준 프로파일과의
    상관이 최대인 시프트 k∈[-2..2] 로 확정. 유일하지 않으면 AlignError.
    저대비 스캔은 자동 보정하고, 흑백 스캔은 info['gray_scan']=True 로 표시."""
    img, is_gray = normalize_scan(img)
    out, info = hp_align.rectify(img, tmpl["marks"])
    info["gray_scan"] = is_gray
    pitch = float(np.median(np.diff(tmpl["marks"]["xs"])))
    refp = np.asarray(tmpl["xprofile"], dtype=np.float32)
    prof = x_profile(out)
    # 페이지가 k·pitch 만큼 밀렸다면 prof[x] ≈ refp[x - k·pitch]
    scores = {k: _shift_corr(prof, refp, int(round(k * pitch)))
              for k in (-2, -1, 0, 1, 2)}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (k1, s1), (_, s2) = ranked[0], ranked[1]
    # 실질 판별은 1·2위 마진 — 저품질 스캔(A4 재스캔·저해상)은 corr 절대값이
    # 0.4대까지 내려가지만 마진은 명확(실측 0.43 vs 0.23). 절대 하한은
    # 카드가 아예 아닌 페이지 방어용.
    ok = (s1 >= 0.55 and (s1 - s2) >= 0.05) or (s1 >= 0.35 and (s1 - s2) >= 0.12)
    resolved_by = "profile"
    if not ok:
        # 2차 판별: 상위 후보 k 각각으로 수험번호 학교코드(19718 고정)를 실제로
        # 읽어본다 — 정확히 하나의 후보에서만 읽히면 그 후보 채택(읽기-검증).
        # 저품질 스캔에서 프로파일 상관이 뭉개져도 마킹 자체는 뚜렷한 경우가
        # 많아(실사용 corr 0.41 vs 0.30 마진 미달 사례) 경계 페이지를 회복한다.
        k1 = _id_readback_pick(out, tmpl, pitch,
                               [k for k, _ in ranked[:3] if scores[k] >= 0.30])
        if k1 is None:
            pretty = {k: round(v, 3) for k, v in scores.items()}
            raise hp_align.AlignError(f"격자 검증 모호 (상관 {pretty})")
        resolved_by = "id_readback"
        s1 = scores[k1]
    if k1 != 0:
        M = np.array([[1, 0, -k1 * pitch], [0, 1, 0]], dtype=float)
        out = cv2.warpAffine(out, M, (out.shape[1], out.shape[0]),
                             borderValue=(255, 255, 255))
    info = dict(info, grid_k=k1, grid_corr=round(s1, 3), grid_by=resolved_by)
    return out, info


SCHOOL_CODE = 19718   # 산남고 — 수험번호 읽기-검증 기준


def _id_readback_pick(img: np.ndarray, tmpl: dict, pitch: float,
                      cands: list[int]) -> int | None:
    """후보 시프트들로 수험번호를 읽어 학교코드가 일치하는 '유일한' 후보를 반환."""
    idg = next((g for g in tmpl.get("grids", []) if g["name"] == "id"), None)
    if idg is None or not cands:
        return None
    import hp_id
    gray = pencil_gray(img)
    hits = []
    for k in cands:
        xs = [x + k * pitch for x in idg["xs"]]
        rid = hp_id.read_id(gray, dict(idg, xs=xs))
        if rid.get("school") == SCHOOL_CODE:
            hits.append(k)
    return hits[0] if len(hits) == 1 else None


def rectified_pages(pdf: Path, ref: dict, limit: int | None = None,
                    tmpl: dict | None = None):
    """(페이지번호, 정렬이미지) — 정렬 실패 페이지는 (번호, None) 으로 표시.
    tmpl(objective 좌표 포함)을 주면 격자주기 모호성 검증까지 수행(권장)."""
    doc = fitz.open(str(pdf))
    n = doc.page_count if limit is None else min(limit, doc.page_count)
    for i in range(n):
        pix = doc[i].get_pixmap(dpi=300)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR) if pix.n == 4 else cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        try:
            if tmpl is not None:
                out, _ = rectify_verified(img, tmpl)
            else:
                out, _ = hp_align.rectify(img, ref)
            yield i, out
        except hp_align.AlignError:
            yield i, None
    doc.close()


def calibrate(spec: dict, pdf: Path, ref: dict | None = None,
              prior: dict | None = None) -> dict:
    # 열(xs): 상단 타이밍 마크에서 블록 x범위 안의 5개 = 정확한 열좌표(드리프트무관).
    # 행(ys): 빨강+검정 블롭 행중심 투표(마킹돼도 위치 잡힘).
    per = {blk["q0"]: {"xs": [], "rowc": [], "cols": []} for blk in spec["objective"]}
    npages = 0
    pdfs = list(pdf) if isinstance(pdf, (list, tuple)) else [pdf]
    if ref:
        pages = (im for p in pdfs
                 for _, im in rectified_pages(p, ref, tmpl=prior) if im is not None)
    else:
        pages = (im for p in pdfs for im in oriented_pages(p))
    for img in pages:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blobs = bubble_blobs(img)
        if len(blobs) == 0:
            continue
        npages += 1
        for blk in spec["objective"]:
            sub = blobs[(blobs[:, 0] > blk["x_lo"]) & (blobs[:, 0] < blk["x_hi"])
                        & (blobs[:, 1] > blk["y_lo"]) & (blobs[:, 1] < blk["y_hi"])]
            if len(sub) < 2:
                continue
            cols = sorted(oc._cluster(sub[:, 0], 28))
            per[blk["q0"]]["cols"].extend(cols)
            per[blk["q0"]]["rowc"].extend(oc._cluster(sub[:, 1], 30))
            # 열(xs): 실제 버블중심(블롭)에서 5열. 마킹돼도 빨강+검정이라 위치 잡힘.
            # 마크는 열개수 힌트로만: 블록 x범위 내 5마크가 있으면 그 근처 5블롭열 채택.
            xs = _pick5(cols)
            if len(cols) >= 5 and (xs[-1] - xs[0]) < 360:
                per[blk["q0"]]["xs"].append(xs)
    good = [np.median(np.diff(x)) for p in per.values() for x in p["xs"]]
    opt_pitch = float(np.median(good)) if good else 59.0

    # 한 카드의 모든 블록은 같은 행 y(위 정렬)를 공유한다. 블록 rowc 를 전부 모아
    # 페이지 다수에서 반복 등장한 행만 남겨 (y0, pitch) 를 하나로 확정 → 결정적.
    def voted_rows(rowc):
        vals = sorted(rowc)
        groups, cur = [], [vals[0]]
        for v in vals[1:]:
            if v - cur[-1] <= 22:
                cur.append(v)
            else:
                groups.append(cur)
                cur = [v]
        groups.append(cur)
        thr = max(2, npages // 3)                      # 페이지 1/3 이상 등장 = 실제 행
        cen = sorted(float(np.mean(g)) for g in groups if len(g) >= thr)
        # 상단 ①②③④⑤ 라벨행: q1 위 ~60px(정상 pitch ~100 보다 확연히 촘촘)일 때만 제거
        while (len(cen) >= 3 and (cen[1] - cen[0]) < 78
               and (cen[2] - cen[1]) - (cen[1] - cen[0]) > 22):
            cen = cen[1:]
        return cen

    # 전 블록 공통 pitch(간격은 카드 전체 동일) — 블록별 표본이 적어도 안정적.
    gcen = voted_rows([y for p in per.values() for y in p["rowc"]]) or [400.0]
    gd = [b - a for a, b in zip(gcen, gcen[1:]) if 60 <= b - a <= 160]
    gpitch = float(np.median(gd)) if gd else 100.0

    # 1차: 블록별 xs·y0·pitch 산출
    prelim = []
    for blk in spec["objective"]:
        p = per[blk["q0"]]
        if p["xs"]:
            xs = [round(float(x), 1) for x in np.median(np.array(p["xs"]), axis=0)]
            npg = len(p["xs"])
        else:
            colc = sorted(oc._cluster(sorted(p["cols"]), 22)) if p["cols"] else []
            if len(colc) < 1:
                raise RuntimeError(f"블록 q{blk['q0']}: 열 검출 실패. x범위/마크 확인.")
            xs = [round(colc[0] + opt_pitch * kk, 1) for kk in range(5)]
            npg = 0
        bc = voted_rows(p["rowc"]) if p["rowc"] else []
        pitch = gpitch
        if len(bc) >= 3:
            bd = [b - a for a, b in zip(bc, bc[1:]) if 60 <= b - a <= 160]
            if bd:
                pitch = float(np.median(bd))
        # y0 는 '등간격 격자 적합'으로 선택: 후보 시작점마다 nrows 격자를 깔아
        # 투표 행중심과 20px 내 매칭되는 행 수가 최대인 시작점을 채택.
        # (라벨행 '문번/답/란' 에서 시작하면 이후 행이 전부 어긋나 매칭 1개,
        #  실제 1번 행에서 시작하면 전부 매칭 — 임계값 칼끝 휴리스틱 불필요.
        #  상단 행이 투표에서 빠진 경우를 위해 bc[i]-pitch*k 도 후보에 포함하되
        #  spec 의 y_lo 아래로 내려가는 시작점은 제외.)
        if not bc:
            raise RuntimeError(f"블록 q{blk['q0']}: 행 검출 실패. y범위 확인.")
        cands = sorted({c - pitch * k for c in bc for k in range(3)
                        if c - pitch * k > max(150.0, blk["y_lo"] - 30)})
        rowc_all = p["rowc"]
        def fit(y0):
            ys_g = [y0 + pitch * k for k in range(blk["nrows"])
                    if y0 + pitch * k <= blk["y_hi"] + 30]
            errs = [min(abs(y - c) for c in bc) for y in ys_g]
            # 동률 판별: 투표 미달 행도 원시 행중심 지지(있으면 실제 행)로 가른다
            raw = sum(min(sum(1 for r in rowc_all if abs(r - y) <= 20), 3) for y in ys_g)
            return (sum(1 for e in errs if e <= 20), raw, -sum(errs))
        y0 = max(cands, key=fit) if cands else bc[0]
        prelim.append([blk, xs, y0, pitch, npg, p["rowc"]])

    objective = []
    for blk, xs, y0, pitch, npg, _ in prelim:
        ys = [round(y0 + pitch * k, 1) for k in range(blk["nrows"])]
        objective.append(dict(q0=blk["q0"], nrows=blk["nrows"], xs=xs, ys=ys, _pages=npg))
    out = dict(name=spec.get("name", "calibrated"), subject=spec.get("subject", ""),
               description="Auto-calibrated (red+dark, cross-page vote) by hp_g3.py",
               objective=objective)
    if ref:
        out["marks"] = ref
        out["description"] += " on rectified pages"
    return out


def _nearest(cands: list[float], v: float, tol: float) -> float:
    if not cands:
        return v
    c = min(cands, key=lambda x: abs(x - v))
    return c if abs(c - v) <= tol else v


def pencil_gray(img: np.ndarray) -> np.ndarray:
    """darkness 측정용 회색조 = 픽셀 최대채널(HSV 의 V) — 컬러 드롭아웃.

    밝은 컬러 인쇄(빈 마킹칸 원: V≈253)는 백지에 수렴하고, 필기(사인펜·연필,
    스캔에서 색이 끼어도 V≈170 이하로 어두움)만 신호로 남는다. 실측(7월 학평
    국어 카드): 마킹 V중앙 172 / 인쇄원 253 / 바탕 255. 유채색 픽셀 분류
    방식은 어두운 적갈색 마킹까지 지워 폐기.
    (cv2.max 2회가 numpy max(axis=2) 대비 ~5배 빠름 — 페이지당 최대 단일 비용)"""
    b, g, r = cv2.split(img)
    return cv2.max(cv2.max(b, g), r)


def adaptive_fill_min(tops: list[float]) -> float:
    """학생별 적응 임계 — 확실히 마킹된 셀들의 top darkness 중앙값의 45%
    (하한 FILL_FLOOR, 상한 FILL_MIN). 표본 부족 시 FILL_MIN."""
    strong = [t for t in tops if t >= 40.0]
    level = float(np.median(strong)) if len(strong) >= 5 else FILL_MIN / 0.45
    return min(FILL_MIN, max(FILL_FLOOR, 0.45 * level))


def read_objective(img: np.ndarray, tmpl: dict,
                   gray: np.ndarray | None = None) -> dict[int, int]:
    """{문항: 답(0=미마킹, -1=중복, 1~5)}.

    전제: img 는 rectify(타이밍마크 아핀 정렬)를 거쳐 템플릿 좌표계와 일치.
    - darkness 는 빨강 인쇄를 백지화한 회색조에서 측정(인쇄원 오검 차단).
      gray 에 pencil_gray(img) 를 미리 계산해 넘기면 재계산을 생략한다.
    - 임계는 학생별 적응: 그 페이지 마킹 농도 중앙값의 45% (하한 FILL_FLOOR,
      상한 FILL_MIN) — 흐린 연필 학생도, 진한 사인펜 학생도 같은 규칙.
    - 잔여 미세오차만 (y±12) 근방 최대 darkness 로 흡수. 블록 세로 스냅은
      정렬 후 불필요해졌고, 부실 페이지에서 오히려 해가 돼 제거.
    """
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if gray is None:
        gray = pencil_gray(img)
    # 1차: 전 문항 darkness 행렬 수집
    rows = []  # (q, [dk×5])
    for blk in tmpl["objective"]:
        xs, ys = blk["xs"], blk["ys"]
        for i, y in enumerate(ys):
            dk = [max(oc._darkness(gray, x, y + ddy, 14) for ddy in (-12, 0, 12)) for x in xs]
            rows.append((blk["q0"] + i, dk))
    fill_min = adaptive_fill_min([max(dk) for _, dk in rows])
    out = {}
    for q, dk in rows:
        out[q] = decide_cell(dk, fill_min)
    return out


def decide_cell(dk: list[float], fill_min: float) -> int:
    """한 문항(선지 darkness 목록) → 답(1~n)/미마킹(0)/중복(-1).

    절대 임계에 두 가지 상대 규칙 보강:
    - 지운 자국: 임계 이상이 2개여도 최고가 2위의 2배 이상이면 최고 채택
      (지우개 잔흔 40~60 vs 실마킹 100+ — 실측 크롭 감사로 검증)
    - 연한 점 마킹: 전부 임계 미달이어도 최고가 뚜렷한 단독 우세
      (≥18 & 2위의 4배)면 채택 — 점만 콕 찍는 학생 구제
    애매하면 종전대로 -1/0 → 검토 목록."""
    order = sorted(range(len(dk)), key=lambda j: -dk[j])
    top, second = dk[order[0]], dk[order[1]]
    marked = [j for j in range(len(dk)) if dk[j] >= fill_min]
    if len(marked) == 1:
        return marked[0] + 1
    if len(marked) > 1:
        return order[0] + 1 if top >= 2.0 * second else -1
    if top >= 18.0 and top >= 4.0 * max(second, 1.0):
        return order[0] + 1
    return 0


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
              f"ys={len(ys)}행 [{round(ys[0])}..{round(ys[-1])}]")
    print("→", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
