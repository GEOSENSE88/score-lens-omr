# -*- coding: utf-8 -*-
"""
OMR 코어 엔진 — 평가원 국어영역 표준 답안지(2027 6월 모평 양식)
score_lens / omr_scorer

핵심 아이디어
- 인쇄된 마킹칸(원+숫자)은 '빨강 계열'로 인쇄됨 → 색 마스크로 분리 → 연결성분으로 칸 검출
- 페이지마다 격자를 '자동검출'하므로 스캔별 미세 이동/배율을 흡수 (검증 완료)
- 채움 여부는 회색 이미지의 '어두움(darkness)'으로 판독 (마킹 117~174 vs 빈칸 40~48, 명확 분리)

검증 상태: 답안 1~45 판독 + 선택과목(화작/언매) 검출 = 실제 스캔으로 확인 완료.
"""
from __future__ import annotations
import numpy as np
import cv2


# ── 답안 블록 정의 (국어: 공통 1-34, 선택 35-45) ──────────────────────
# 각 블록의 (x 검출범위, 문항수, 시작문항) — 좌표는 페이지마다 자동검출됨
#   ⚠️ x_lo 는 각 블록 ①열 중심보다 충분히 왼쪽이어야 한다. ①열을 잘라내면
#      ②부터 5칸을 잡아 전 문항이 '한 칸 밀려' 판독된다(common2 21~34 버그 사례).
#      ①열 중심: common1≈1944, common2≈2364, select≈2872.
ANSWER_BLOCKS = [
    dict(name="common1", x_lo=1900, x_hi=2210, nrows=20, q0=1,  y_hi=2400),
    dict(name="common2", x_lo=2345, x_hi=2650, nrows=14, q0=21, y_hi=1800),
    dict(name="select",  x_lo=2850, x_hi=3260, nrows=11, q0=35, y_hi=1600),
]

# 선택과목 마킹칸 (좌측 블록, 페이지 기준 고정 좌표 — 3300x2550 기준)
# ① 화법과작문 / ② 언어와매체  (같은 x열, 세로 2칸)
SUBJECT_MARKS = {
    "화법과작문": (925, 1115),
    "언어와매체": (925, 1270),
}

REF_W, REF_H = 3300, 2550  # 보정 기준 해상도 (300 DPI, A4 가로)


# ── 상단 검정 타이밍 마크 기반 정렬(앵커) ─────────────────────────────
# OMR 카드 최상단의 검정 눈금(타이밍 마크)으로 스캔별 이동/가로배율을 보정한다.
# 기준 페이지(math_p1, 3300x2550)에서 측정한 앵커:
REF_TOP_ANCHOR = (81.0, 3202.0, 67.0)   # (가장 왼쪽 마크 x, 가장 오른쪽 마크 x, 마크 띠 y)


def detect_top_anchor(gray: np.ndarray):
    """상단 검정 타이밍 마크 → (x_left, x_right, y_band). 실패 시 None."""
    strip = gray[0:130, :]
    dark = (strip < 90).astype("uint8") * 255
    n, _, stats, cent = cv2.connectedComponentsWithStats(dark, 8)
    xs, ys = [], []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if 12 <= w <= 45 and 25 <= h <= 110 and a > 400:
            xs.append(cent[i][0]); ys.append(cent[i][1])
    if len(xs) < 10:
        return None
    return (min(xs), max(xs), float(np.median(ys)))


def make_anchor_transform(gray: np.ndarray, ref=REF_TOP_ANCHOR):
    """기준 좌표 (X,Y) → 이 페이지 좌표로 매핑하는 함수. 앵커 검출 실패 시 항등."""
    a = detect_top_anchor(gray)
    if a is None:
        return lambda x, y: (x, y)
    xl, xr, yb = a
    rxl, rxr, ryb = ref
    sx = (xr - xl) / (rxr - rxl) if (rxr - rxl) else 1.0
    dy = yb - ryb
    return lambda x, y: (xl + (x - rxl) * sx, y + dy)


def _to_ref(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    if (w, h) != (REF_W, REF_H):
        return cv2.resize(bgr, (REF_W, REF_H), interpolation=cv2.INTER_AREA)
    return bgr


def _top_mark_count(gray: np.ndarray) -> int:
    """상단 130px 띠에서 타이밍 마크(검정 눈금) 개수. 방향 판별용."""
    strip = gray[0:130, :]
    dark = (strip < 90).astype("uint8") * 255
    n, _, stats, _ = cv2.connectedComponentsWithStats(dark, 8)
    c = 0
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if 12 <= w <= 45 and 25 <= h <= 110 and a > 400:
            c += 1
    return c


def auto_orient(bgr: np.ndarray) -> np.ndarray:
    """스캔이 뒤집히거나(180°) 옆으로 돌아가도(90/270°) 상단 타이밍마크가
    위로 오도록 바로잡는다. 정상 스캔은 그대로 통과(오버헤드 없음)."""
    ref0 = _to_ref(bgr)
    if _top_mark_count(cv2.cvtColor(ref0, cv2.COLOR_BGR2GRAY)) >= 10:
        return ref0                                    # 정상 — 빠른 경로
    best = (_top_mark_count(cv2.cvtColor(ref0, cv2.COLOR_BGR2GRAY)), ref0)
    for rot in (cv2.ROTATE_180, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
        ref = _to_ref(cv2.rotate(bgr, rot))
        c = _top_mark_count(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY))
        if c >= 10:
            return ref                                 # 유효 방향 발견
        if c > best[0]:
            best = (c, ref)
    return best[1]                                     # 못 찾으면 마크 최다 방향


def load_page(path) -> np.ndarray:
    # ndarray(BGR) 를 직접 받으면 디스크를 거치지 않는다 — auto_orient 만 적용.
    # (auto_orient 는 정상 방향이면 즉시 반환하는 빠른 경로라 중복 호출 부담이 작다)
    if isinstance(path, np.ndarray):
        return auto_orient(path)
    # cv2.imread 는 Windows 에서 한글 경로/파일명을 못 읽는다 → fromfile+imdecode 로 우회
    try:
        img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    except OSError:
        img = None
    if img is None:
        raise FileNotFoundError(path)
    return auto_orient(img)


def pdf_page_count(pdf_path) -> int:
    import fitz
    with fitz.open(str(pdf_path)) as doc:
        return doc.page_count


def iter_pdf_pages(pdf_path, dpi: int = 300, pages=None):
    """PDF 페이지를 PNG 파일을 거치지 않고 BGR ndarray 로 바로 렌더.

    (page_index, img) 를 순서대로 yield 한다 — 한 장씩 스트리밍이므로
    수백 페이지 배치에서도 메모리는 페이지 1장분만 쓴다.
    pages 를 주면 해당 인덱스(0-base)만 렌더한다(격자 보정 샘플용).
    """
    import fitz
    doc = fitz.open(str(pdf_path))
    try:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        idxs = range(doc.page_count) if pages is None else pages
        for i in idxs:
            pix = doc[i].get_pixmap(matrix=mat, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8)
            arr = arr.reshape(pix.height, pix.stride)[:, : pix.width * pix.n]
            arr = arr.reshape(pix.height, pix.width, pix.n)
            yield i, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    finally:
        doc.close()


def red_mask(img: np.ndarray) -> np.ndarray:
    """인쇄된 마킹칸(빨강 계열)만 남기는 마스크."""
    b, g, r = cv2.split(img.astype(int))
    return (((r - b) > 30) & (r > 120)).astype("uint8") * 255


def save_review_images(pdf_path, pages, out_dir, dpi: int = 300,
                       width: int = 1400) -> int:
    """확인필요 페이지의 스캔 이미지를 out_dir/review_imgs/p{n}.jpg 로 저장.

    웹 검토 UI '카드 보기'용 — auto_orient 만 적용한 원본(정렬 파이프라인과
    무관하게 항상 보여줄 수 있음). pages 는 1-기반 페이지 번호 목록."""
    from pathlib import Path as _P
    pages = sorted({int(p) for p in pages if p})
    if not pages:
        return 0
    img_dir = _P(out_dir) / "review_imgs"
    img_dir.mkdir(parents=True, exist_ok=True)
    for pi, raw in iter_pdf_pages(pdf_path, dpi, pages=[p - 1 for p in pages]):
        img = auto_orient(raw)
        h, w = img.shape[:2]
        small = cv2.resize(img, (width, round(h * width / w)),
                           interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 78])
        if ok:
            buf.tofile(str(img_dir / f"p{pi + 1}.jpg"))
    return len(pages)


def _cluster(vals, gap):
    vals = np.sort(vals)
    groups = [[vals[0]]]
    for v in vals[1:]:
        if v - groups[-1][-1] > gap:
            groups.append([v])
        else:
            groups[-1].append(v)
    return [float(np.mean(g)) for g in groups]


def detect_blobs(mask: np.ndarray) -> np.ndarray:
    """마킹칸 후보 블롭의 (cx, cy) 목록."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mor = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    n, _, stats, cent = cv2.connectedComponentsWithStats(mor, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if 18 <= w <= 60 and 18 <= h <= 55 and a > 180:
            out.append((cent[i][0], cent[i][1]))
    return np.array(out)


def build_answer_grid(blobs: np.ndarray) -> dict:
    """답안 영역의 행 baseline + 블록별 5열 x좌표를 한 페이지에서 추정.

    ⚠️ 단일 페이지 추정은 마킹이 인쇄칸을 덮으면 열 시작/간격이 틀어질 수
    있다(예: 한 칸 밀림). 실전에서는 calibrate_template() 으로 여러 페이지를
    중앙값 합의해 단일 템플릿을 만들어 쓴다.
    """
    # 공통 1·2열 블롭으로 행 baseline(y0, pitch) 추정
    ybase = blobs[(blobs[:, 1] > 380) & (blobs[:, 0] > 1918) & (blobs[:, 0] < 2710)]
    rc = _cluster(ybase[:, 1], 45)
    y0 = min(rc)
    pitch = float(np.median(np.diff(sorted(rc))))

    grid = dict(y0=y0, pitch=pitch, blocks={})
    for blk in ANSWER_BLOCKS:
        sub = blobs[(blobs[:, 0] > blk["x_lo"]) & (blobs[:, 0] < blk["x_hi"])
                    & (blobs[:, 1] > 400) & (blobs[:, 1] < blk["y_hi"])]
        cols = sorted(_cluster(sub[:, 0], 34))
        x0 = cols[0]
        xpitch = float(np.median(np.diff(cols[:5])))
        grid["blocks"][blk["name"]] = dict(
            x0=x0, xpitch=xpitch, q0=blk["q0"], nrows=blk["nrows"])
    return grid


def calibrate_template(image_paths, sample: int | None = None) -> dict:
    """여러 페이지의 격자를 중앙값으로 합의 → 단일 안정 템플릿.

    페이지별 단일추정의 이상치(밀림/간격오류)를 median 이 자동 배제한다.

    image_paths 항목은 경로 / ndarray(BGR) / 무인자 callable(지연 렌더) 모두 허용.
    sample=None 이면 제너레이터를 그대로 스트리밍 소비한다(메모리 1장분).
    """
    items = image_paths
    if sample:
        items = list(items)
        if len(items) > sample:
            step = len(items) / sample
            items = [items[int(i * step)] for i in range(sample)]
    grids = []
    for p in items:
        try:
            src = p() if callable(p) else p
            if not isinstance(src, np.ndarray):
                src = str(src)
            grids.append(build_answer_grid(detect_blobs(red_mask(load_page(src)))))
        except Exception:
            continue
    if not grids:
        raise RuntimeError("템플릿 보정 실패: 유효한 페이지가 없습니다.")

    def med(key_fn):
        return float(np.median([key_fn(g) for g in grids]))

    tmpl = dict(
        y0=med(lambda g: g["y0"]),
        pitch=med(lambda g: g["pitch"]),
        blocks={},
        n_pages=len(grids),
    )
    for blk in ANSWER_BLOCKS:
        nm = blk["name"]
        tmpl["blocks"][nm] = dict(
            x0=med(lambda g: g["blocks"][nm]["x0"]),
            xpitch=med(lambda g: g["blocks"][nm]["xpitch"]),
            q0=blk["q0"], nrows=blk["nrows"])
    return tmpl


def _darkness(gray: np.ndarray, cx: float, cy: float, rad: int = 14) -> float:
    x0, y0 = int(cx - rad), int(cy - rad)
    patch = gray[max(0, y0):y0 + 2 * rad, max(0, x0):x0 + 2 * rad]
    if patch.size == 0:
        return 0.0
    return 255.0 - float(patch.mean())   # 클수록 진하게 채워짐


def read_answers(gray: np.ndarray, grid: dict,
                 fill_min: float = 50.0, margin: float = 20.0) -> dict:
    """문항별 답(1~5), 미마킹=0, 중복마킹=-1."""
    y0, pitch = grid["y0"], grid["pitch"]
    ans = {}
    for bk in grid["blocks"].values():
        xs = [bk["x0"] + bk["xpitch"] * i for i in range(5)]
        for j in range(bk["nrows"]):
            q = bk["q0"] + j
            cy = y0 + pitch * j
            d = np.array([_darkness(gray, cx, cy) for cx in xs])
            idx = int(np.argmax(d))
            others = np.delete(d, idx)
            second = float(np.max(others)) if len(others) else 0.0
            if d[idx] < fill_min or (d[idx] - others.mean()) < margin:
                ans[q] = 0                      # 미마킹/불명확
            elif second >= fill_min and (d[idx] - second) < 12:
                ans[q] = -1                     # 중복마킹 의심
            else:
                ans[q] = idx + 1
    return ans


def read_subject(gray: np.ndarray, fill_min: float = 50.0) -> str | None:
    """선택과목: 화법과작문 / 언어와매체 / None."""
    scores = {name: _darkness(gray, x, y, rad=18)
              for name, (x, y) in SUBJECT_MARKS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= fill_min else None


def process_page(path, template: dict | None = None) -> dict:
    """한 페이지 판독. path 는 파일 경로 또는 ndarray(BGR).
    template 을 주면(권장) 합의 격자로 읽고, 없으면 그 페이지에서 단독 추정(이상치 위험)."""
    img = load_page(path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    grid = template or build_answer_grid(detect_blobs(red_mask(img)))
    return dict(
        path=path if isinstance(path, str) else None,
        subject=read_subject(gray),
        answers=read_answers(gray, grid),
        grid=grid,
    )
