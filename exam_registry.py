# -*- coding: utf-8 -*-
"""
EBSi 시험 자동 등록 (score_lens / omr_scorer)

교사가 시험 ID(irecord)를 몰라도 되도록:
  1) find_irecord(year, month, grade)  — (연도, 월, 학년)으로 EBSi 를 프로브해 irecord 발견
  2) discover_papers(irecord)          — 오답률 페이지 HTML에서 과목별 paper_id 자동 추출
  3) register_exam(...)                — 국어/수학/XIP(영어·한국사·탐구) 정답키 자동 생성
  4) list_exams(keys_dir)              — keys/ 스캔 → 시험 드롭다운 목록

검증 근거 (2026-07-03):
  - irecord 규칙 YYYYMM04G: 202506043·202606043(고3)·202606041(고1)·202606042(고2) 유효 확인.
  - wrongList HTML 의 <tr class="... subj_80003"> 행 안 fnVodSelect(27037323 …) 가
    ebsi_xip_keys.PAPER_IDS 의 (202606043, 영어)=27037323 과 일치.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import requests
import urllib3

import ebsi_xip_keys as xip
import grade_cuts as gcut
import mimac_cuts as mimac

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent
EBSI = "https://www.ebsi.co.kr"

# 학년별 EBSi targetCd (풀서비스 URL 파라미터)
TARGET_CD = {1: "D100", 2: "D200", 3: "D300"}


def _session() -> requests.Session:
    s = requests.Session()
    s.verify = False    # 학교망 프록시 인증서 대응 (기존 스크립트들과 동일)
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": f"{EBSI}/"})
    # EBSi 가 서버 egress 에서 가끔 느림 → 연결/읽기 타임아웃 재시도(백오프)
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1.5,
                  status_forcelist=[502, 503, 504], allowed_methods=None)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


# ── 1) irecord 발견 ───────────────────────────────────────────────
def probe_irecord(irecord: str, session: requests.Session | None = None) -> bool:
    """해당 irecord 가 EBSi 에 존재하는가 (풀서비스 페이지 응답으로 판정)."""
    s = session or _session()
    try:
        r = s.get(f"{EBSI}/ebs/xip/xipt/RetrieveSCVMainTop.ajax",
                  params={"irecord": irecord}, timeout=20)
        return r.status_code == 200 and len(r.content) > 10_000
    except requests.RequestException:
        return False


def find_irecord(year: int, month: int, grade: int) -> str | None:
    """(연도, 월, 학년) → irecord = YYYY·MM·OO·G.

    OO(시행 2자리 코드)는 주관 기관에 따라 달라진다 — 평가원(6·9월 모평)=04,
    시도교육청 전국연합학력평가는 회차마다 다름(관측: 2026-07=08, 2025-07=10).
    따라서 04 를 우선 시도하고, 이어서 01~20 을 관측 빈도순으로 전수 프로브한다.
    (첫 히트를 반환 — 같은 (연·월·학년) 조합엔 시험이 하나뿐.)"""
    s = _session()
    priority = [4, 8, 10, 11, 3, 7, 9, 1, 2, 5, 6, 12]     # 관측·추정 빈도순
    codes = priority + [c for c in range(1, 21) if c not in priority]
    for oo in codes:
        ir = f"{year}{month:02d}{oo:02d}{grade}"
        if probe_irecord(ir, s):
            return ir
    return None


# ── 2) 과목별 paper_id 자동 발견 ─────────────────────────────────
_ID2SUBJ = {v: k for k, v in xip.SUBJECT_IDS.items()}


def discover_papers(irecord: str) -> dict[str, str]:
    """오답률 페이지 HTML → {과목명: paper_id}.

    각 문항 행이 <tr class="resultArea subj_<과목ID>"> 이고 행 안에
    fnVodSelect(<paperId>, …) 가 있다. 과목ID→과목명은 xip.SUBJECT_IDS 역매핑.
    미지의 과목ID 는 'subj_<ID>' 그대로 반환해 확장 여지를 남긴다.
    """
    s = _session()
    r = s.get(f"{EBSI}/ebs/xip/xipa/retrieveMainWrongList.ajax",
              params={"irecord": irecord}, timeout=15)
    r.raise_for_status()
    html = r.content.decode("utf-8", "ignore")

    papers: dict[str, str] = {}
    for m in re.finditer(r'class="resultArea subj_(\d+)"[^>]*>(.*?)</tr>', html, re.S):
        subj_id, row = m.group(1), m.group(2)
        pid = re.search(r"fnVodSelect\((\d{6,10})", row)
        if not pid:
            continue
        name = _ID2SUBJ.get(subj_id, f"subj_{subj_id}")
        papers.setdefault(name, pid.group(1))
    return papers


# ── 3) 시험 등록 (정답키 자동 생성) ──────────────────────────────
# 고1·2 과목 판별: ① 검증된 과목ID 매핑 우선, ② 없으면 교시순+문항수 시그니처.
# (시그니처만으로는 국어↔영어(둘 다 45문항)가 순서 뒤바뀌면 구분 불가하므로
#  ID 매핑을 1순위로 둔다. ID 는 2026-06 고1/고2 wrongList 에서 실측.)
_G12_SUBJECT_IDS = {
    # 고1 (202606041)
    "17012": "국어", "110001": "수학", "17014": "영어", "17020": "한국사",
    "140072": "통합사회", "140073": "통합과학",
    # 고2 (202606042)
    "17022": "국어", "140111": "수학", "120013": "영어", "50612": "한국사",
    "140220": "통합사회", "140221": "통합과학",
}
_G12_SIGNATURE = [("국어", 45), ("수학", 30), ("영어", 45), ("한국사", 20),
                  ("통합사회", 25), ("통합과학", 25)]


def _write_key_g12(irecord: str, subject: str, paper_id: str, keys_dir: Path,
                   session: requests.Session) -> dict:
    """고1·2 과목 키 생성. XIP Point 는 ×10 저장(15=1.5점)이라 /10 정규화."""
    answers, item_ids, answers_url = xip.fetch_answers(session, paper_id)
    points_raw, _, paper_url = xip.fetch_points(session, paper_id)
    if set(answers) != set(points_raw):
        raise RuntimeError(f"{subject}: 정답/배점 문항 불일치")

    def norm(v: int) -> float | int:
        f = v / 10 if v >= 10 else float(v)   # 45문항×2점대 → Point 20 형태
        return int(f) if float(f).is_integer() else f

    points = {q: norm(v) for q, v in points_raw.items()}
    payload = {
        "exam_id": irecord,
        "subject": subject,
        "elective": None,
        "paper_id": paper_id,
        "grade_band": "g12",
        "complete": True,
        "item_count": len(answers),
        "max_score": round(sum(points.values()), 1),
        "answers": {str(q): answers[q] for q in sorted(answers)},
        "points": {str(q): points[q] for q in sorted(points)},
        "source": {"answers_api": answers_url, "paper_api": paper_url,
                   "note": "XIP API; points normalized /10 (e.g. 15 -> 1.5)."},
    }
    keys_dir.mkdir(parents=True, exist_ok=True)
    path = keys_dir / f"{irecord}_{subject}_xip_api.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


# ── 고3 국어·수학 키: XIP 정답 API 기반(해설 PDF 레이아웃 비의존) ──────
_XIP_TYPE = {"21": "objective", "31": "short"}   # TypeID → OMR 채점 유형
_KOR_ELECTIVES = {"화법과 작문", "언어와 매체"}
_MATH_ELECTIVES = {"확률과 통계", "미적분", "기하"}


def _register_g3_kuksu(irecord: str, papers: dict[str, str], keys_dir: Path,
                       has_key, out: dict, progress) -> dict:
    """국·수 선택과목 키를 XIP 에서 생성. 반환 {'국어':[선택과목…], '수학':[…]}.

    discover_papers 의 미지 subj_ 과목들 중 과목명(SubjectName)이 국·수 선택과목인
    시험지를 골라 stored 스키마와 동일하게 저장한다(이미 있으면 건너뛰고 완비로 집계).
    """
    s = xip._session()
    built = {"국어": [], "수학": []}
    for key, pid in papers.items():
        if not key.startswith("subj_"):
            continue
        try:
            name, points, types, purl = xip.fetch_paper_detail(s, pid)
        except Exception:
            continue
        if name in _KOR_ELECTIVES:
            subj = "국어"
        elif name in _MATH_ELECTIVES:
            subj = "수학"
        else:
            continue
        path = keys_dir / f"{irecord}_{subj}_{name.replace(' ', '')}.json"
        if path.exists():
            built[subj].append(name)
            out["skipped"].append(f"{subj} {name}(이미 있음)")
            continue
        try:
            progress(f"{subj} {name} 정답키 생성 중… (EBSi XIP)")
            answers, _, aurl = xip.fetch_answers(s, pid)
            if set(answers) != set(points):
                raise RuntimeError(f"정답/배점 문항 불일치: {sorted(set(answers) ^ set(points))}")
            if subj == "국어":
                payload = {
                    "exam_id": irecord, "subject": "국어", "elective": name,
                    "max_score": round(sum(points.values())),
                    "answers": {str(q): answers[q] for q in sorted(answers)},
                    "points": {str(q): points[q] for q in sorted(points)},
                    "source": {"xip_answer_api": aurl, "xip_paper_api": purl},
                }
            else:
                questions = {
                    str(q): {"answer": answers[q], "points": points[q],
                             "type": _XIP_TYPE.get(types.get(q), "objective")}
                    for q in sorted(answers)
                }
                payload = {
                    "exam": f"irecord {irecord}", "subject": "수학", "elective": name,
                    "irecord": irecord, "total": round(sum(points.values())),
                    "questions": questions,
                    "source": {"xip_answer_api": aurl, "xip_paper_api": purl},
                }
            keys_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            built[subj].append(name)
            out["created"].append(f"{subj} {name}")
        except Exception as exc:
            out["errors"].append(f"{subj} {name}: {exc}")
    return built


def register_exam(year: int, month: int, grade: int,
                  keys_dir: Path | None = None,
                  progress=lambda msg: None) -> dict:
    """시험 하나를 통째로 등록. 반환: {irecord, created[], skipped[], errors[]}"""
    keys_dir = Path(keys_dir or ROOT / "keys")
    out = dict(irecord=None, created=[], skipped=[], errors=[])

    progress(f"{year}년 {month}월 고{grade} 시험을 EBSi에서 찾는 중…")
    irecord = find_irecord(year, month, grade)
    if not irecord:
        out["errors"].append("EBSi에서 시험을 찾지 못했습니다. 아직 등록 전이거나 다른 회차 체계입니다.")
        return out
    out["irecord"] = irecord
    progress(f"시험 발견: irecord={irecord}")

    def has_key(subject: str) -> bool:
        return any(subject in p.stem for p in keys_dir.glob(f"{irecord}_*{subject}*.json"))

    progress("과목별 시험지 ID를 자동 수집 중…")
    try:
        papers = discover_papers(irecord)
    except Exception as exc:
        out["errors"].append(f"paper_id 수집 실패: {exc}")
        papers = {}
    progress(f"발견 과목 {len(papers)}개")

    # 등급컷 (예상 등급·표준점수·백분위 산출용) — 재등록 시 갱신됨
    def fetch_cuts(subject_ids: str | None):
        try:
            progress("EBSi 등급컷(예상 등급·표점·백분위) 수집 중…")
            cuts = gcut.fetch_grade_cuts(irecord, grade, subject_ids)
            if cuts:
                gcut.save_grade_cuts(irecord, grade, cuts, keys_dir)
                n_est = sum(1 for c in cuts.values()
                            if any(x.get("raw") is not None for x in c["cuts"]))
                out["grade_cuts"] = f"{len(cuts)}과목 (예상치 산출가능 {n_est})"
                progress(f"등급컷 {len(cuts)}과목 저장 ✓")
            else:
                out["grade_cuts"] = "없음 (분석 전이거나 미공개)"
        except Exception as exc:
            out["errors"].append(f"등급컷 수집 실패: {exc}")

    if grade in (1, 2):
        # subj_<ID> 키에서 과목ID 추출해 등급컷 조회
        ids = ",".join(re.sub(r"^subj_", "", k) for k in papers) if papers else None
        fetch_cuts(ids)
        return _register_g12(irecord, papers, keys_dir, has_key, out, progress)
    fetch_cuts(None)   # 고3: 국수영한/사탐/과탐 고정 그룹

    # 고3 국어·수학 원점수 등급컷은 EBSi 가 비공개('-') → 미맥에서 선택과목별로 보강
    try:
        mres = mimac.register_g3(irecord, year, month, keys_dir,
                                 progress=lambda m: progress(m))
        out["mimac"] = (f"국·수 {mres['electives']}선택과목 (시험일 {mres['date']})"
                        if mres.get("ok") else f"실패: {mres.get('reason')}")
    except Exception as exc:
        out["mimac"] = f"미맥 수집 실패: {exc}"

    # ── 고3 ──
    env_cmd = dict(cwd=ROOT, text=True, encoding="utf-8", errors="replace",
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)

    # 국어·수학 — XIP(정답 API) 우선. 시도교육청 학평의 해설 PDF 레이아웃이
    # 평가원과 달라 PDF 파서가 자주 실패하므로, 레이아웃 비의존인 XIP 로 먼저
    # 생성하고 부족분만 기존 PDF 파서로 보강한다. (6월 평가원 키와 완전 일치 검증됨)
    kuksu = _register_g3_kuksu(irecord, papers, keys_dir, has_key, out, progress)

    if len(kuksu.get("국어", [])) >= 2:
        pass  # 화작·언매 모두 XIP 로 확보
    elif has_key("국어"):
        out["skipped"].append("국어(이미 있음)")
    else:
        progress("국어 정답키 추출 중… (EBSi 해설 PDF 폴백)")
        url = f"{EBSI}/ebs/xip/xipa/retrieveSCVMainInfo.ebs?irecord={irecord}&targetCd={TARGET_CD[grade]}"
        p = subprocess.run([sys.executable, "ebsi_keys.py", url], **env_cmd)
        (out["created"] if p.returncode == 0 else out["errors"]).append(
            "국어" if p.returncode == 0 else f"국어: {p.stdout[-300:]}")

    if len(kuksu.get("수학", [])) >= 3:
        pass  # 확통·미적·기하 모두 XIP 로 확보
    elif has_key("수학"):
        out["skipped"].append("수학(이미 있음)")
    else:
        progress("수학 정답키 추출 중… (EBSi 문제/해설 PDF 폴백)")
        p = subprocess.run([sys.executable, "math_keys.py", irecord], **env_cmd)
        (out["created"] if p.returncode == 0 else out["errors"]).append(
            "수학" if p.returncode == 0 else f"수학: {p.stdout[-300:]}")

    # XIP 과목 (영어·한국사·탐구류)
    for subject, paper_id in sorted(papers.items()):
        if subject.startswith("subj_"):
            out["skipped"].append(f"{subject}(미지 과목ID)")
            continue
        if has_key(subject):
            out["skipped"].append(f"{subject}(이미 있음)")
            continue
        try:
            progress(f"{subject} 정답키 받는 중…")
            xip.write_key(irecord, subject, paper_id, keys_dir)
            out["created"].append(subject)
        except Exception as exc:
            out["errors"].append(f"{subject}: {exc}")
    return out


def _g12_subject_pairs(papers: dict[str, str], out: dict) -> list[tuple[str, int, str]]:
    """papers({'subj_<ID>': paper_id}) → [(과목명, 기대문항수, paper_id)].
    ① 검증된 과목ID 매핑, ② 폴백: 교시순(paper id 오름차순)+시그니처."""
    expect_n = dict(_G12_SIGNATURE)
    by_id = {}
    for key, pid in papers.items():
        sid = re.sub(r"^subj_", "", key)
        if sid in _G12_SUBJECT_IDS:
            by_id[_G12_SUBJECT_IDS[sid]] = pid
    if len(by_id) == len(_G12_SIGNATURE):
        return [(subj, expect_n[subj], pid) for subj, pid in by_id.items()]
    out.setdefault("notes", []).append(
        f"과목ID 매핑 {len(by_id)}/6 — 교시순 시그니처 폴백 사용 (새 회차 ID 는 확인 후 매핑에 추가 권장)")
    ordered = sorted(papers.values(), key=int)
    if len(ordered) != len(_G12_SIGNATURE):
        return []
    return [(subj, n, pid) for (subj, n), pid in zip(_G12_SIGNATURE, ordered)]


def _register_g12(irecord: str, papers: dict[str, str], keys_dir: Path,
                  has_key, out: dict, progress) -> dict:
    """고1·2: 6과목 전부 XIP 로 생성. 과목ID 매핑 우선, 시그니처 폴백."""
    s = _session()
    pairs = _g12_subject_pairs(papers, out)
    if not pairs:
        out["errors"].append(
            f"과목 수가 예상(6)과 다릅니다: {len(papers)}개 — 판별을 건너뜁니다.")
        return out
    for subject, expect_n, paper_id in pairs:
        if has_key(subject):
            out["skipped"].append(f"{subject}(이미 있음)")
            continue
        try:
            progress(f"{subject} 정답키 받는 중…")
            payload = _write_key_g12(irecord, subject, paper_id, keys_dir, s)
            if payload["item_count"] != expect_n:
                out["errors"].append(
                    f"{subject}: 문항수 {payload['item_count']} ≠ 예상 {expect_n} — 키 파일 확인 필요")
            else:
                out["created"].append(subject)
        except Exception as exc:
            out["errors"].append(f"{subject}: {exc}")
    return out


# ── 4) 등록된 시험 목록 (드롭다운용) ─────────────────────────────
def exam_label(exam_id: str) -> str:
    m = re.fullmatch(r"(\d{4})(\d{2})\d\d(\d)", str(exam_id))
    if not m:
        return str(exam_id)
    y, mo, g = m.group(1), int(m.group(2)), m.group(3)
    return f"{y}년 {mo}월 모의고사 (고{g})"


def list_exams(keys_dir: Path | None = None) -> list[dict]:
    keys_dir = Path(keys_dir or ROOT / "keys")
    exams: dict[str, set] = {}
    for p in keys_dir.glob("*.json"):
        try:
            k = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        eid = str(k.get("exam_id") or k.get("irecord") or "")
        subj = k.get("subject")
        if eid and subj:
            exams.setdefault(eid, set()).add(subj)
    out = [dict(exam_id=eid, label=exam_label(eid),
                grade=int(eid[-1]) if eid[-1].isdigit() else None,
                subjects=sorted(subs))
           for eid, subs in exams.items()]
    out.sort(key=lambda e: e["exam_id"], reverse=True)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="EBSi 시험 자동 등록")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find");     f.add_argument("year", type=int); f.add_argument("month", type=int); f.add_argument("grade", type=int)
    d = sub.add_parser("papers");   d.add_argument("irecord")
    r = sub.add_parser("register"); r.add_argument("year", type=int); r.add_argument("month", type=int); r.add_argument("grade", type=int)
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "find":
        print(find_irecord(a.year, a.month, a.grade))
    elif a.cmd == "papers":
        print(json.dumps(discover_papers(a.irecord), ensure_ascii=False, indent=2))
    elif a.cmd == "register":
        res = register_exam(a.year, a.month, a.grade, progress=print)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(list_exams(), ensure_ascii=False, indent=2))
