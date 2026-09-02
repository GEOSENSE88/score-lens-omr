# -*- coding: utf-8 -*-
"""
EBSi 풀서비스 → 수학 정답·배점 자동 추출 → keys/*.json
score_lens / omr_scorer

해설 PDF가 있으면 글자를 읽고, 해설 게시 전에는 먼저 올라온
``math_main_ans_*.png`` 정답표를 읽는다.

수학 정답표: [공통: 수학Ⅰ·수학Ⅱ] 1-15 객관식(원문자)+16-22 단답(숫자),
[선택: 확률과 통계/미적분/기하] 각 23-28 객관식+29-30 단답.
배점: 문제지의 [3점]/[4점] 표시(없으면 2점). 선택과목별 총점 100 검증.

사용법:  python math_keys.py 202606043
"""
from __future__ import annotations
import json, re, sys, urllib.request
from pathlib import Path
import fitz

from ebsi_math_answer_png import parse_answer_image

BASE = "https://www.ebsi.co.kr"
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
KEYS_DIR = Path(__file__).parent / "keys"
WORK_DIR = Path(__file__).parent / "work"
ELECTIVES = ["확률과 통계", "미적분", "기하"]
CIRC = {chr(0x2460 + i): i + 1 for i in range(5)}


def _get(url, referer=None):
    h = dict(HDR)
    if referer:
        h["Referer"] = referer; h["X-Requested-With"] = "XMLHttpRequest"
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40).read()


def resolve_math_pdfs(irecord):
    """수학 문제지와 해설 PDF 또는 정답 PNG URL을 찾는다."""
    ref = f"{BASE}/ebs/xip/xipa/retrieveSCVMainInfo.ebs?irecord={irecord}&targetCd=D200"
    html = _get(f"{BASE}/ebs/xip/xipt/RetrieveSCVMainTop.ajax?irecord={irecord}", ref).decode("cp949", "ignore")
    sources = re.findall(r"https?://wdown\.ebsi\.co\.kr/[^\s\"')]+\.(?:pdf|png)", html)
    out = {}
    for u in sources:
        nm = u.rsplit("/", 1)[-1]
        if nm.startswith("math_main_mun"): out["mun"] = u
        elif nm.startswith("math_main_hsj"): out["hsj"] = u
        elif nm.startswith("math_main_ans"): out["ans"] = u
    if "mun" not in out or not ({"hsj", "ans"} & set(out)):
        raise RuntimeError(f"수학 문제/정답 자료 못 찾음: {sources}")
    return out


def _answers_after(text, heading, qrange):
    """heading 뒤에서 qrange 문항의 정답(객관식=1~5, 단답=숫자) 추출."""
    i = text.find(heading)
    if i < 0:
        return {}
    tail = text[i + len(heading): i + len(heading) + 1200]
    out = {}
    for q, val in re.findall(r"(\d{1,2})\.\s*([①-⑤]|\d{1,3})", tail):
        q = int(q)
        if q not in qrange:
            continue
        out[q] = CIRC[val] if val in CIRC else int(val)
        if len(out) == len(qrange):
            break
    return out


def parse_answers(hsj_pdf):
    d = fitz.open(hsj_pdf)
    full = "".join(d[i].get_text() for i in range(d.page_count))
    common = _answers_after(full, "[공통", set(range(1, 23)))
    res = {"공통": common}
    for el in ELECTIVES:
        res[el] = _answers_after(full, f"[선택: {el}", set(range(23, 31)))
    return res


def _page_columns(page):
    words = page.get_text("words")
    if not words:
        return ""
    midx = page.rect.width / 2
    ser = lambda ws: " ".join(w[4] for w in sorted(ws, key=lambda w: (round(w[1] / 6), w[0])))
    left = [w for w in words if (w[0] + w[2]) / 2 < midx]
    right = [w for w in words if (w[0] + w[2]) / 2 >= midx]
    return ser(left) + " \n " + ser(right)


def parse_points(mun_pdf):
    """문제지에서 [3점]/[4점] → (섹션, 문항). 없으면 2점."""
    d = fitz.open(mun_pdf)
    sect = "공통"
    expect = {"공통": 1, "확률과 통계": 23, "미적분": 23, "기하": 23}
    pts = {"공통": {}, "확률과 통계": {}, "미적분": {}, "기하": {}}
    rng = {"공통": (1, 22), "확률과 통계": (23, 30), "미적분": (23, 30), "기하": (23, 30)}
    curq = None
    pat = re.compile(r"(확률과\s*통계)|(미적분)|(기하)|(\[\s*([34])\s*점\s*\])|(?<![\d.])(\d{1,2})\s*[.．]")
    for pi in range(d.page_count):
        for m in pat.finditer(_page_columns(d[pi])):
            if m.group(1): sect, curq = "확률과 통계", None
            elif m.group(2): sect, curq = "미적분", None
            elif m.group(3): sect, curq = "기하", None
            elif m.group(4):
                if curq is not None:
                    pts[sect][curq] = int(m.group(5))
            else:
                n = int(m.group(6)); lo, hi = rng[sect]
                if n == expect[sect] and lo <= n <= hi:
                    curq, expect[sect] = n, n + 1
    for sec, (lo, hi) in rng.items():
        for q in range(lo, hi + 1):
            pts[sec].setdefault(q, 2)
    return pts


def build_keys(irecord, exam_name=""):
    KEYS_DIR.mkdir(exist_ok=True); WORK_DIR.mkdir(exist_ok=True)
    urls = resolve_math_pdfs(irecord)
    mun = WORK_DIR / f"_{irecord}_math_mun.pdf"; mun.write_bytes(_get(urls["mun"]))
    if "hsj" in urls:
        hsj = WORK_DIR / f"_{irecord}_math_hsj.pdf"; hsj.write_bytes(_get(urls["hsj"]))
        answers = parse_answers(hsj)
    else:
        ans = WORK_DIR / f"_{irecord}_math_ans.png"; ans.write_bytes(_get(urls["ans"]))
        print("  해설 PDF 게시 전 - EBSi 정답 PNG에서 추출")
        answers = parse_answer_image(ans)
    points = parse_points(mun)
    written = []
    for el in ELECTIVES:
        q = {}
        for n in range(1, 31):
            sec = "공통" if n <= 22 else el
            kind = "objective" if (n <= 15 or 23 <= n <= 28) else "short"
            q[str(n)] = {"answer": answers[sec][n], "points": points[sec][n], "type": kind}
        total = sum(v["points"] for v in q.values())
        if total != 100:
            raise SystemExit(f"[오류] {el} 배점 합계가 100점이 아닙니다: {total}점 — 키 파일을 저장하지 않습니다")
        key = dict(exam=exam_name or f"irecord {irecord}", subject="수학", elective=el,
                   irecord=irecord, total=total, questions=q)
        out = KEYS_DIR / f"{irecord}_수학_{el.replace(' ', '')}.json"
        out.write_text(json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(out)
        print(f"  ✅ {out.name} (총점 {total})")
    return written


def main():
    if len(sys.argv) < 2:
        print(__doc__); return 1
    m = re.search(r"irecord=(\d+)", sys.argv[1])
    ir = m.group(1) if m else sys.argv[1].strip()
    print(f"수학 정답·배점 추출 — irecord={ir}")
    build_keys(ir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
