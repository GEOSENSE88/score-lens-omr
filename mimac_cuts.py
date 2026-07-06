# -*- coding: utf-8 -*-
"""
대성마이맥(미맥스터디) 고3 국어·수학 원점수 등급컷 수집 (score_lens / omr_scorer)

EBSi 는 고3 국어·수학(선택과목제) 원점수 등급컷을 확정 단계에 '-' 로 비운다.
미맥은 이를 **선택과목별 원점수 범위**로 제공한다(화작/언매, 확통/미적/기하).
우리 채점기는 학생의 선택과목을 판독하므로, 이 컷으로 국·수 예상등급을 낸다.

경로 (2026-07-06 실측):
  - HmockAnalysis.ds (no param) → 연도 select(연→base groupNo) + date_list(날짜→groupNo)
  - date_list <li><a groupNo=442 ...>6.4</a> → 6월 고3 = groupNo 442 (class="on"=현재)
  - HmockAnalysisExamPointCut.ds?groupNo=442 → 과목별 표. 각 표 직전에 과목명,
    표 안 '1등급 100-95' 처럼 원점수 범위. 컷 = 범위 하한(=그 등급 최소 원점수).

⚠️ 미맥은 고3·N수 전용. 고1·2 는 EBSi 가 원점수를 완전히 주므로 여기서 다루지 않는다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests
import urllib3

ROOT = Path(__file__).resolve().parent

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://www.mimacstudy.com"
ANALYSIS = f"{BASE}/hmockTest/HmockAnalysis.ds"
POINTCUT = f"{BASE}/hmockTest/HmockAnalysisExamPointCut.ds"

# 미맥 과목명(공백 포함) → 우리 키(공백 없음)
G3_ELECTIVES = {
    "국어": ["화법과작문", "언어와매체"],
    "수학": ["확률과통계", "미적분", "기하"],
}
_SUBJ_PAT = "|".join(["화법과 ?작문", "언어와 ?매체", "확률과 ?통계", "미적분", "기하"])


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": f"{BASE}/"})
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=4, connect=4, read=4, backoff_factor=1.5,
        status_forcelist=[502, 503, 504], allowed_methods=None)))
    return s


def _get(session, url, **params) -> str:
    r = session.get(url, params=params or None, timeout=20)
    r.raise_for_status()
    return r.content.decode("euc-kr", "ignore")


def resolve_groupno(year: int, month: int, session=None) -> tuple[str | None, str | None]:
    """(연도, 월) → (고3 groupNo, 매칭된 날짜). 실패 시 (None, None)."""
    s = session or _session()
    html = _get(s, ANALYSIS)                        # no-param → 최신(고3) + 셀렉터
    # 연도 → base groupNo
    years = dict((y, g) for g, y in re.findall(
        r'groupNo=(\d+)"[^>]*>(\d{4})년', html))
    base = years.get(str(year))
    if base is None:
        return None, None
    # 해당 연도 페이지의 date_list
    if base not in html[:1] and f'groupNo={base}"' not in _date_list_html(html):
        html = _get(s, ANALYSIS, groupNo=base)
    for gno, date in _date_list(html):
        if int(date.split(".")[0]) == month:        # 6월 → '6.4'
            return gno, date
    return None, None


def _date_list_html(html: str) -> str:
    m = re.search(r'<ul class="date_list">(.*?)</ul>', html, re.S)
    return m.group(1) if m else ""


def _date_list(html: str) -> list[tuple[str, str]]:
    """[(groupNo, '6.4'), ...] — javascript(준비중) 링크는 제외."""
    out = []
    for a in re.finditer(r'<a href="[^"]*groupNo=(\d+)[^"]*"[^>]*>([\d.]+)</a>',
                         _date_list_html(html)):
        out.append((a.group(1), a.group(2)))
    return out


def _range_to_cut(text: str):
    """'100-95' → 95 (하한=그 등급 최소 원점수). '100' → 100. 없으면 None."""
    nums = [int(n) for n in re.findall(r"\d+", text)]
    return min(nums) if nums else None


def fetch_g3_score_cuts(groupno: str, session=None) -> dict:
    """고3 국어·수학 선택과목별 원점수 등급컷.

    반환: {"on_date": "6.4",
           "국어": {"화법과작문": {1:95, 2:.., ...}, "언어와매체": {...}},
           "수학": {"확률과통계": {...}, "미적분": {...}, "기하": {...}}}
    """
    s = session or _session()
    html = _get(s, POINTCUT, groupNo=groupno)
    on = re.search(r'class="on">([\d.]+)</a>', html)
    result = {"on_date": on.group(1) if on else None, "국어": {}, "수학": {}}

    parts = re.split(r"(<table)", html)
    for i in range(1, len(parts), 2):
        before = parts[i - 1]
        tab = parts[i] + (parts[i + 1] if i + 1 < len(parts) else "")
        txt = re.sub(r"<[^>]+>", " ", tab)
        txt = re.sub(r"\s+", " ", txt)
        if "등급" not in txt or "원점수" not in txt:
            continue
        pre = re.sub(r"<[^>]+>", " ", before[-300:])
        labs = re.findall(_SUBJ_PAT, pre)
        if not labs:
            continue
        elective = _norm(labs[-1])
        subject = "국어" if elective in ("화법과작문", "언어와매체") else "수학"
        cuts = {}
        for gm in re.finditer(r"(\d)등급\s+([\d\- ]+?)\s+\d", txt):
            grade = int(gm.group(1))
            cut = _range_to_cut(gm.group(2))
            if cut is not None:
                cuts[grade] = cut
        if cuts:
            result[subject][elective] = cuts
    return result


def get_g3_score_cuts(year: int, month: int) -> dict:
    """(연도, 월) → 고3 국·수 선택과목별 원점수컷 (+ 검증용 on_date, groupno)."""
    s = _session()
    gno, date = resolve_groupno(year, month, s)
    if not gno:
        return {"ok": False, "reason": f"{year}년 {month}월 고3 시험을 미맥에서 찾지 못함"}
    data = fetch_g3_score_cuts(gno, s)
    return {"ok": True, "groupno": gno, "date": date, **data}


# ── 저장·조회·등급 산출 (고3 국·수 예상등급용) ──────────────────
def save_g3_cuts(irecord: str, data: dict, keys_dir: Path | None = None) -> Path:
    keys_dir = Path(keys_dir or ROOT / "keys")
    keys_dir.mkdir(parents=True, exist_ok=True)
    path = keys_dir / f"{irecord}_미맥등급컷.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_g3_cuts(irecord: str, keys_dir: Path | None = None) -> dict:
    path = Path(keys_dir or ROOT / "keys") / f"{irecord}_미맥등급컷.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def grade_from_cuts(cuts: dict, raw) -> int | str:
    """{등급: 원점수컷} + 원점수 → 예상등급 (raw>=컷 인 최고 등급, 미만이면 9)."""
    if not cuts or raw in (None, ""):
        return ""
    try:
        raw = float(raw)
    except (TypeError, ValueError):
        return ""
    grade = 9
    for g in sorted(int(k) for k in cuts):
        if raw >= float(cuts[str(g)] if str(g) in cuts else cuts[g]):
            grade = g
            break
    return grade


def mimac_g3_grade(irecord: str, subject: str, elective: str, raw,
                   keys_dir: Path | None = None) -> int | str:
    """고3 국어/수학 학생의 (선택과목, 원점수) → 미맥 예상등급. 없으면 ''."""
    data = load_g3_cuts(irecord, keys_dir)
    subj = data.get(subject) or {}
    cuts = subj.get(_norm(elective)) or subj.get(elective)
    if not cuts:                                    # 선택과목 표기 차이 보정
        for k, v in subj.items():
            if _norm(k) == _norm(elective):
                cuts = v
                break
    return grade_from_cuts(cuts, raw) if cuts else ""


def register_g3(irecord: str, year: int, month: int,
                keys_dir: Path | None = None, progress=lambda m: None) -> dict:
    """고3 미맥 국·수 원점수컷 수집·저장. 반환: 요약 dict."""
    progress("미맥에서 고3 국어·수학 원점수 등급컷 수집 중…")
    res = get_g3_score_cuts(year, month)
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason", "실패")}
    # 저장 형식: {과목: {선택과목(공백제거): {등급: 컷}}, _meta}
    store = {"국어": {_norm(k): v for k, v in res["국어"].items()},
             "수학": {_norm(k): v for k, v in res["수학"].items()},
             "_meta": {"source": "대성마이맥", "groupno": res["groupno"],
                       "date": res["date"], "on_date": res["on_date"]}}
    save_g3_cuts(irecord, store, keys_dir)
    n = sum(len(store[s]) for s in ("국어", "수학"))
    progress(f"미맥 국·수 {n}개 선택과목 원점수컷 저장 (시험일 {res['on_date']})")
    return {"ok": True, "electives": n, "date": res["on_date"]}


if __name__ == "__main__":
    import sys
    y, m = int(sys.argv[1]), int(sys.argv[2])
    print(json.dumps(get_g3_score_cuts(y, m), ensure_ascii=False, indent=2))
