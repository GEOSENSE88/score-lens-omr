# -*- coding: utf-8 -*-
"""고3 학평 카드의 숫자 그리드·선택박스 판독 — 수험번호/선택과목/탐구코드/수학 단답형.

전제: 페이지는 hp_align.rectify 로 정렬됨(고정 좌표 직독).
보정: 답란과 동일하게 '인쇄 마킹칸 블롭'의 여러 페이지 합의(중앙값)로
그리드 xs/ys 를 확정한다. spec 의 창(x_lo..y_hi)은 그리드 분리용일 뿐.

spec 확장 형식:
  "grids":  [{"name":"id","ncols":9,"nrows":10,"x_lo":..,"x_hi":..,"y_lo":..,"y_hi":..}]
  "selects":[{"name":"choice","labels":["화작","언매"],"x_lo":..,"x_hi":..,"y_lo":..,"y_hi":..}]
템플릿 산출:
  "grids":  [{name, ncols, nrows, xs[ncols], ys[nrows]}]
  "selects":[{name, labels, x, ys[n]}]
"""
from __future__ import annotations

import numpy as np

import omr_core as oc
import hp_g3


def _union_vote(vals_per_page: list[list[float]], want: int, npages: int,
                what: str, lo: float, hi: float,
                sparse_idx: int | None = None) -> list[float]:
    """페이지별 군집 좌표의 합집합을 다시 군집 → 다수 페이지 재현 좌표만.

    인쇄가 흐린 카드(예: 국어 수험번호 원)는 페이지당 절반만 잡혀도 여러
    페이지 합집합이면 복원된다. 개수가 모자라면(테두리와 붙어 블롭이 안 잡히는
    가장자리 열, 학년처럼 희박한 열) 등간격 격자 적합으로 보간한다 — 창(lo..hi)
    안에 want 개 격자가 들어가는 시작점 중 관측 좌표 매칭이 최대인 것."""
    allv = sorted(v for vs in vals_per_page for v in vs)
    if not allv:
        raise RuntimeError(f"{what}: 블롭 없음")
    groups, cur = [], [allv[0]]
    for v in allv[1:]:
        if v - cur[-1] <= 16:
            cur.append(v)
        else:
            groups.append(cur)
            cur = [v]
    groups.append(cur)
    # 임계: 희박 그리드(수학 단답형 — 문항당 마크 1~3개)도 살리도록 완만하게.
    # 잡음 군집은 등간격 격자 적합이 걸러낸다.
    thr = max(2, npages // 10)
    kept = [(float(np.median(g)), len(g)) for g in groups if len(g) >= thr]
    kept.sort()
    cen = [c for c, _ in kept]
    size = {c: n for c, n in kept}
    if len(cen) == want and sparse_idx is None:
        return [round(c, 1) for c in cen]
    if len(cen) < 2:
        raise RuntimeError(f"{what}: 좌표 부족 — {[round(c) for c in cen]}")
    d = [b - a for a, b in zip(cen, cen[1:])]
    m = float(np.median(d))
    good = [x for x in d if 0.7 * m <= x <= 1.3 * m]
    pitch = float(np.median(good)) if good else m
    cands = sorted({c - pitch * k for c in cen for k in range(want)
                    if lo <= c - pitch * k and c - pitch * k + pitch * (want - 1) <= hi})
    if not cands:
        raise RuntimeError(f"{what}: 격자 적합 후보 없음 — {[round(c) for c in cen]}")
    def fit(y0):
        errs = [min(abs(y0 + pitch * k - c) for c in cen) for k in range(want)]
        return (sum(1 for e in errs if e <= 12), -round(sum(errs)))
    best = max(fit(c)[0] for c in cands)
    tied = [c for c in cands if fit(c)[0] == best]
    if len(tied) > 1 and sparse_idx is not None:
        # 물리 판별자: sparse_idx 번째 열(학년)은 인쇄 원이 없어 블롭이 최소.
        def sup(y0):
            x = y0 + pitch * sparse_idx
            return min((size[c] for c in cen if abs(c - x) <= 12), default=0)
        y0 = min(tied, key=sup)
    else:
        y0 = max(tied, key=fit)
    need = min(len(cen), max(2, want - 2))   # 관측 군집 대부분이 격자에 얹혀야
    if fit(y0)[0] < need:
        raise RuntimeError(f"{what}: 격자 적합 실패 (매칭 {fit(y0)[0]}/{want}) — "
                           f"{[round(c) for c in cen]}")
    return [round(y0 + pitch * k, 1) for k in range(want)]


def calibrate_extras(spec: dict, pages) -> dict:
    """rectify 된 페이지 이터러블에서 grids/selects 좌표 합의(합집합 투표)."""
    grids = {g["name"]: {"cols": [], "rows": [], "g": g} for g in spec.get("grids", [])}
    npages = 0
    for img in pages:
        blobs = hp_g3.mark_blobs(img)   # 마킹만 — 인쇄색·배경색 무관
        if len(blobs) == 0:
            continue
        npages += 1
        for st in grids.values():
            g = st["g"]
            sub = blobs[(blobs[:, 0] > g["x_lo"]) & (blobs[:, 0] < g["x_hi"])
                        & (blobs[:, 1] > g["y_lo"]) & (blobs[:, 1] < g["y_hi"])]
            if len(sub) < 2:
                continue
            st["cols"].append(sorted(oc._cluster(sub[:, 0], 26)))
            st["rows"].append(sorted(oc._cluster(sub[:, 1], 30)))
    out = {"grids": []}
    for name, st in grids.items():
        g = st["g"]
        out["grids"].append(dict(
            name=name, ncols=g["ncols"], nrows=g["nrows"],
            xs=_union_vote(st["cols"], g["ncols"], npages, f"그리드 {name} 열",
                           g["x_lo"], g["x_hi"],
                           # id: 학년열(6번째) 희박 / sa: 백의자리(첫째) 희박
                           sparse_idx=5 if name == "id"
                           else (0 if name.startswith("sa") else None)),
            ys=_union_vote(st["rows"], g["nrows"], npages, f"그리드 {name} 행",
                           g["y_lo"], g["y_hi"])))
    # 선택박스는 라벨 글자와 블롭이 붙어 자동합의가 불안정 → spec 의 고정좌표
    # (정렬 프레임에서 실측)를 그대로 신뢰해 템플릿으로 복사한다.
    if "selects" in spec:
        out["selects"] = spec["selects"]
    return out


def read_digit_grid(gray: np.ndarray, grid: dict,
                    fill_min: float = hp_g3.FILL_MIN) -> list[int | None]:
    """열별 마킹 숫자(행 index = 숫자 0~9). 미마킹 None, 중복 -1.

    답란과 동일한 상대 규칙(hp_g3.decide_cell) 적용 — 지운 자국·연한 점
    마킹을 구제한다(수험번호 오채택은 학교코드 읽기-검증·반=파일 대조가 방어)."""
    out = []
    for x in grid["xs"]:
        dk = [max(oc._darkness(gray, x, y + d, 14) for d in (-12, 0, 12))
              for y in grid["ys"]]
        v = hp_g3.decide_cell(dk, fill_min)
        out.append(None if v == 0 else (v - 1 if v > 0 else -1))
    return out


def read_select(gray: np.ndarray, sel: dict, fill_min: float = 45.0) -> str | None:
    """선택박스: 마킹된 라벨(단일)을 반환. 미마킹 None, 중복 '!중복'.

    sel = {"name":.., "options": [[label, x, y], ...]} (정렬 프레임 고정좌표).
    마킹은 속이 찬 블롭이라 상자평균 darkness 로 인쇄 숫자(테두리+획)와
    구분된다. ±16px 국소 탐색으로 잔여 오차 흡수."""
    dk = []
    for _, x, y in sel["options"]:
        dk.append(max(oc._darkness(gray, x + dx, y + dy, 14)
                      for dx in (-16, 0, 16) for dy in (-16, 0, 16)))
    order = sorted(range(len(dk)), key=lambda i: -dk[i])
    top, second = dk[order[0]], dk[order[1]]
    marked = [i for i, v in enumerate(dk) if v >= fill_min]
    if len(marked) == 1:
        return sel["options"][marked[0]][0]
    if len(marked) > 1:
        # 최고가 2위의 1.8배 이상이면 최고 채택(인쇄 잔상 방어), 아니면 중복
        return sel["options"][order[0]][0] if top >= 1.8 * second else "!중복"
    # 절대 임계 미달이어도 상대 우세가 확실하면 채택 — 점만 콕 찍은 연한
    # 마킹(실측 darkness 29~45, 빈 옵션 2~5) 구제. V채널이라 인쇄는 ~0.
    if top >= 18 and top >= 4 * max(second, 1.0):
        return sel["options"][order[0]][0]
    return None


def read_id(gray: np.ndarray, grid: dict) -> dict:
    """수험번호 그리드(학교5+학년1+반2+번호2 = 10열) → {school, grade, ban, num, raw}."""
    d = read_digit_grid(gray, grid)
    def join(part):
        return None if any(v is None or v == -1 for v in part) else int("".join(map(str, part)))
    return dict(school=join(d[0:5]), grade=join(d[5:6]),
                ban=join(d[6:8]), num=join(d[8:10]), raw=d)


def read_name(gray: np.ndarray, grid: dict, fill_min: float = 50.0) -> dict:
    """성명 그리드(음절 4 × [초성|중성|종성] 3열, 자모 21행) → 판독 결과.

    자모 세로 배열은 평가원 카드와 동일(초성 19 / 중성 21 / 종성 21)임을
    실측으로 확인(2026-07 학평 카드, '권세진'·'권지연' 수동 대조).

    반환: {name, ok, issues[]} — **조용한 이름 생성 금지**:
      - 초성은 마킹됐는데 중성이 임계 미달 → 그 음절 '?' + issue (기본 ㅏ 대체 않음)
      - 한 열에 임계 이상이 2개고 1·2위 차이가 근소 → 중복 issue
    ok=False 인 이름은 호출측이 성명으로 쓰지 말고 검토 플래그를 달아야 한다."""
    import name_reader as nr
    xs, ys = grid["xs"], grid["ys"]

    def pick(x: float, nrows: int):
        d = [max(oc._darkness(gray, x, y + dy, 13) for dy in (-10, 0, 10))
             for y in ys[:nrows]]
        order = sorted(range(nrows), key=lambda i: -d[i])
        top, second = d[order[0]], d[order[1]]
        dup = top >= fill_min and second >= fill_min and top < 1.6 * second
        return order[0], top, dup

    out, issues = [], []
    for s in range(4):
        ci, cd, cdup = pick(xs[3 * s], len(nr.CHO))
        if cd < fill_min:
            break                          # 초성 공란 → 이름 끝
        ji, jd, jdup = pick(xs[3 * s + 1], len(nr.JUNG))
        ki, kd, kdup = pick(xs[3 * s + 2], len(nr.JONG_COL))
        if jd < fill_min:                  # 초성만 있고 중성 미달 — 조합 금지
            issues.append(f"{s + 1}음절 중성 미판독")
            out.append("?")
            continue
        if cdup or jdup or (kd >= fill_min and kdup):
            issues.append(f"{s + 1}음절 자모 중복의심")
        jong = nr.JONG_INDEX.get(nr.JONG_COL[ki], 0) if kd >= fill_min else 0
        out.append(nr.compose(ci, ji, jong))
    return dict(name="".join(out), ok=not issues, issues=issues)


def read_short_answer(gray: np.ndarray, grid: dict) -> int | None:
    """수학 단답형(백·십·일 3열): 마킹 숫자 → 정수. 규정상 빈 상위자리 허용
    (한 자리 답은 일의 자리만, 또는 십의자리 0 표기). 전체 미마킹은 None,
    중복 마킹 열은 판독불가(-1 반환)."""
    d = read_digit_grid(gray, grid)
    if any(v == -1 for v in d):
        return -1
    if all(v is None for v in d):
        return None
    return sum((v or 0) * m for v, m in zip(d, (100, 10, 1)))
