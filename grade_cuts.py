# -*- coding: utf-8 -*-
"""
EBSi 예상 등급컷 수집 + 예상 등급·표준점수·백분위 산출 (score_lens / omr_scorer)

데이터 출처: /ebs/xip/xipa/retrieveGrdCutList.ajax (풀서비스 등급컷 탭)
  - 상대평가 과목: 등급별 원점수컷·표준점수컷·백분위컷 + 평균·표준편차
  - 절대평가 과목(영어·한국사): 고정 원점수컷 (등급만 산출)
  - 고3 국어·수학: 원점수컷 비공개('-') → 표점·백분위 컷만 저장(예상치 산출 불가)

산출 (2026-06 고1 실데이터로 공식 검증):
  - 예상등급   : 원점수 ≥ 등급컷[g] 인 최고 등급 (컷 미만이면 9등급)
  - 예상표준점수: base + scale × (원점수-평균)/표준편차
                 만점 100 과목 base=100/scale=20, 만점 50 과목 base=50/scale=10
                 (검증: 고1 국어 91점 → 133.7 ≈ 컷표 134)
  - 예상백분위 : (원점수컷, 백분위컷) 포인트 선형보간, 0~100 클램프

⚠️ 모두 EBSi '예상' 데이터 기반 추정치다. 공식 성적표를 대체하지 않는다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent
EBSI = "https://www.ebsi.co.kr"

# 고3 등급컷 탭의 과목ID 그룹 (SCV 페이지 grdCutList 호출에서 확인)
G3_ORDS = {
    "1": "63004,80003,140117,140119",                       # 국수영한
    "2": "141,142,146,63001,63002,63003,66001,66002,140112",  # 사회탐구
    "3": "154,155,156,157,158,159,140115,140116",           # 과학탐구
}
ABSOLUTE_SUBJECTS = {"영어", "한국사"}   # 절대평가 — 원점수 고정컷, 표점/백분위 없음


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = False    # 학교망 프록시 인증서 대응
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": f"{EBSI}/",
                      "X-Requested-With": "XMLHttpRequest"})
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1.5,
                  status_forcelist=[502, 503, 504], allowed_methods=None)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _num(s: str):
    s = s.strip()
    if not s or s == "-":
        return None
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def _parse_tables(html: str) -> dict[str, dict]:
    """등급컷 HTML → {과목명: {avg, std, cuts:[{grade,last_year,raw,std_score,pct}]}}"""
    out: dict[str, dict] = {}
    # 과목 블록: <h3>과목</h3> ... 다음 <h3> 전까지
    blocks = re.split(r"<h3>", html)[1:]
    for blk in blocks:
        m = re.match(r"([^<]+)</h3>", blk)
        if not m:
            continue
        subject = m.group(1).strip()
        avg = _num((re.search(r"평균\s*:\s*([\d.]+)", blk) or [None, ""])[1])
        std = _num((re.search(r"표준편차\s*:\s*([\d.]+)", blk) or [None, ""])[1])

        cuts = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", blk, re.S):
            cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
            if len(cells) < 5:
                continue
            texts = [re.sub(r"<[^>]+>", "", c[1]).strip() for c in cells]
            grade_txt = texts[0]
            grade = 0 if "최고" in grade_txt else (_num(grade_txt) or None)
            if grade is None:
                continue
            # 열 배치가 과목마다 다르다 (고3 국어=화작/언매 2열, 수학=3열 원점수).
            # 원점수는 class="c_blue" 셀로 식별하고, 표점/백분위는 항상 마지막 2열.
            raw_vals = [_num(texts[i]) for i, (attr, _) in enumerate(cells)
                        if "c_blue" in attr]
            raw = next((v for v in raw_vals if v is not None), None)
            cuts.append(dict(grade=int(grade), last_year=_num(texts[1]),
                             raw=raw, std_score=_num(texts[-2]), pct=_num(texts[-1])))
        if cuts:
            out[subject] = dict(avg=avg, std=std, cuts=cuts)
    return out


def fetch_grade_cuts(irecord: str, grade: int,
                     subject_ids: str | None = None) -> dict:
    """EBSi 에서 등급컷 수집 → {과목명: {...}}.

    고1·2: subject_ids(6과목, discover_papers 의 ID들) 를 ord=1 로 한 번에.
    고3  : 국수영한/사탐/과탐 3개 그룹 순회.
    """
    s = _session()
    merged: dict[str, dict] = {}
    if grade in (1, 2):
        groups = {"1": subject_ids} if subject_ids else G3_ORDS   # ids 없으면 폴백
    else:
        groups = G3_ORDS
    for ord_no, sid in groups.items():
        if not sid:
            continue
        try:
            r = s.post(f"{EBSI}/ebs/xip/xipa/retrieveGrdCutList.ajax",
                       data={"subjectId": sid, "irecord": irecord, "ord": ord_no},
                       timeout=15)
            r.raise_for_status()
            merged.update(_parse_tables(r.content.decode("utf-8", "ignore")))
        except requests.RequestException:
            continue
    return merged


def _n_raw(cut: dict) -> int:
    return sum(1 for c in cut.get("cuts", []) if c.get("raw") is not None)


def save_grade_cuts(irecord: str, grade: int, cuts: dict,
                    keys_dir: Path | None = None) -> Path:
    """등급컷 저장. 재수집 시 원점수컷을 잃지 않도록 병합한다.

    고3 국어·수학은 시험 직후 '예상' 단계에는 원점수 예상컷이 뜨지만, 며칠 뒤
    '확정' 단계가 되면 원점수 칸이 '-' 로 바뀐다(선택과목제라 표준점수만 확정).
    그래서 다시 '정답 가져오기'를 눌러도 이미 받아둔 원점수컷이 사라지지 않도록,
    과목별로 원점수(raw)가 더 많이 채워진 쪽을 유지한다.
    """
    keys_dir = Path(keys_dir or ROOT / "keys")
    keys_dir.mkdir(parents=True, exist_ok=True)
    prev = load_grade_cuts(irecord, keys_dir)
    merged = dict(cuts)
    for subj, old in prev.items():
        new = merged.get(subj)
        # 새 데이터에 원점수가 없거나 예전보다 적으면 예전(원점수 있는) 것을 지킨다
        if _n_raw(old) > _n_raw(new or {}):
            merged[subj] = old
    payload = dict(exam_id=irecord, grade=grade, kind="grade_cuts",
                   source="EBSi retrieveGrdCutList.ajax (예상/확정 등급컷)",
                   subjects=merged)
    path = keys_dir / f"{irecord}_등급컷.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_grade_cuts(irecord: str, keys_dir: Path | None = None) -> dict:
    path = Path(keys_dir or ROOT / "keys") / f"{irecord}_등급컷.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("subjects", {})
    except Exception:
        return {}


# ── 예상치 산출 ──────────────────────────────────────────────────
def _norm(s: str) -> str:
    # 공백·중간점 제거: EBSi 컷 표기 '사회·문화'/'정치와 법' ↔ 판독 과목명 '사회문화'/'정치와법'
    return re.sub(r"[\s·]+", "", str(s or ""))


def find_subject_cut(cuts_by_subject: dict, subject: str) -> dict | None:
    n = _norm(subject)
    for k, v in cuts_by_subject.items():
        if _norm(k) == n:
            return v
    return None


def estimate(cut: dict | None, raw) -> dict:
    """원점수 → {등급, 표준점수, 백분위}. 산출 불가 항목은 ''."""
    out = dict(등급="", 표준점수="", 백분위="")
    if not cut or raw is None or raw == "":
        return out
    try:
        raw = float(raw)
    except (TypeError, ValueError):
        return out
    rows = [c for c in cut.get("cuts", []) if c.get("grade")]   # 1~8등급 컷
    raw_rows = [c for c in rows if c.get("raw") is not None]

    # 예상등급: 원점수컷 비교 (컷 미만 전부면 9등급)
    if raw_rows:
        grade = 9
        for c in sorted(raw_rows, key=lambda c: c["grade"]):
            if raw >= c["raw"]:
                grade = c["grade"]
                break
        out["등급"] = grade

    # 예상 표준점수: 평균·표준편차 공식 (만점으로 base/scale 결정)
    avg, std = cut.get("avg"), cut.get("std")
    if avg is not None and std:
        top = next((c for c in cut.get("cuts", []) if c.get("grade") == 0), None)
        full = (top or {}).get("raw") or (100 if avg > 25 else 50)
        base, scale = (100, 20) if full >= 100 else (50, 10)
        z = (raw - avg) / std
        out["표준점수"] = int(round(base + scale * z))

    # 예상 백분위: (원점수컷, 백분위컷) 선형보간
    pts = sorted({(c["raw"], c["pct"]) for c in cut.get("cuts", [])
                  if c.get("raw") is not None and c.get("pct") is not None})
    if len(pts) >= 2:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if raw <= xs[0]:
            # 최저 컷 아래: 마지막 구간 기울기로 외삽 (0 미만 클램프)
            slope = (ys[1] - ys[0]) / (xs[1] - xs[0]) if xs[1] != xs[0] else 0
            val = ys[0] + (raw - xs[0]) * slope
        elif raw >= xs[-1]:
            val = ys[-1]
        else:
            for i in range(len(xs) - 1):
                if xs[i] <= raw <= xs[i + 1]:
                    t = (raw - xs[i]) / (xs[i + 1] - xs[i]) if xs[i + 1] != xs[i] else 0
                    val = ys[i] + t * (ys[i + 1] - ys[i])
                    break
        out["백분위"] = int(round(max(0.0, min(100.0, val))))
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="EBSi 등급컷 수집/예상치 산출")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("irecord")
    f.add_argument("--grade", type=int, default=3)
    f.add_argument("--subject-ids", default=None)
    e = sub.add_parser("est")
    e.add_argument("irecord")
    e.add_argument("subject")
    e.add_argument("raw", type=float)
    a = ap.parse_args()
    if a.cmd == "fetch":
        cuts = fetch_grade_cuts(a.irecord, a.grade, a.subject_ids)
        p = save_grade_cuts(a.irecord, a.grade, cuts)
        print(f"{len(cuts)}과목 → {p}")
        for name, c in cuts.items():
            n_raw = sum(1 for x in c["cuts"] if x.get("raw") is not None)
            print(f"  {name}: 컷 {len(c['cuts'])}행(원점수 {n_raw}), 평균={c.get('avg')} σ={c.get('std')}")
    else:
        cut = find_subject_cut(load_grade_cuts(a.irecord), a.subject)
        print(estimate(cut, a.raw))
