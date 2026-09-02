# -*- coding: utf-8 -*-
"""
과목별 채점결과 CSV 통합 → 통합 성적 워크북 (score_lens / omr_scorer)

run.py / run_math.py / run_objective.py / run_explore.py 가 output/ 에 떨군
과목별 점수표·판독표 CSV 를 학생 단위로 모아 report_export.build_workbook 에 넘긴다.
학생 매칭은 정확한 (반,번호)를 우선하고, 과목 간 수험번호 오독은 유일한 성명으로
보완한다. 같은 과목에 동명이인이 있으면 각각의 (반,번호)로 별도 유지한다.

    python consolidate.py --grade 3 --out-dir output --keys-dir keys \
        --korean output/20260604165616_점수표.csv \
        --math   output/20260604165525_수학_점수표.csv \
        --english output/20260604165440_영어_판독표.csv \
        --history output/20260604165332_한국사_판독표.csv \
        --explore output/20260604165249_탐구_판독표.csv \
        --out output/통합성적표.xlsx
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import grade_cuts as gc
import report_export as rx


def _read_csv(path):
    if not path:
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _ints(s):
    return [int(x) for x in str(s or "").split() if x.strip().lstrip("-").isdigit()]


def _norm(s):
    return str(s or "").replace(" ", "").strip()


def _num_pt(v):
    """배점 정규화 — 고1·2 통합과목은 1.5점 등 소수 배점이 있어 float 허용."""
    f = float(v)
    return int(f) if f.is_integer() else f


def _num_score(v, default=0):
    """점수 파싱 — '9.5' 같은 소수 점수 허용, 빈값은 default."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return int(f) if f.is_integer() else round(f, 1)


def _key_ans_pts(k: dict):
    """정답키 dict → ({q:정답}, {q:배점}). answers/points 또는 questions dict 포맷 지원."""
    ans = {int(q): int(v) for q, v in (k.get("answers") or {}).items()}
    pts = {int(q): _num_pt(v) for q, v in (k.get("points") or {}).items()}
    if not ans and isinstance(k.get("questions"), dict):   # 수학 키 포맷
        for q, spec in k["questions"].items():
            if isinstance(spec, dict) and spec.get("answer") is not None:
                ans[int(q)] = int(spec["answer"])
                pts[int(q)] = _num_pt(spec.get("points", 0))
    return ans, pts


def _load_key_points(keys_dir: Path, subject: str, elective: str | None,
                     exam_id: str | None = None):
    """keys/ 에서 (subject[, elective][, exam_id]) 정답키의 {q:정답}, {q:배점}. 공백무시 매칭.

    ⚠️ exam_id 필터 필수 — keys/ 에 고3(202606043)·고1(202606041) 등 여러 시험이
    공존하면 과목명만으로는 다른 학년 키가 잡힌다(2026-07-03 실제 회귀 사례).
    """
    best = None
    for p in Path(keys_dir).glob("*.json"):
        try:
            k = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if exam_id and str(k.get("exam_id") or k.get("irecord") or "") != str(exam_id):
            continue
        if _norm(k.get("subject")) != _norm(subject):
            continue
        k_el = _norm(k.get("elective"))
        el = _norm(elective)
        if el and k_el and k_el != el:
            continue
        ans, pts = _key_ans_pts(k)
        if ans:
            # 선택과목 정확일치 > complete > 문항수 (elective 일치가 최우선)
            score = (bool(el and k_el == el), bool(k.get("complete")), len(ans))
            if best is None or score > best[0]:
                best = (score, ans, pts)
    return (best[1], best[2]) if best else ({}, {})


def _std_ident(row):
    return dict(반=row.get("반", ""), 번호=row.get("번호", ""), 이름=row.get("성명", ""))


def _ident_part(value) -> str:
    """CSV 식별값을 비교 가능한 문자열로 정규화한다."""
    text = str(value or "").strip()
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def _valid_bb(ident) -> tuple[str, str] | None:
    """완전한 (반, 번호)만 반환. 판독 불확실 표시는 학생 키로 쓰지 않는다."""
    bb = (_ident_part(ident.get("반")), _ident_part(ident.get("번호")))
    return bb if all(bb) and "?" not in "".join(bb) else None


def _row_stu_answers(row, limit=60):
    """CSV 행의 문항 컬럼('1'..'60')에서 학생답 추출.

    정답 공개 전에도 모든 문항 셀을 보여준다. 특히 수학 단답 미마킹은 원시
    CSV 에 빈 문자열로 남을 수 있으므로 0(미마킹)으로 정규화한다.
    """
    out = {}
    for key, v in row.items():
        if not str(key).isdigit():
            continue
        q = int(key)
        if not 1 <= q <= limit:
            continue
        s = str(v).strip() if v is not None else ""
        out[q] = int(s) if s.lstrip("-").isdigit() else 0
    return out


def _jeongo_questions(ans, row):
    """정오 dict 를 만들 문항 목록 — 키가 있으면 키 기준, 없으면 CSV 학생답 기준."""
    return sorted(ans) if ans else sorted(_row_stu_answers(row))


def consolidate(args) -> list[dict]:
    keys_dir = Path(args.keys_dir)
    exam_id = getattr(args, "exam_id", None)
    students: dict[str, dict] = {}

    def get_student(ident, subject_field):
        """과목 행을 학생에 연결한다.

        1) 완전한 (반,번호)가 기존 학생과 같으면 확정 매칭
        2) 번호가 다르더라도 같은 이름의 후보가 딱 하나이고, 아직 이 과목이
           들어오지 않았다면 수험번호 오독으로 보고 성명 보완 매칭
        3) 같은 과목에 같은 이름이 이미 있으면 동명이인이므로 새 학생 생성
        """
        bb = _valid_bb(ident)
        name = str(ident.get("이름") or "").strip()
        s = None
        if bb:
            exact = [candidate for candidate in students.values()
                     if _valid_bb(candidate) == bb]
            if exact:
                s = exact[0]
        if s is None and name:
            named = [candidate for candidate in students.values()
                     if str(candidate.get("이름") or "").strip() == name
                     and subject_field not in candidate]
            if len(named) == 1:
                s = named[0]
        if s is None:
            base = (f"id:{bb[0]}-{bb[1]}" if bb else
                    f"name:{name}" if name else "unknown")
            key, suffix = base, 2
            while key in students:
                key = f"{base}#{suffix}"
                suffix += 1
            s = dict(학년=args.grade, 반=ident["반"], 번호=ident["번호"],
                     이름=ident["이름"], 계열번호="")
            students[key] = s
        else:
            # 같은 학생의 다른 과목에서 더 완전한 식별값을 읽었다면 보강한다.
            for field in ("반", "번호", "이름"):
                if not s.get(field) and ident.get(field):
                    s[field] = ident[field]
        return s

    def _clean_num(v):
        f = float(v)
        return int(f) if f.is_integer() else round(f, 1)

    def parse_pandokpyo(row, subject, elective=None):
        """판독표 포맷(점수/만점/문항별 학생답) → (선택, 공통원, 선택원, 원점수, 만점, 정오).
        고1·2 국어/수학 등 선택과목 없는 양식: 전 문항을 '공통'으로 본다.
        정답키가 없으면(공개 전) 학생답만 정오에 담고 점수는 공란."""
        ans, pts = _load_key_points(keys_dir, subject, elective, exam_id)
        jeongo, total = {}, 0.0
        for q in _jeongo_questions(ans, row):
            stu = row.get(str(q))
            stu = int(stu) if str(stu).lstrip("-").isdigit() else 0
            ok = stu == ans[q] if q in ans else False
            jeongo[q] = {"답": stu, "정답": ans.get(q), "배점": pts.get(q, 0), "ok": ok}
            if ok:
                total += pts.get(q, 0)
        score = row.get("점수")
        if str(score).replace(".", "").isdigit():
            score = _clean_num(score)
        else:
            score = _clean_num(total) if ans else ""
        return dict(선택=subject, 공통원=score, 선택원="", 원점수=score,
                    만점=_clean_num(row.get("만점") or 100) if ans else "", 정오=jeongo,
                    확인=row.get("확인필요", ""), 페이지=row.get("페이지", ""))

    # ── 국어: 점수표(고3, 원점수+선택과목) 또는 판독표(고1·2) 자동 감지
    for row in _read_csv(args.korean):
        ident = _std_ident(row)
        s = get_student(ident, "국어")
        if "원점수" in row:                       # 고3 점수표 포맷
            elective = row.get("선택과목") or "국어"
            ans, pts = _load_key_points(keys_dir, "국어", elective, exam_id)
            wrong = set(_ints(row.get("틀린문항"))) | set(_ints(row.get("미마킹"))) | set(_ints(row.get("중복마킹")))
            jeongo, gong, sun = {}, 0, 0
            for q in (sorted(pts) if pts else sorted(_row_stu_answers(row))):
                pt = pts.get(q, 0)
                ok = (q not in wrong) if pts else False
                stu = row.get(str(q))              # 점수표 CSV의 문항별 학생답(있으면)
                stu = int(stu) if str(stu).lstrip("-").isdigit() else 0
                jeongo[q] = {"답": stu, "정답": ans.get(q), "배점": pt, "ok": ok}
                if ok:
                    if q <= 34:
                        gong += pt
                    else:
                        sun += pt
            s["국어"] = dict(선택=elective, 공통원=gong or "", 선택원=sun or "",
                            원점수=int(row.get("원점수") or 0) if pts else "",
                            만점=int(row.get("만점") or 100) if pts else "",
                            정오=jeongo, 확인=row.get("확인필요", ""), 페이지=row.get("페이지", ""))
        else:                                     # 고1·2 판독표 포맷
            s["국어"] = parse_pandokpyo(row, "국어")

    # ── 수학: 점수표(고3) 또는 판독표(고1·2) 자동 감지
    for row in _read_csv(args.math):
        ident = _std_ident(row)
        s = get_student(ident, "수학")
        if "원점수" in row:                       # 고3 점수표 포맷
            elective = row.get("선택과목") or "수학"
            ans, pts = _load_key_points(keys_dir, "수학", elective, exam_id)
            wrong = set(_ints(row.get("틀린문항")))
            jeongo, gong, sun = {}, 0, 0
            for q in (sorted(pts) if pts else sorted(_row_stu_answers(row))):
                pt = pts.get(q, 0)
                ok = (q not in wrong) if pts else False
                stu = row.get(str(q))              # 점수표 CSV의 문항별 학생답(있으면)
                stu = int(stu) if str(stu).lstrip("-").isdigit() else 0
                jeongo[q] = {"답": stu, "정답": ans.get(q), "배점": pt, "ok": ok}
                if ok:
                    if q <= 22:
                        gong += pt
                    else:
                        sun += pt
            s["수학"] = dict(선택=elective, 공통원=gong or "", 선택원=sun or "",
                            원점수=int(row.get("원점수") or 0) if pts else "",
                            만점=int(row.get("만점") or 100) if pts else "",
                            정오=jeongo, 확인=row.get("확인필요", ""), 페이지=row.get("페이지", ""))
        else:                                     # 고1·2 판독표 포맷
            s["수학"] = parse_pandokpyo(row, "수학")

    # ── 영어 (판독표): 원점수 + 문항별 정오(학생답 + 정답대조)
    for row in _read_csv(args.english):
        ident = _std_ident(row)
        s = get_student(ident, "영어")
        ans, pts = _load_key_points(keys_dir, "영어", None, exam_id)
        score_col = row.get("부분점수") or row.get("점수") or 0
        jeongo = {}
        for q in _jeongo_questions(ans, row):
            stu = row.get(str(q))
            stu = int(stu) if str(stu).lstrip("-").isdigit() else 0
            jeongo[q] = {"답": stu, "정답": ans.get(q), "배점": pts.get(q, 0),
                         "ok": stu == ans[q] if q in ans else False}
        s["영어"] = dict(원점수=_num_score(score_col) if ans else "",
                        만점=_num_score(row.get("부분만점") or row.get("만점"), 100) if ans else "",
                        정오=jeongo, 확인=row.get("확인필요", ""), 페이지=row.get("페이지", ""))

    # ── 한국사 (판독표)
    for row in _read_csv(args.history):
        ident = _std_ident(row)
        s = get_student(ident, "한국사")
        ans, pts = _load_key_points(keys_dir, "한국사", None, exam_id)
        jeongo = {}
        for q in _jeongo_questions(ans, row):
            stu = row.get(str(q))
            stu = int(stu) if str(stu).lstrip("-").isdigit() else 0
            jeongo[q] = {"답": stu, "정답": ans.get(q), "배점": pts.get(q, 0),
                         "ok": stu == ans[q] if q in ans else False}
        s["한국사"] = dict(원점수=_num_score(row.get("점수")) if ans else "",
                          만점=_num_score(row.get("만점"), 50) if ans else "",
                          정오=jeongo, 확인=row.get("확인필요", ""), 페이지=row.get("페이지", ""))

    # ── 탐구 (판독표): 제1·제2 선택
    for row in _read_csv(args.explore):
        ident = _std_ident(row)
        s = get_student(ident, "탐구")
        tam = []
        for pre in ("제1선택", "제2선택"):
            subj = row.get(f"{pre}과목")
            if not subj:
                continue
            ans, pts = _load_key_points(keys_dir, subj, None, exam_id)
            qs = sorted(ans) if ans else sorted(
                q for q in range(1, 21)
                if str(row.get(f"{pre}_{q}", "")).strip().lstrip("-").isdigit())
            jeongo = {}
            for q in qs:
                stu = row.get(f"{pre}_{q}")
                stu = int(stu) if str(stu).lstrip("-").isdigit() else 0
                jeongo[q] = {"답": stu, "정답": ans.get(q), "배점": pts.get(q, 0),
                             "ok": stu == ans[q] if q in ans else False}
            tam.append(dict(과목=subj,
                            원점수=_num_score(row.get(f"{pre}점수")) if ans else "",
                            만점=_num_score(row.get(f"{pre}만점"), 50) if ans else "",
                            정오=jeongo,
                            확인=row.get("확인필요", ""), 페이지=row.get("페이지", "")))
        if tam:
            s["탐구"] = tam

    # 로스터 순서: 반, 번호
    def sort_key(s):
        return (str(s.get("반", "")), int(s["번호"]) if str(s.get("번호", "")).isdigit() else 0)

    return sorted(students.values(), key=sort_key)


def _attach_estimates(students: list[dict], exam_id: str | None, keys_dir) -> dict:
    """EBSi 등급컷으로 과목별 예상등급/표점/백분위 부착. 반환=등급컷 dict.

    고3 국어·수학은 EBSi 가 원점수컷을 비우므로(선택과목제) 예상등급이 공란인데,
    미맥의 선택과목별 원점수컷이 있으면 학생 선택과목에 맞춰 예상등급을 채운다.
    """
    if not exam_id:
        return {}
    cuts = gc.load_grade_cuts(exam_id, keys_dir)
    import mimac_cuts as mimac
    has_mimac = bool(mimac.load_g3_cuts(exam_id, keys_dir))
    if not cuts and not has_mimac:
        return {}
    for s in students:
        for subject in ("국어", "수학", "영어", "한국사"):
            d = s.get(subject)
            if not d:
                continue
            est = gc.estimate(gc.find_subject_cut(cuts, subject), d.get("원점수"))
            등급, 표점, 백분위 = est["등급"], est["표준점수"], est["백분위"]
            # 고3 국·수: EBSi 등급 공란이면 미맥 선택과목별 원점수컷으로 보강
            if subject in ("국어", "수학") and 등급 in ("", None):
                mg = mimac.mimac_g3_grade(exam_id, subject, d.get("선택", ""),
                                          d.get("원점수"), keys_dir)
                if mg != "":
                    등급 = mg
            d.update(예상등급=등급, 예상표준점수=표점, 예상백분위=백분위)
        for t in (s.get("탐구") or []):
            est = gc.estimate(gc.find_subject_cut(cuts, t.get("과목", "")), t.get("원점수"))
            t.update(예상등급=est["등급"], 예상표준점수=est["표준점수"], 예상백분위=est["백분위"])
    return cuts


def build_students(grade: int, keys_dir="keys", korean=None, math=None,
                   english=None, history=None, explore=None,
                   social=None, science=None, exam_id=None) -> list[dict]:
    """과목별 CSV → 학생 레코드 리스트 (편집 가능한 중간 상태). 예상등급까지 부착."""
    from types import SimpleNamespace
    args = SimpleNamespace(grade=grade, keys_dir=keys_dir, korean=korean, math=math,
                           english=english, history=history, explore=explore,
                           social=social, science=science, exam_id=exam_id)
    students = consolidate(args)
    _merge_integrated(args, students)   # 고1·2 통합사회/통합과학 → 탐구1/탐구2
    students.sort(key=lambda s: (str(s.get("반", "")),
                                 int(s["번호"]) if str(s.get("번호", "")).isdigit() else 0))
    _attach_estimates(students, exam_id, keys_dir)
    return students


def regrade_student_subject(s: dict, subject: str, exam_id, keys_dir):
    """편집 후 한 학생의 한 과목을 정오 dict 기준으로 재채점하고 예상등급 갱신."""
    d = s.get(subject) if subject in ("국어", "수학", "영어", "한국사") else None
    tam = None
    if d is None:
        for t in (s.get("탐구") or []):
            if t.get("과목") == subject:
                tam = t; d = t; break
    if not d:
        return
    jeongo = d.get("정오", {})
    has_key = any(cell.get("정답") is not None for cell in jeongo.values())
    total = 0.0
    gong = sun = 0.0
    for q, cell in jeongo.items():
        ans = cell.get("답")
        ok = (ans not in (0, -1, "", None)) and ans == cell.get("정답")
        cell["ok"] = ok
        pt = cell.get("배점", 0) or 0
        if ok:
            total += pt
            if subject == "국어":
                gong += pt if int(q) <= 34 else 0
                sun += pt if int(q) > 34 else 0
            elif subject == "수학":
                gong += pt if int(q) <= 22 else 0
                sun += pt if int(q) > 22 else 0
    def _clean(v):
        return int(v) if float(v).is_integer() else round(v, 1)
    # 정답 공개 전(전 셀 정답 None) — 수정해도 점수는 공란 유지
    d["원점수"] = _clean(total) if has_key else ""
    if subject in ("국어", "수학"):
        d["공통원"] = _clean(gong) if gong else ""
        d["선택원"] = _clean(sun) if sun else ""
    # 예상등급 갱신
    import mimac_cuts as mimac        # 함수 지역 import (모듈 상단에 없음)
    cuts = gc.load_grade_cuts(exam_id, keys_dir) if exam_id else {}
    est = gc.estimate(gc.find_subject_cut(cuts, subject), d.get("원점수"))
    등급, 표점, 백분위 = est["등급"], est["표준점수"], est["백분위"]
    if subject in ("국어", "수학") and 등급 in ("", None) and exam_id:
        mg = mimac.mimac_g3_grade(exam_id, subject, d.get("선택", ""), d.get("원점수"), keys_dir)
        if mg != "":
            등급 = mg
    d.update(예상등급=등급, 예상표준점수=표점, 예상백분위=백분위)


def workbook_from_students(students, out, exam_id=None, exam_year=None, keys_dir="keys") -> Path:
    """편집된 학생 레코드 → 통합 워크북."""
    if not students:
        raise RuntimeError("통합할 학생 데이터가 없습니다.")
    cuts = gc.load_grade_cuts(exam_id, keys_dir) if exam_id else {}
    return rx.build_workbook(students, out, exam_year=exam_year, cuts_by_subject=cuts)


def consolidate_paths(grade: int, keys_dir="keys", korean=None, math=None,
                      english=None, history=None, explore=None,
                      social=None, science=None,
                      out="output/통합성적표.xlsx", exam_year=None,
                      exam_id=None) -> Path:
    """과목별 CSV 경로 → 통합 워크북 경로 (학생생성+워크북 한 번에)."""
    if not exam_id:
        print("⚠️ exam_id(--irecord) 미지정 — 다른 시험 정답키가 잡힐 수 있습니다.")
    students = build_students(grade, keys_dir, korean, math, english, history,
                              explore, social, science, exam_id)
    if not students:
        raise RuntimeError("통합할 학생 데이터가 없습니다 (CSV 경로 확인).")
    return workbook_from_students(students, out, exam_id, exam_year, keys_dir)


def main() -> int:
    ap = argparse.ArgumentParser(description="과목별 CSV 통합 → 통합 성적 워크북")
    ap.add_argument("--grade", type=int, required=True, choices=[1, 2, 3])
    ap.add_argument("--keys-dir", default="keys")
    ap.add_argument("--korean")
    ap.add_argument("--math")
    ap.add_argument("--english")
    ap.add_argument("--history")
    ap.add_argument("--explore", help="탐구 판독표(고3 2선택). 고1·2 통합사회/통합과학은 --social/--science 사용")
    ap.add_argument("--social", help="통합사회 판독표(고1·2)")
    ap.add_argument("--science", help="통합과학 판독표(고1·2)")
    ap.add_argument("--exam-year", default=None)
    ap.add_argument("--irecord", "--exam-id", dest="exam_id", default=None,
                    help="정답키 시험 ID (keys 에 여러 시험 공존 시 필수)")
    ap.add_argument("--out", default="output/통합성적표.xlsx")
    args = ap.parse_args()

    out = consolidate_paths(args.grade, args.keys_dir, args.korean, args.math,
                            args.english, args.history, args.explore,
                            args.social, args.science, args.out, args.exam_year,
                            exam_id=args.exam_id)
    print(f"통합 완료 → {out}")
    return 0


def _merge_integrated(args, students):
    """고1·2 통합사회/통합과학 판독표를 학생 탐구[0]/탐구[1] 로 편입.

    탐구 CSV 에만 있는 학생도 레코드를 생성한다 (무경고 탈락 방지).
    매칭은 정확한 (반,번호) → 해당 통합과목이 비어 있는 유일 성명 순으로 시도.
    """
    keys_dir = Path(args.keys_dir)
    exam_id = getattr(args, "exam_id", None)
    by_name = {}
    for s in students:
        if s.get("이름"):
            by_name.setdefault(s["이름"], []).append(s)
    by_bb = {_valid_bb(s): s for s in students if _valid_bb(s)}

    def find_or_create(ident, slot):
        bb = _valid_bb(ident)
        s = by_bb.get(bb) if bb else None
        if not s and ident["이름"]:
            candidates = []
            for candidate in by_name.get(ident["이름"], []):
                tam = candidate.get("탐구") or []
                if len(tam) <= slot or tam[slot] is None:
                    candidates.append(candidate)
            if len(candidates) == 1:
                s = candidates[0]
        if not s:
            s = dict(학년=args.grade, 반=ident["반"], 번호=ident["번호"],
                     이름=ident["이름"], 계열번호="")
            students.append(s)
            if ident["이름"]:
                by_name.setdefault(ident["이름"], []).append(s)
            if bb:
                by_bb[bb] = s
        return s

    def add(path, subject, slot):
        for row in _read_csv(path):
            ident = _std_ident(row)
            s = find_or_create(ident, slot)
            ans, pts = _load_key_points(keys_dir, subject, None, exam_id)
            jeongo = {}
            for q in _jeongo_questions(ans, row):
                stu = row.get(str(q))
                stu = int(stu) if str(stu).lstrip("-").isdigit() else 0
                jeongo[q] = {"답": stu, "정답": ans.get(q), "배점": pts.get(q, 0),
                             "ok": stu == ans[q] if q in ans else False}
            entry = dict(과목=subject,
                         원점수=_num_score(row.get("점수") or row.get("원점수")) if ans else "",
                         만점=_num_score(row.get("만점"), 100) if ans else "", 정오=jeongo)
            s.setdefault("탐구", [None, None])
            while len(s["탐구"]) < 2:
                s["탐구"].append(None)
            s["탐구"][slot] = entry

    if args.social:
        add(args.social, "통합사회", 0)
    if args.science:
        add(args.science, "통합과학", 1)
    for s in students:
        if "탐구" in s:
            s["탐구"] = [t for t in s["탐구"] if t]


if __name__ == "__main__":
    raise SystemExit(main())
