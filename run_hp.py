# -*- coding: utf-8 -*-
"""고3 학평(전국연합) 과목별 판독·채점 — 웹앱용 러너.

    python run_hp.py <scan.pdf> --subject 국어 --keys-dir keys \\
        --irecord 202607083 --out output/... [--names 명단.csv] [--workers 6]

- 페이지 병렬(multiprocessing): 렌더→정렬(rectify_verified)→판독을 페이지
  단위로 나눠 코어 수만큼 동시 처리.
- 진행률을 "PROGRESS n/total" 로 stdout 에 흘려 웹이 실시간 표시.
- 산출: consolidate.build_students 가 그대로 읽는 CSV(기존 평가원 러너와
  동일 포맷) + 사람이 보는 판독표 xlsx. → 통합성적표·결과편집 UI 재사용.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from multiprocessing import Pool, cpu_count
from pathlib import Path

import cv2
import numpy as np
import fitz

import hp_align
import hp_g3
import hp_run

HERE = Path(__file__).resolve().parent

SUBJ_SETUP = {  # 과목 라벨 → (템플릿들, hp_run kind, csv 파일명 suffix)
    "국어": (["korean_g3hp"], "korean", "_점수표.csv"),
    "수학": (["math_g3hp"], "math", "_수학_점수표.csv"),
    "영어": (["english_g3hp"], "english", "_영어_판독표.csv"),
    "한국사": (["history_g3hp"], "history", "_한국사_판독표.csv"),
    "탐구": (["expl1_g3hp", "expl2_g3hp"], "explore", "_탐구_판독표.csv"),
}

# ── 페이지 워커 (Windows spawn 안전: 모듈 전역 + initializer) ─────
_W: dict = {}


def _winit(pdf_path: str, tnames: list[str], kind: str):
    _W["doc"] = fitz.open(pdf_path)
    _W["tmpls"] = [json.loads((HERE / "templates" / f"{n}.json").read_text(encoding="utf-8"))
                   for n in tnames]
    _W["kind"] = kind


def _wpage(i: int) -> dict:
    pix = _W["doc"][i].get_pixmap(dpi=300)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR if pix.n == 4 else cv2.COLOR_RGB2BGR)
    try:
        out, _ = hp_g3.rectify_verified(img, _W["tmpls"][0])
    except hp_align.AlignError as e:
        return dict(page=i, error=f"정렬실패:{e}")
    rec = hp_run.read_page(_W["kind"], _W["tmpls"], out)
    rec["page"] = i
    return rec


def read_pdf(pdf: Path, tnames: list[str], kind: str, workers: int) -> list[dict]:
    doc = fitz.open(str(pdf))
    n = doc.page_count
    doc.close()
    recs = []
    if workers <= 1:
        _winit(str(pdf), tnames, kind)
        for i in range(n):
            recs.append(_wpage(i))
            print(f"PROGRESS {i + 1}/{n}", flush=True)
        _W["doc"].close()
    else:
        with Pool(processes=workers, initializer=_winit,
                  initargs=(str(pdf), tnames, kind)) as pool:
            for k, rec in enumerate(pool.imap_unordered(_wpage, range(n)), 1):
                recs.append(rec)
                print(f"PROGRESS {k}/{n}", flush=True)
    recs.sort(key=lambda r: r["page"])
    return recs


# ── 채점·CSV 행 구성 ──────────────────────────────────────────────
def _load_names(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ban = str(row.get("반", "")).strip().lstrip("0")
            num = str(row.get("번호", "")).strip().lstrip("0")
            if ban and num:
                out[(ban, num)] = str(row.get("성명", "")).strip()
    return out


def _ident(rec: dict, names: dict) -> dict:
    sid = rec.get("id") or {}
    ban = "" if sid.get("ban") is None else str(sid["ban"])
    num = "" if sid.get("num") is None else str(sid["num"])
    return {"반": ban, "번호": num, "성명": names.get((ban, num), "")}


def _flags(rec: dict, ident: dict) -> list[str]:
    fl = []
    sid = rec.get("id") or {}
    if rec.get("error"):
        fl.append(rec["error"])
    if sid.get("school") not in (None, 19718):
        fl.append(f"학교번호이상:{sid.get('school')}")
    if not ident["반"] or not ident["번호"]:
        fl.append("수험번호불완전")
    return fl


def _ans_str(v) -> str:
    return "" if v is None else str(v)


def rows_for(kind: str, recs: list[dict], keys: dict, names: dict) -> list[dict]:
    rows = []
    for rec in recs:
        if rec.get("error"):
            rows.append({"반": "", "번호": "", "성명": f"⚠판독실패 p{rec['page'] + 1}",
                         "확인필요": rec["error"]})
            continue
        ident = _ident(rec, names)
        main = rec.get("ans") or rec.get("ans1") or {}
        if hp_run.is_blank(main) and not ident["반"] and not ident["번호"] \
                and (kind != "explore" or hp_run.is_blank(rec.get("ans2", {0: 0}))):
            continue                       # 백지(결시) 카드 — 명부에서 제외
        fl = _flags(rec, ident)
        row = dict(ident)
        if kind == "history":
            key = keys["history"]
            sc = hp_run.score_simple(rec["ans"], key)
            wrong = [q for q, a in rec["ans"].items() if a != int(key["answers"][str(q)])]
            row.update(점수=sc, 만점=50)
            row.update({str(q): _ans_str(a) for q, a in rec["ans"].items()})
            _ = wrong
        elif kind == "english":
            sc = hp_run.score_simple(rec["ans"], keys["english"])
            row.update(점수=sc, 만점=100)
            row.update({str(q): _ans_str(a) for q, a in rec["ans"].items()})
        elif kind == "korean":
            ch = rec.get("choice")
            row.update({str(q): _ans_str(a) for q, a in rec["ans"].items()})
            if ch in keys["korean"]:
                key = keys["korean"][ch]
                sc = hp_run.score_simple(rec["ans"], key)
                wrong = sorted(q for q, a in rec["ans"].items()
                               if a not in (0, -1) and a != int(key["answers"][str(q)]))
                miss = sorted(q for q, a in rec["ans"].items() if a == 0)
                dup = sorted(q for q, a in rec["ans"].items() if a == -1)
                row.update(선택과목=ch, 원점수=sc, 만점=100,
                           틀린문항=" ".join(map(str, wrong)),
                           미마킹=" ".join(map(str, miss)),
                           중복마킹=" ".join(map(str, dup)))
            else:
                fl.append(f"선택미표기:{ch or ''}")
                row.update(선택과목=ch or "", 원점수=0, 만점=100)
        elif kind == "math":
            ch = rec.get("choice")
            for q in range(1, 31):
                v = rec["ans"].get(q) if (q <= 15 or 23 <= q <= 28) else rec["sa"].get(q)
                row[str(q)] = _ans_str(v)
            if ch in keys["math"]:
                qs = keys["math"][ch]["questions"]
                sc, wrong = 0, []
                for q, meta in qs.items():
                    qn = int(q)
                    got = (rec["ans"].get(qn) if meta["type"] == "objective"
                           else rec["sa"].get(qn))
                    got = None if got == -1 else got
                    if got == meta["answer"]:
                        sc += int(meta["points"])
                    else:
                        wrong.append(qn)
                row.update(선택과목=ch, 원점수=sc, 만점=100,
                           틀린문항=" ".join(map(str, sorted(wrong))))
                if any(v == -1 for v in rec["sa"].values()):
                    fl.append("단답중복")
            else:
                fl.append(f"선택미표기:{ch or ''}")
                row.update(선택과목=ch or "", 원점수=0, 만점=100)
        else:  # explore
            for k in (1, 2):
                ans, code = rec[f"ans{k}"], rec[f"code{k}"]
                pre = f"제{k}선택"
                if hp_run.is_blank(ans) and code is None:
                    continue               # 해당 선택 미응시
                if code not in keys["explore"]:
                    fl.append(f"탐{k}코드이상:{code}")
                    continue
                name, key = keys["explore"][code]
                sc = hp_run.score_simple(ans, key)
                row[f"{pre}과목"] = name
                row[f"{pre}점수"] = sc
                row[f"{pre}만점"] = 50
                row.update({f"{pre}_{q}": _ans_str(a) for q, a in ans.items()})
        row["확인필요"] = ";".join(fl)
        rows.append(row)
    rows.sort(key=lambda r: (r["반"] == "", str(r["반"]),
                             int(r["번호"]) if str(r["번호"]).isdigit() else 0))
    return rows


def write_outputs(rows: list[dict], out_dir: Path, stem: str, suffix: str, label: str):
    fields = []
    for r in rows:                          # 열 순서 보존 합집합
        for k in r:
            if k not in fields:
                fields.append(k)
    csv_path = out_dir / f"{stem}{suffix}"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    # 판독표 xlsx (다운로드 목록용)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = label
    ws.append(fields)
    for r in rows:
        ws.append([r.get(c, "") for c in fields])
    xlsx_path = out_dir / f"{stem}_{label}_판독표.xlsx"
    wb.save(xlsx_path)
    return csv_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--subject", required=True, choices=list(SUBJ_SETUP))
    ap.add_argument("--keys-dir", type=Path, default=HERE / "keys")
    ap.add_argument("--irecord", default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--names", type=Path, default=None)
    ap.add_argument("--dpi", type=int, default=300)      # 웹 호환(현재 300 고정 렌더)
    ap.add_argument("--template", default=None)          # 웹 호환(템플릿은 과목으로 결정)
    ap.add_argument("--workers", type=int,
                    default=max(1, min(6, (cpu_count() or 4) - 2)))
    a = ap.parse_args()

    tnames, kind, suffix = SUBJ_SETUP[a.subject]
    if not a.irecord:
        print("경고: --irecord 미지정 — 정답키 매칭이 모호할 수 있습니다.", flush=True)
    keys = hp_run.load_keys(a.keys_dir, a.irecord or "")
    names = _load_names(a.names)
    a.out.mkdir(parents=True, exist_ok=True)

    print(f"{a.subject}: 학평 카드 판독 시작 ({a.pdf.name}, workers={a.workers})", flush=True)
    recs = read_pdf(a.pdf, tnames, kind, a.workers)
    rows = rows_for(kind, recs, keys, names)
    csv_path = write_outputs(rows, a.out, a.pdf.stem, suffix, a.subject)

    nfail = sum(1 for r in recs if r.get("error"))
    nflag = sum(1 for r in rows if r.get("확인필요"))
    bans = Counter(r["반"] for r in rows if r["반"])
    print(f"{a.subject}: {len(recs)}쪽 → 학생 {len(rows)}명 "
          f"(정렬실패 {nfail}, 확인필요 {nflag})", flush=True)
    print(f"반 분포: {dict(sorted(bans.items(), key=lambda x: (len(x[0]), x[0])))}", flush=True)
    print(f"→ {csv_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
