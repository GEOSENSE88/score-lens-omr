# -*- coding: utf-8 -*-
"""고3 학평(인천 전국연합) 전과목 판독·채점·통합 성적 산출.

    python hp_run.py --scan-dir "D:\\...\\2026. 7월 전국연합학력평가" \\
        --exam 202607083 --out output/hp_july

과목폴더(한국사/국어/수학/영어/탐구)별 PDF(반 단위)를 정렬(hp_align)→판독
(hp_g3/hp_id)→채점(EBSi 키)하고, (반, 번호) 기준으로 통합 성적표(xlsx)와
검토목록(csv)을 만든다. 정렬 실패·수험번호 불완전·선택 미표기 등은 전부
플래그로 드러낸다(조용한 오판독 금지).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import fitz

import hp_align
import hp_g3
import hp_id
import grade_cuts as gc

HERE = Path(__file__).resolve().parent

SUBJECTS = {  # 폴더명 → (템플릿, 종류)
    "한국사": (["history_g3hp"], "history"),
    "국어": (["korean_g3hp"], "korean"),
    "수학": (["math_g3hp"], "math"),
    "영어": (["english_g3hp"], "english"),
    "탐구": (["expl1_g3hp", "expl2_g3hp"], "explore"),
}
EXPL_CODES = {11: "생활과윤리", 12: "윤리와사상", 13: "한국지리", 14: "세계지리",
              15: "동아시아사", 16: "세계사", 17: "경제", 18: "정치와법", 19: "사회문화",
              20: "물리학Ⅰ", 21: "화학Ⅰ", 22: "생명과학Ⅰ", 23: "지구과학Ⅰ",
              24: "물리학Ⅱ", 25: "화학Ⅱ", 26: "생명과학Ⅱ", 27: "지구과학Ⅱ"}
HIST_CUTS = [40, 35, 30, 25, 20, 15, 10, 5]      # 한국사 절대등급(50점)
ENG_CUTS = [90, 80, 70, 60, 50, 40, 30, 20]      # 영어 절대등급(100점)


def _load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def load_keys(keys_dir: Path, exam: str) -> dict:
    """과목종류별 정답키 적재."""
    k = {}
    k["history"] = _load_json(keys_dir / f"{exam}_한국사_xip_api.json")
    k["english"] = _load_json(keys_dir / f"{exam}_영어_xip_api.json")
    k["korean"] = {el: _load_json(keys_dir / f"{exam}_국어_{el}.json")
                   for el in ("화법과작문", "언어와매체")}
    k["math"] = {el: _load_json(keys_dir / f"{exam}_수학_{el}.json")
                 for el in ("확률과통계", "미적분", "기하")}
    k["explore"] = {}
    for code, name in EXPL_CODES.items():
        p = keys_dir / f"{exam}_{name}_xip_api.json"
        if p.exists():
            k["explore"][code] = (name, _load_json(p))
    return k


def score_simple(ans: dict[int, int], key: dict) -> int:
    answers, points = key["answers"], key["points"]
    sc = 0
    for q, a in ans.items():
        qs = str(q)
        if qs in answers and a == int(answers[qs]):
            sc += int(points[qs])
    return sc


def abs_grade(score: int, cuts: list[int]) -> int:
    return next((g for g, c in enumerate(cuts, 1) if score >= c), 9)


def rectified(pdf: Path, tmpl: dict):
    doc = fitz.open(str(pdf))
    for i in range(doc.page_count):
        pix = doc[i].get_pixmap(dpi=300)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR if pix.n == 4 else cv2.COLOR_RGB2BGR)
        try:
            out, info = hp_g3.rectify_verified(img, tmpl)
            yield i, out, info
        except hp_align.AlignError as e:
            yield i, None, str(e)
    doc.close()


def grid_of(tmpl: dict, name: str) -> dict | None:
    return next((g for g in tmpl.get("grids", []) if g["name"] == name), None)


def sel_of(tmpl: dict, name: str) -> dict | None:
    return next((s for s in tmpl.get("selects", []) if s["name"] == name), None)


def read_page(kind: str, tmpls: list[dict], img: np.ndarray) -> dict:
    """페이지 1장 판독 → {id, choice/codes, ans(들), sa(단답)}."""
    gray = hp_g3.pencil_gray(img)
    rec: dict = {}
    idg = grid_of(tmpls[0], "id")
    if idg:
        rec["id"] = hp_id.read_id(gray, idg)
    if kind == "korean":
        rec["choice"] = hp_id.read_select(gray, sel_of(tmpls[0], "choice"))
        rec["ans"] = hp_g3.read_objective(img, tmpls[0])
    elif kind == "math":
        rec["choice"] = hp_id.read_select(gray, sel_of(tmpls[0], "choice"))
        rec["ans"] = hp_g3.read_objective(img, tmpls[0])
        rec["sa"] = {int(g["name"][2:]): hp_id.read_short_answer(gray, g)
                     for g in tmpls[0]["grids"] if g["name"].startswith("sa")}
    elif kind == "explore":
        rec["ans1"] = hp_g3.read_objective(img, tmpls[0])
        rec["ans2"] = hp_g3.read_objective(img, tmpls[1])
        for i, tm in enumerate(tmpls, 1):
            cg = grid_of(tm, "code")
            d = hp_id.read_digit_grid(gray, cg)
            rec[f"code{i}"] = (None if any(v is None or v == -1 for v in d)
                               else d[0] * 10 + d[1])
    else:  # history / english
        rec["ans"] = hp_g3.read_objective(img, tmpls[0])
    return rec


def is_blank(ans: dict[int, int]) -> bool:
    return all(v == 0 for v in ans.values())


def process_subject(subj: str, scan_dir: Path, keys: dict, cuts: dict) -> list[dict]:
    tnames, kind = SUBJECTS[subj]
    tmpls = [_load_json(HERE / "templates" / f"{n}.json") for n in tnames]
    rows = []
    for pdf in sorted((scan_dir / subj).glob("*.pdf")):
        ban_expect = pdf.stem.split("-")[-1]
        for i, img, info in rectified(pdf, tmpls[0]):
            row = dict(과목=subj, 파일=pdf.stem, 페이지=i, 플래그=[])
            if img is None:
                row["플래그"].append(f"정렬실패:{info}")
                rows.append(row)
                continue
            rec = read_page(kind, tmpls, img)
            sid = rec.get("id") or {}
            row.update(학교=sid.get("school"), 반=sid.get("ban"), 번호=sid.get("num"))
            # 백지(결시) 카드
            main_ans = rec.get("ans") or rec.get("ans1") or {}
            if is_blank(main_ans) and (kind != "explore" or is_blank(rec.get("ans2", {0: 0}))) \
                    and row["반"] is None and row["번호"] is None:
                row["플래그"].append("백지카드")
                rows.append(row)
                continue
            if sid.get("school") != 19718:
                row["플래그"].append(f"학교번호이상:{sid.get('school')}")
            if sid.get("grade") not in (None, 3):
                row["플래그"].append(f"학년이상:{sid.get('grade')}")
            if row["반"] is None or row["번호"] is None:
                row["플래그"].append("수험번호불완전")
            elif str(row["반"]) != ban_expect:
                row["플래그"].append(f"반≠파일({row['반']}≠{ban_expect})")
            # 채점
            if kind == "history":
                sc = score_simple(rec["ans"], keys["history"])
                row.update(선택="", 원점수=sc, 등급=abs_grade(sc, HIST_CUTS))
            elif kind == "english":
                sc = score_simple(rec["ans"], keys["english"])
                row.update(선택="", 원점수=sc, 등급=abs_grade(sc, ENG_CUTS))
            elif kind == "korean":
                ch = rec["choice"]
                if ch in keys["korean"]:
                    sc = score_simple(rec["ans"], keys["korean"][ch])
                    est = gc.estimate(gc.find_subject_cut(cuts, "국어"), sc)
                    row.update(선택=ch, 원점수=sc, 등급=est.get("등급"))
                else:
                    row["플래그"].append(f"선택미표기:{ch}")
                    row.update(선택=ch or "", 원점수=None, 등급=None)
            elif kind == "math":
                ch = rec["choice"]
                if ch in keys["math"]:
                    qs = keys["math"][ch]["questions"]
                    sc = 0
                    for q, meta in qs.items():
                        qn = int(q)
                        if meta["type"] == "objective":
                            got = rec["ans"].get(qn)
                        else:
                            got = rec["sa"].get(qn)
                            got = None if got in (None, -1) else got
                        if got == meta["answer"]:
                            sc += int(meta["points"])
                    est = gc.estimate(gc.find_subject_cut(cuts, "수학"), sc)
                    row.update(선택=ch, 원점수=sc, 등급=est.get("등급"))
                    ndup = sum(1 for v in rec["sa"].values() if v == -1)
                    if ndup:
                        row["플래그"].append(f"단답중복:{ndup}")
                else:
                    row["플래그"].append(f"선택미표기:{ch}")
                    row.update(선택=ch or "", 원점수=None, 등급=None)
            else:  # explore — 선택 1·2 각각
                for k in (1, 2):
                    ans, code = rec[f"ans{k}"], rec[f"code{k}"]
                    if is_blank(ans) and code is None:
                        row.update({f"탐{k}과목": "", f"탐{k}원점수": None, f"탐{k}등급": None})
                        continue
                    if code not in keys["explore"]:
                        row["플래그"].append(f"탐{k}코드이상:{code}")
                        row.update({f"탐{k}과목": str(code), f"탐{k}원점수": None, f"탐{k}등급": None})
                        continue
                    name, key = keys["explore"][code]
                    sc = score_simple(ans, key)
                    est = gc.estimate(gc.find_subject_cut(cuts, name), sc)
                    row.update({f"탐{k}과목": name, f"탐{k}원점수": sc, f"탐{k}등급": est.get("등급")})
            # 이상치 통계
            n0 = sum(1 for v in main_ans.values() if v == 0)
            nd = sum(1 for v in main_ans.values() if v == -1)
            if nd:
                row["플래그"].append(f"중복마킹:{nd}")
            row.update(미마킹=n0)
            rows.append(row)
        done = [r for r in rows if r["파일"] == pdf.stem]
        nf = sum(1 for r in done if r["플래그"])
        print(f"{subj}/{pdf.stem}: {len(done)}쪽, 플래그 {nf}건", flush=True)
    return rows


def consolidate(all_rows: dict[str, list[dict]], out_dir: Path):
    """(반, 번호) 기준 통합 성적표 xlsx + 검토목록 csv."""
    students: dict[tuple, dict] = {}
    review = []
    for subj, rows in all_rows.items():
        for r in rows:
            if r["플래그"]:
                review.append(dict(과목=r["과목"], 파일=r["파일"], 페이지=r["페이지"],
                                   반=r.get("반"), 번호=r.get("번호"),
                                   플래그=";".join(r["플래그"])))
            if r.get("반") is None or r.get("번호") is None:
                continue
            st = students.setdefault((r["반"], r["번호"]), {"반": r["반"], "번호": r["번호"]})
            if subj == "탐구":
                for k in (1, 2):
                    if r.get(f"탐{k}과목"):
                        st[f"탐구{k}과목"] = r[f"탐{k}과목"]
                        st[f"탐구{k}"] = r[f"탐{k}원점수"]
                        st[f"탐구{k}등급"] = r[f"탐{k}등급"]
            elif r.get("원점수") is not None:
                st[subj] = r["원점수"]
                st[f"{subj}등급"] = r.get("등급")
                if r.get("선택"):
                    st[f"{subj}선택"] = r["선택"]
    cols = ["반", "번호", "국어선택", "국어", "국어등급", "수학선택", "수학", "수학등급",
            "영어", "영어등급", "한국사", "한국사등급",
            "탐구1과목", "탐구1", "탐구1등급", "탐구2과목", "탐구2", "탐구2등급"]
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "통합"
    ws.append(cols)
    for key in sorted(students):
        st = students[key]
        ws.append([st.get(c) for c in cols])
    wb.save(out_dir / "통합성적.xlsx")
    with open(out_dir / "검토목록.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["과목", "파일", "페이지", "반", "번호", "플래그"])
        w.writeheader()
        w.writerows(review)
    print(f"통합 {len(students)}명 → {out_dir/'통합성적.xlsx'}")
    print(f"검토 {len(review)}건 → {out_dir/'검토목록.csv'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-dir", type=Path, required=True)
    ap.add_argument("--exam", default="202607083")
    ap.add_argument("--out", type=Path, default=HERE / "output" / "hp_july")
    ap.add_argument("--subjects", nargs="*", default=list(SUBJECTS))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    keys = load_keys(HERE / "keys", a.exam)
    cuts = gc.load_grade_cuts(a.exam, HERE / "keys")   # {과목명: cut} 를 바로 반환
    all_rows = {}
    for subj in a.subjects:
        rows = process_subject(subj, a.scan_dir, keys, cuts)
        all_rows[subj] = rows
        fields = sorted({k for r in rows for k in r})
        with open(a.out / f"{subj}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({**r, "플래그": ";".join(r["플래그"])})
        # 자기검증 요약
        bans = Counter(r.get("반") for r in rows if r.get("반") is not None)
        dup = [(b, n, c) for (b, n), c in
               Counter((r.get("반"), r.get("번호")) for r in rows
                       if r.get("번호") is not None).items() if c > 1]
        print(f"[{subj}] 반 분포: {dict(sorted(bans.items()))}")
        if dup:
            print(f"[{subj}] ⚠️ (반,번호) 중복: {dup}")
    consolidate(all_rows, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
