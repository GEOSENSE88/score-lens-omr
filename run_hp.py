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
import hp_id
import hp_run

HERE = Path(__file__).resolve().parent

SUBJ_SETUP = {  # 과목 라벨 → (템플릿들, hp_run kind, csv 파일명 suffix)
    "국어": (["korean_g3hp"], "korean", "_점수표.csv"),
    "수학": (["math_g3hp"], "math", "_수학_점수표.csv"),
    "영어": (["english_g3hp"], "english", "_영어_판독표.csv"),
    "한국사": (["history_g3hp"], "history", "_한국사_판독표.csv"),
    "탐구": (["expl1_g3hp", "expl2_g3hp"], "explore", "_탐구_판독표.csv"),
}
# 충북교육청 모의평가 카드(--card cb) — 카드 스캔이 확보된 과목만 보정됨
SUBJ_SETUP_CB = {
    "국어": (["korean_g3cb"], "korean", "_점수표.csv"),
    "수학": (["math_g3cb"], "math", "_수학_점수표.csv"),
    "영어": (["english_g3cb"], "english", "_영어_판독표.csv"),
}
# 고1·2 전국연합 학평 카드(--card g12) — 2026-09 고2 백지 서식 실측 보정.
# 수험번호 9열(학교4+학년1+반2+번호2), 국어·수학은 선택과목 없음(공통),
# 수학 단답형은 22~30. CSV 는 판독표 포맷('점수' 컬럼) — consolidate 의
# 고1·2 자동 감지(parse_pandokpyo)와 정합.
SUBJ_SETUP_G12 = {
    "국어": (["korean_g12hp"], "korean_g12", "_국어_판독표.csv"),
    "수학": (["math_g12hp"], "math_g12", "_수학_판독표.csv"),
    "영어": (["english_g12hp"], "english", "_영어_판독표.csv"),
    "한국사": (["history_g12hp"], "history", "_한국사_판독표.csv"),
    "통합사회": (["social_g12hp"], "social", "_통합사회_판독표.csv"),
    "통합과학": (["science_g12hp"], "science", "_통합과학_판독표.csv"),
}

# ── 페이지 워커 (Windows spawn 안전: 모듈 전역 + initializer) ─────
_W: dict = {}


def _winit(pdf_path: str, tnames: list[str], kind: str, dpi: int = 300,
           img_dir: str | None = None):
    import os
    # 프로세스 병렬 × OpenCV 내부 스레드 과경쟁 조절 (실측상 1~4 차이는 노이즈
    # 수준 — vCPU 적은 서버에 안전한 1을 기본값으로)
    cv2.setNumThreads(int(os.environ.get("OMR_CV_THREADS", "1")))
    _W["doc"] = fitz.open(pdf_path)
    _W["tmpls"] = [json.loads((HERE / "templates" / f"{n}.json").read_text(encoding="utf-8"))
                   for n in tnames]
    _W["kind"] = kind
    _W["dpi"] = dpi
    _W["img_dir"] = img_dir


def _wsave_review(i: int, img: np.ndarray) -> None:
    """페이지 카드 이미지(JPEG) 저장 — 웹 '카드 보기'용. 워커가 정렬본을 들고
    있을 때 저장하므로 재렌더 비용이 없다. 전 페이지 저장(정상 학생도 원본
    대조 가능)."""
    d = _W.get("img_dir")
    if not d:
        return
    h, w = img.shape[:2]
    small = cv2.resize(img, (1400, round(h * 1400 / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 78])
    if ok:
        buf.tofile(str(Path(d) / f"p{i + 1}.jpg"))


def _wpage(i: int) -> dict:
    pix = _W["doc"][i].get_pixmap(dpi=_W["dpi"])
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR if pix.n == 4 else cv2.COLOR_RGB2BGR)
    try:
        out, info = hp_g3.rectify_verified(img, _W["tmpls"][0])
    except hp_align.AlignError as e:
        _wsave_review(i, img)               # 정렬 실패면 원본이라도 보여준다
        return dict(page=i, error=f"정렬실패:{e}")
    _wsave_review(i, out)
    rec = hp_run.read_page(_W["kind"], _W["tmpls"], out)   # 성명 포함(단일 gray)
    if info.get("gray_scan"):
        rec["gray_scan"] = True
    rec["page"] = i
    return rec


def read_pdf(pdf: Path, tnames: list[str], kind: str, workers: int,
             dpi: int = 300, img_dir: Path | None = None) -> list[dict]:
    doc = fitz.open(str(pdf))
    n = doc.page_count
    doc.close()
    if img_dir is not None:
        img_dir.mkdir(parents=True, exist_ok=True)
    idir = str(img_dir) if img_dir is not None else None
    recs = []
    if workers <= 1:
        _winit(str(pdf), tnames, kind, dpi, idir)
        for i in range(n):
            recs.append(_wpage(i))
            print(f"PROGRESS {i + 1}/{n}", flush=True)
        _W["doc"].close()
    else:
        with Pool(processes=min(workers, n), initializer=_winit,
                  initargs=(str(pdf), tnames, kind, dpi, idir)) as pool:
            for k, rec in enumerate(pool.imap_unordered(_wpage, range(n)), 1):
                recs.append(rec)
                print(f"PROGRESS {k}/{n}", flush=True)
    recs.sort(key=lambda r: r["page"])
    return recs


# ── 채점·CSV 행 구성 ──────────────────────────────────────────────
def load_keys_tolerant(keys_dir: Path, exam: str) -> dict:
    """정답키 적재 — 없는 파일은 건너뛴다(EBSi 미등록 시험: 판독표만 생성).
    hp_run.load_keys(전 과목 필수)와 달리 과목별 부분 적재를 허용한다."""
    def _try(p: Path):
        return hp_run._load_json(p) if p.exists() else None
    k: dict = {}
    k["history"] = _try(keys_dir / f"{exam}_한국사_xip_api.json")
    k["english"] = _try(keys_dir / f"{exam}_영어_xip_api.json")
    # 고1·2: 국어·수학도 선택과목 없는 단일 키(answers/points, 수학 단답 22~30 포함)
    k["korean_g12"] = _try(keys_dir / f"{exam}_국어_xip_api.json")
    k["math_g12"] = _try(keys_dir / f"{exam}_수학_xip_api.json")
    k["social"] = _try(keys_dir / f"{exam}_통합사회_xip_api.json")
    k["science"] = _try(keys_dir / f"{exam}_통합과학_xip_api.json")
    k["korean"] = {el: v for el in ("화법과작문", "언어와매체")
                   if (v := _try(keys_dir / f"{exam}_국어_{el}.json"))}
    k["math"] = {el: v for el in ("확률과통계", "미적분", "기하")
                 if (v := _try(keys_dir / f"{exam}_수학_{el}.json"))}
    k["explore"] = {}
    for code, name in hp_run.EXPL_CODES.items():
        v = _try(keys_dir / f"{exam}_{name}_xip_api.json")
        if v:
            k["explore"][code] = (name, v)
    return k


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


def _ident(rec: dict, names: dict) -> tuple[dict, list[str]]:
    """(신원 dict, 성명 관련 플래그). 조용한 이름 생성 금지 —
    카드 판독 성명은 신뢰(ok)될 때만 쓰고, 명단이 있으면 대조 경고에 활용."""
    sid = rec.get("id") or {}
    ban = "" if sid.get("ban") is None else str(sid["ban"])
    num = "" if sid.get("num") is None else str(sid["num"])
    nrec = rec.get("name") or {}
    card = nrec.get("name", "")
    roster = names.get((ban, num), "")
    fl: list[str] = []
    norm = lambda s: str(s or "").replace(" ", "")
    if roster:
        final = roster
        if nrec.get("ok") and card and norm(card) != norm(roster):
            fl.append(f"성명대조불일치(카드판독:{card})")
    elif nrec.get("ok") and card:
        final = card
    else:
        final = ""
        if card or nrec.get("issues"):
            fl.append("성명판독불가:" + ",".join(nrec.get("issues") or ["약한 마킹"]))
    return {"반": ban, "번호": num, "성명": final}, fl


def _flags(rec: dict, ident: dict) -> list[str]:
    fl = []
    sid = rec.get("id") or {}
    if rec.get("error"):
        fl.append(rec["error"])
    if rec.get("gray_scan"):
        fl.append("흑백스캔(컬러 재스캔 권장)")
    if sid.get("school") not in (None, 19718, 9718):   # 9718 = 고1·2 카드(4열, 첫자리 인쇄)
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
                         "페이지": rec["page"] + 1, "확인필요": rec["error"]})
            continue
        ident, name_flags = _ident(rec, names)
        main = rec.get("ans") or rec.get("ans1") or {}
        if hp_run.is_blank(main) and not ident["반"] and not ident["번호"] \
                and (kind != "explore" or hp_run.is_blank(rec.get("ans2", {0: 0}))):
            continue                       # 백지(결시) 카드 — 명부에서 제외
        fl = _flags(rec, ident) + name_flags
        # 과목·카드 바꿔치기 안전망: 수험번호는 읽혔는데 답란이 대부분 비면
        # 다른 과목 카드를 올렸을 가능성이 크다 (백지 결시는 위에서 이미 제외).
        if kind != "explore" and main:
            blank_ratio = sum(1 for v in main.values() if v == 0) / len(main)
            if blank_ratio >= 0.6:
                fl.append("미마킹과다(과목·양식 확인)")
        row = dict(ident)
        row["페이지"] = rec["page"] + 1
        if kind == "history":
            row.update({str(q): _ans_str(a) for q, a in rec["ans"].items()})
            key = keys["history"]
            if key:                           # 정답키 미등록 — 판독표만
                row.update(점수=hp_run.score_simple(rec["ans"], key), 만점=50)
            else:
                row.update(점수="", 만점="")
        elif kind == "english":
            row.update({str(q): _ans_str(a) for q, a in rec["ans"].items()})
            if keys["english"]:               # 정답키 미등록 — 판독표만
                row.update(점수=hp_run.score_simple(rec["ans"], keys["english"]),
                           만점=100)
            else:
                row.update(점수="", 만점="")
        elif kind in ("korean_g12", "social", "science"):
            # 고1·2 객관식 단일 키 과목 — 판독표 포맷(점수/만점 + 문항별 답)
            row.update({str(q): _ans_str(a) for q, a in rec["ans"].items()})
            key = keys[kind]
            if key:
                row.update(점수=hp_run.score_simple(rec["ans"], key),
                           만점=key.get("max_score") or (100 if kind == "korean_g12" else 50))
            else:
                row.update(점수="", 만점="")
        elif kind == "math_g12":
            # 고1·2 수학: 객관식 1~21 + 단답형 22~30 (단일 키, answers 에 단답 정답 수)
            allans = dict(rec["ans"])
            allans.update(rec["sa"])
            for q in range(1, 31):
                row[str(q)] = _ans_str(allans.get(q))
            if any(v == -1 for v in rec["sa"].values()):
                fl.append("단답중복")
            key = keys["math_g12"]
            if key:
                scored = {q: (0 if a in (None, -1) else a) for q, a in allans.items()}
                row.update(점수=hp_run.score_simple(scored, key), 만점=100)
            else:
                row.update(점수="", 만점="")
        elif kind == "korean":
            ch = rec.get("choice")
            row.update({str(q): _ans_str(a) for q, a in rec["ans"].items()})
            if not keys["korean"]:            # 정답키 미등록 — 판독표만
                row.update(선택과목=ch or "", 원점수="", 만점="")
            elif ch in keys["korean"]:
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
            if not keys["math"]:              # 정답키 미등록 — 판독표만
                row.update(선택과목=ch or "", 원점수="", 만점="")
            elif ch in keys["math"]:
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
                if not keys["explore"]:       # 정답키 미등록 — 판독표만
                    row[f"{pre}코드"] = code
                    row.update({f"{pre}_{q}": _ans_str(a) for q, a in ans.items()})
                    continue
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


def _write_meta(out_dir: Path, tnames: list[str]) -> None:
    """재현성 기록: 코드 버전(git) + 템플릿 해시 — 결과물이 어느 보정본으로
    만들어졌는지 추적 가능하게 한다."""
    import hashlib
    import subprocess
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE,
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        rev = ""
    tmpl_hash = {}
    for n in tnames:
        p = HERE / "templates" / f"{n}.json"
        if p.exists():
            tmpl_hash[n] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    (out_dir / "run_meta.json").write_text(json.dumps(
        dict(code_rev=rev, templates=tmpl_hash), ensure_ascii=False, indent=1),
        encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--subject", required=True,
                    choices=sorted({*SUBJ_SETUP, *SUBJ_SETUP_G12}))
    ap.add_argument("--card", default="hp", choices=("hp", "cb", "g12"),
                    help="카드 양식: hp=고3 학평 / cb=충북 모의평가 / g12=고1·2 학평")
    ap.add_argument("--keys-dir", type=Path, default=HERE / "keys")
    ap.add_argument("--irecord", default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--names", type=Path, default=None)
    ap.add_argument("--dpi", type=int, default=300)      # 웹 호환(현재 300 고정 렌더)
    ap.add_argument("--template", default=None)          # 웹 호환(템플릿은 과목으로 결정)
    import os
    auto = max(1, min(10, (cpu_count() or 8) // 2))   # ≈물리코어, 상한 10
    ap.add_argument("--workers", type=int,
                    default=int(os.environ.get("OMR_WORKERS", "0")) or auto)
    a = ap.parse_args()

    setup = {"cb": SUBJ_SETUP_CB, "g12": SUBJ_SETUP_G12}.get(a.card, SUBJ_SETUP)
    if a.subject not in setup:
        print(f"[오류] {a.subject}: 이 카드 양식({a.card})은 해당 과목이 아직 "
              "보정되지 않았습니다", flush=True)
        return 1
    tnames, kind, suffix = setup[a.subject]
    if not a.irecord:
        print("경고: --irecord 미지정 — 정답키 매칭이 모호할 수 있습니다.", flush=True)
    keys = load_keys_tolerant(a.keys_dir, a.irecord or "")
    if not keys.get(kind):
        print("정답키 없음 — 채점 없이 판독표만 생성합니다 (키 등록 후 재실행 시 채점).",
              flush=True)
    names = _load_names(a.names)
    a.out.mkdir(parents=True, exist_ok=True)

    print(f"{a.subject}: 학평 카드 판독 시작 ({a.pdf.name}, workers={a.workers}, dpi={a.dpi})", flush=True)
    recs = read_pdf(a.pdf, tnames, kind, a.workers, dpi=a.dpi,
                    img_dir=a.out / "review_imgs")
    rows = rows_for(kind, recs, keys, names)
    csv_path = write_outputs(rows, a.out, a.pdf.stem, suffix, a.subject)
    print(f"검토용 카드 이미지 {len(recs)}장 저장 (review_imgs/)", flush=True)
    _write_meta(a.out, tnames)

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
