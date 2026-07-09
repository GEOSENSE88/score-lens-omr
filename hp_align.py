# -*- coding: utf-8 -*-
"""고3 학평 카드 페이지 정규화(rectify) — 상단 타이밍마크 기반.

실물 스캔의 페이지별 회전(90/180/270°)·기울기(skew)·평행이동·미세 배율을
상단 타이밍마크(검은 클럭트랙)를 기준으로 한 번의 아핀 변환으로 흡수한다.
정렬 후에는 보정 템플릿의 고정 좌표를 그대로 쓸 수 있다.

- 방향: 4방향 각각 상단 띠에서 '등간격 일직선 마크 사슬' 점수로 판별
  (기존 auto_orient 의 단순 개수 방식은 밀림·기울기에 취약했음).
- 정렬: 마크 중심들을 직선 적합(deskew 각) → 기준 마크열과 1D 매칭(배율·이동)
  → 합성 아핀 1회 warp.
- QC: 매칭 마크 수·적합 잔차·각도·배율이 한도를 벗어나면 AlignError 로 실패를
  드러낸다(조용한 오판독 금지). 호출측은 해당 페이지를 검토 목록으로 분리.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

REF_W, REF_H = 3300, 2550
BAND = 340          # 마크 탐색 상단 띠 (skew ±3° 에서도 마크행이 안에 들어옴)
MAX_ANGLE = 3.0     # 허용 skew (도)
MAX_SCALE_DEV = 0.12   # A4 세로 스캔(≈1.06) 등 실측 배율 편차 허용


class AlignError(RuntimeError):
    """페이지 정렬 실패 — 이 페이지는 판독하지 말고 검토 목록으로."""


def band_marks(gray: np.ndarray, band: int = BAND) -> np.ndarray:
    """상단 띠의 타이밍마크 후보 (cx, cy).

    타이밍마크 = 속이 꽉 찬 검은 사각형. 인쇄 글자행도 '등간격 일직선'이라
    사슬 점수로는 못 거르므로 충전율(area/bbox)≥0.7 로 여기서 배제한다."""
    strip = gray[0:band, :]
    dark = (strip < 100).astype("uint8") * 255
    n, _, st, ct = cv2.connectedComponentsWithStats(dark, 8)
    out = [ct[i] for i in range(1, n)
           if 8 <= st[i][2] <= 80 and 20 <= st[i][3] <= 150 and st[i][4] > 250
           and st[i][4] >= 0.7 * st[i][2] * st[i][3]]
    return np.array(out) if out else np.empty((0, 2))


def _fit_line(marks: np.ndarray) -> tuple[np.ndarray, float, float]:
    """마크들에서 일직선 행만 남긴다 → (inliers, slope, resid_mad)."""
    pts = marks[np.argsort(marks[:, 0])]
    # 반복 강건 적합: y 중앙값 근방 → polyfit → 잔차 큰 점 제거
    keep = pts[np.abs(pts[:, 1] - np.median(pts[:, 1])) < 70]
    for _ in range(3):
        if len(keep) < 5:
            return keep, 0.0, 1e9
        m, b = np.polyfit(keep[:, 0], keep[:, 1], 1)
        r = keep[:, 1] - (m * keep[:, 0] + b)
        mad = float(np.median(np.abs(r - np.median(r)))) + 1e-6
        nk = keep[np.abs(r) < max(6.0, 4 * mad)]
        if len(nk) == len(keep):
            break
        keep = nk
    m, b = np.polyfit(keep[:, 0], keep[:, 1], 1)
    r = keep[:, 1] - (m * keep[:, 0] + b)
    return keep, float(m), float(np.median(np.abs(r)))


def _chain_score(inliers: np.ndarray) -> int:
    """등간격 사슬 점수: 이웃 간격이 중앙값 pitch 의 0.5~1.5배인 마크 수."""
    if len(inliers) < 5:
        return len(inliers)
    xs = np.sort(inliers[:, 0])
    d = np.diff(xs)
    p = float(np.median(d))
    if p <= 4:
        return 0
    return int(np.sum((d > 0.5 * p) & (d < 1.5 * p))) + 1


def coarse_orient(bgr: np.ndarray):
    """4방향 중 타이밍마크 사슬이 가장 뚜렷한 방향으로 회전+REF 크기 통일.
    반환: (img, inlier_marks, slope). 사슬이 어디에도 없으면 AlignError.

    정방향(무회전)에서 강한 사슬(≥25)이 나오면 나머지 회전 검사를 생략 —
    스캔 대부분이 정방향이라 방향판별 비용(페이지 판독의 ~38%)을 크게 줄인다.
    (오방향 페이지의 상단엔 클럭트랙이 없어 25개 등간격 사슬이 나올 수 없음)"""
    best = None
    for rot in (None, cv2.ROTATE_180, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
        img = bgr if rot is None else cv2.rotate(bgr, rot)
        h, w = img.shape[:2]
        if w != REF_W:
            # 종횡비 보존(폭 기준) — A4 세로 스캔 등 비율이 다른 입력을 비등방
            # 왜곡 없이 통일한다. 남는 배율 차이는 아핀의 등방 s 가 흡수.
            img = cv2.resize(img, (REF_W, round(h * REF_W / w)), interpolation=cv2.INTER_AREA)
        marks = band_marks(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        if len(marks) < 5:
            continue
        inl, slope, resid = _fit_line(marks)
        score = _chain_score(inl) if resid < 8 else 0
        if best is None or score > best[0]:
            best = (score, img, inl, slope)
        if rot is None and score >= 25:
            break                                      # 정방향 확정 — 조기 종료
    if best is None or best[0] < 12:
        raise AlignError(f"타이밍마크 사슬 미검출 (최대 사슬 {0 if best is None else best[0]}개)")
    return best[1], best[2], best[3]


def _deskewed_marks(bgr):
    """페이지의 마크를 자체 deskew 좌표로 → (xs_sorted, y_median)."""
    _, inl, slope = coarse_orient(bgr)
    th = math.atan(slope)
    c, s = math.cos(th), math.sin(th)
    cx, cy = REF_W / 2, REF_H / 2
    xr = cx + (inl[:, 0] - cx) * c + (inl[:, 1] - cy) * s
    yr = cy - (inl[:, 0] - cx) * s + (inl[:, 1] - cy) * c
    return np.sort(xr), float(np.median(yr))


def learn_ref_marks(pages) -> dict:
    """여러 페이지 합의로 기준 마크열 산출 → {'xs': [...], 'y': float}.

    페이지마다 평행이동·미세 배율이 다르므로, 마크가 가장 많이 잡힌 페이지를
    앵커로 삼아 각 페이지를 앵커 좌표계로 매칭(_match_to_ref)한 뒤 합의한다.
    앵커 좌표계에서 페이지 절반 이상 재현된 마크 x 만 남긴다."""
    per_page = []
    for bgr in pages:
        try:
            per_page.append(_deskewed_marks(bgr))
        except AlignError:
            continue
    if len(per_page) < 3:
        raise AlignError("기준 마크 학습 실패: 유효 페이지 부족")
    anchor = max(per_page, key=lambda p: len(p[0]))
    all_x, npg = [], 0
    for xs, ym in per_page:
        m = _match_to_ref(xs, anchor[0])
        if m is None or m[3] > 3.0:
            continue
        s, b = m[0], m[1]
        all_x.extend(s * xs + b)
        npg += 1
    if npg < 3:
        raise AlignError("기준 마크 학습 실패: 앵커 매칭 페이지 부족")
    xs_sorted = np.sort(np.array(all_x))
    groups, cur = [], [xs_sorted[0]]
    for v in xs_sorted[1:]:
        if v - cur[-1] <= 12:
            cur.append(v)
        else:
            groups.append(cur)
            cur = [v]
    groups.append(cur)
    thr = max(2, npg // 2)
    xs = sorted(float(np.median(g)) for g in groups if len(g) >= thr)
    if len(xs) < 12:
        raise AlignError(f"기준 마크 학습 실패: 합의 마크 {len(xs)}개")
    # 기준 좌표계 = 앵커 페이지 좌표계 (y 포함). 템플릿은 rectify 된 페이지
    # 위에서 재보정하므로 어떤 일관된 프레임이든 무방하다.
    return {"xs": [round(x, 1) for x in xs], "y": round(anchor[1], 1)}


def _match_to_ref(xr: np.ndarray, ref_xs: np.ndarray):
    """deskew 된 페이지 마크 x → 기준 마크 x 매칭. (s, b, 매칭수, 잔차중앙값)
    ref_x ≈ s*x + b."""
    ref_p = float(np.median(np.diff(ref_xs)))
    page_p = float(np.median(np.diff(np.sort(xr))))
    if page_p <= 4:
        return None
    s0 = ref_p / page_p
    # 이동량 후보: 모든 (ref - s0*x) 차이의 최빈 구간
    d = (ref_xs[None, :] - s0 * xr[:, None]).ravel()
    hist, edges = np.histogram(d, bins=np.arange(d.min() - 10, d.max() + 10, 8))
    b0 = float(edges[np.argmax(hist)] + 4)
    # 1:1 매칭(기준 마크당 입력 마크 1개 — 잔차 최소인 것) → 최소자승 재적합 2회.
    # 최근접만 쓰면 여러 입력이 같은 기준 마크에 몰려 nmatch 가 부풀고,
    # 불량 정렬이 QC 를 통과할 수 있다.
    s, b = s0, b0
    pairs = None
    for _ in range(2):
        proj = s * xr + b
        idx = np.abs(ref_xs[None, :] - proj[:, None]).argmin(axis=1)
        res = np.abs(ref_xs[idx] - proj)
        keep, seen = [], set()
        for j in np.argsort(res):                 # 잔차 작은 순으로 기준마크 선점
            if res[j] < 14 and idx[j] not in seen:
                seen.add(int(idx[j]))
                keep.append(j)
        if len(keep) < 8:
            return None
        keep = np.array(keep)
        pairs = (xr[keep], ref_xs[idx[keep]])
        s, b = np.polyfit(pairs[0], pairs[1], 1)
    resid = np.abs(pairs[1] - (s * pairs[0] + b))
    return (float(s), float(b), int(len(pairs[0])),
            float(np.median(resid)), float(np.percentile(resid, 90)))


def rectify(bgr: np.ndarray, ref: dict) -> tuple[np.ndarray, dict]:
    """페이지를 기준 좌표계로 정렬. 반환 (정렬 이미지, 진단 info).
    실패 시 AlignError — 호출측은 페이지를 검토 목록으로 보낼 것."""
    img, inl, slope = coarse_orient(bgr)
    th = math.atan(slope)
    c, s_ = math.cos(th), math.sin(th)
    cx, cy = REF_W / 2, REF_H / 2
    xr = cx + (inl[:, 0] - cx) * c + (inl[:, 1] - cy) * s_
    yr = cy - (inl[:, 0] - cx) * s_ + (inl[:, 1] - cy) * c

    ref_xs = np.asarray(ref["xs"], dtype=float)
    m = _match_to_ref(np.sort(xr), ref_xs)
    if m is None:
        raise AlignError("기준 마크열 매칭 실패")
    s, b, nmatch, resid, resid_p90 = m
    need = max(12, int(0.6 * len(ref_xs)))
    if nmatch < need:
        raise AlignError(f"매칭 마크 부족 ({nmatch}/{len(ref_xs)}, 필요 {need})")
    if resid > 3.0:
        raise AlignError(f"매칭 잔차 과대 (중앙값 {resid:.1f}px)")
    if resid_p90 > 6.0:
        raise AlignError(f"매칭 잔차 과대 (p90 {resid_p90:.1f}px)")
    if abs(math.degrees(th)) > MAX_ANGLE:
        raise AlignError(f"기울기 과대 ({math.degrees(th):.2f}°)")
    if abs(s - 1) > MAX_SCALE_DEV:
        raise AlignError(f"배율 이상 ({s:.3f})")

    dy = ref["y"] - s * float(np.median(yr))
    # 합성 아핀: deskew 회전(중심 기준) → 배율 s → 이동(b, dy)
    R = cv2.getRotationMatrix2D((cx, cy), math.degrees(th), 1.0)
    R3 = np.vstack([R, [0, 0, 1]])
    A3 = np.array([[s, 0, b], [0, s, dy], [0, 0, 1]], dtype=float)
    M = (A3 @ R3)[:2]
    out = cv2.warpAffine(img, M, (REF_W, REF_H),
                         flags=cv2.INTER_AREA, borderValue=(255, 255, 255))
    info = dict(angle=round(math.degrees(th), 3), scale=round(s, 4),
                dx=round(b, 1), dy=round(dy, 1), nmatch=nmatch,
                resid=round(resid, 2), resid_p90=round(resid_p90, 2))
    return out, info
