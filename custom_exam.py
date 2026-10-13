# -*- coding: utf-8 -*-
"""
사설 모의고사 — 정답·배점 직접 입력 (score_lens / omr_scorer)

EBSi 에 없는 시험(사설 모의고사·학교 자체 시험 등)을 교사가 웹에서 정답·배점을
직접 입력해 등록한다. 저장 형식은 EBSi 자동 등록 키와 **완전히 같은 파일명·스키마**
라서 판독기(run_hp/run_math/…)·통합성적표(consolidate)는 수정 없이 그대로 채점한다.

시험 ID: YYYY·MM·OO·G 9자리 — EBSi 시행코드(OO)는 01~20 이므로 사설은 80~99 를
쓴다(충돌 없음, 기존 9자리 정규식·월 파싱 그대로 동작).
메타 파일 {exam_id}_사설정보.json 에 시험 이름과 OMR 카드 종류를 둔다
(subject 키가 없어 정답키 로더들은 자동으로 건너뛴다).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

CUSTOM_OO = range(80, 100)
META_SUFFIX = "_사설정보.json"

# OMR 카드 종류 — web_app.catalog_for 가 이 값으로 판독기를 고른다.
CARDS_G3 = {
    "pg": "평가원·수능형 (2027 신형 카드)",
    "hp": "교육청 전국연합 학력평가형",
    "cb": "충북교육청 모의평가형",
    "pg6": "평가원형 (구형 · 2026년 6월 카드)",
}
CARDS_G12 = {"g12": "고1·2 전국연합 학력평가형"}

KOR_ELECTIVES = ["화법과 작문", "언어와 매체"]
MATH_ELECTIVES = ["확률과 통계", "미적분", "기하"]
EXPL_SUBJECTS = ["생활과윤리", "윤리와사상", "한국지리", "세계지리", "동아시아사", "세계사",
                 "경제", "정치와법", "사회문화", "물리학Ⅰ", "화학Ⅰ", "생명과학Ⅰ", "지구과학Ⅰ",
                 "물리학Ⅱ", "화학Ⅱ", "생명과학Ⅱ", "지구과학Ⅱ"]


def _rng(a: int, b: int) -> list[int]:
    return list(range(a, b + 1))


# 과목 레이아웃: n=문항수, short=단답형 문항, common=공통 문항(선택과목 있는 경우),
# electives=선택과목, max=만점(합계 검증용), pts=기본 배점(UI 초기값)
_MATH_G3_PTS = {**{q: 2 for q in (1, 2)}, **{q: 3 for q in _rng(3, 8)},
                **{q: 4 for q in _rng(9, 15)}, **{q: 3 for q in _rng(16, 19)},
                **{q: 4 for q in _rng(20, 22)}, 23: 2, **{q: 3 for q in _rng(24, 27)},
                **{q: 4 for q in _rng(28, 30)}}
_MATH_G12_PTS = {**{q: 2 for q in _rng(1, 3)}, **{q: 3 for q in _rng(4, 13)},
                 **{q: 4 for q in _rng(14, 21)}, **{q: 3 for q in _rng(22, 25)},
                 **{q: 4 for q in _rng(26, 30)}}


def layouts(grade: int) -> dict[str, dict]:
    if grade == 3:
        return {
            "국어": dict(n=45, short=[], common=_rng(1, 34), electives=KOR_ELECTIVES, max=100, pts=2),
            "수학": dict(n=30, short=_rng(16, 22) + [29, 30], common=_rng(1, 22),
                         electives=MATH_ELECTIVES, max=100, pts=_MATH_G3_PTS),
            "영어": dict(n=45, short=[], max=100, pts=2),
            "한국사": dict(n=20, short=[], max=50, pts=2),
            **{s: dict(n=20, short=[], max=50, pts=2, explore=True) for s in EXPL_SUBJECTS},
        }
    return {
        "국어": dict(n=45, short=[], max=100, pts=2),
        "수학": dict(n=30, short=_rng(22, 30), max=100, pts=_MATH_G12_PTS),
        "영어": dict(n=45, short=[], max=100, pts=2),
        "한국사": dict(n=20, short=[], max=50, pts=2),
        "통합사회": dict(n=25, short=[], max=50, pts=2),
        "통합과학": dict(n=25, short=[], max=50, pts=2),
    }


def layouts_for_ui() -> dict:
    out = {}
    for g in (1, 2, 3):
        L = layouts(g)
        out[g] = {s: dict(n=v["n"], short=v["short"], common=v.get("common"),
                          electives=v.get("electives"), max=v["max"],
                          explore=bool(v.get("explore")),
                          pts={str(q): (v["pts"][q] if isinstance(v["pts"], dict) else v["pts"])
                               for q in range(1, v["n"] + 1)})
                  for s, v in L.items()}
    return dict(layouts=out, cards={3: CARDS_G3, 1: CARDS_G12, 2: CARDS_G12})


# ── 메타 ─────────────────────────────────────────────────────────
def is_custom_id(exam_id: str | None) -> bool:
    m = re.fullmatch(r"\d{6}(\d{2})\d", str(exam_id or ""))
    return bool(m) and int(m.group(1)) in CUSTOM_OO


def load_meta(exam_id: str | None, keys_dir: Path) -> dict | None:
    if not is_custom_id(exam_id):
        return None
    p = Path(keys_dir) / f"{exam_id}{META_SUFFIX}"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _clean_name(name: str) -> str:
    name = re.sub(r"[\x00-\x1f<>]", "", str(name or "")).strip()
    return name[:40]


def create_exam(year: int, month: int, grade: int, name: str, card: str,
                keys_dir: Path) -> dict:
    if not (2000 <= year <= 2100 and 1 <= month <= 12 and grade in (1, 2, 3)):
        raise ValueError("연도/월/학년 범위를 확인하세요.")
    cards = CARDS_G3 if grade == 3 else CARDS_G12
    if card not in cards:
        raise ValueError("OMR 카드 종류를 확인하세요.")
    name = _clean_name(name)
    if not name:
        raise ValueError("시험 이름을 입력하세요 (예: ○○ 사설 3월).")
    keys_dir = Path(keys_dir)
    keys_dir.mkdir(parents=True, exist_ok=True)
    for oo in CUSTOM_OO:
        eid = f"{year}{month:02d}{oo:02d}{grade}"
        if not any(keys_dir.glob(f"{eid}_*.json")):
            break
    else:
        raise ValueError("같은 달·학년의 사설 시험이 너무 많습니다(최대 20개). 안 쓰는 시험을 삭제하세요.")
    meta = dict(exam_id=eid, kind="custom_exam", name=name, card=card, grade=grade,
                year=year, month=month,
                created=datetime.now().isoformat(timespec="seconds"))
    (keys_dir / f"{eid}{META_SUFFIX}").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def update_meta(exam_id: str, keys_dir: Path, name: str | None = None,
                card: str | None = None) -> dict:
    meta = load_meta(exam_id, keys_dir)
    if not meta:
        raise ValueError("사설 시험을 찾을 수 없습니다.")
    if name is not None and _clean_name(name):
        meta["name"] = _clean_name(name)
    cards = CARDS_G3 if meta["grade"] == 3 else CARDS_G12
    if card is not None and card in cards:
        meta["card"] = card
    (Path(keys_dir) / f"{exam_id}{META_SUFFIX}").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def delete_exam(exam_id: str, keys_dir: Path) -> int:
    if not load_meta(exam_id, keys_dir):
        raise ValueError("사설 시험만 삭제할 수 있습니다.")
    n = 0
    for p in Path(keys_dir).glob(f"{exam_id}_*.json"):
        p.unlink(missing_ok=True)
        n += 1
    return n


# ── 과목 정답키 저장 ──────────────────────────────────────────────
def _key_path(keys_dir: Path, exam_id: str, grade: int, subject: str,
              elective: str | None) -> Path:
    if grade == 3 and subject in ("국어", "수학"):
        return Path(keys_dir) / f"{exam_id}_{subject}_{elective.replace(' ', '')}.json"
    return Path(keys_dir) / f"{exam_id}_{subject}_xip_api.json"


def _num(v) -> float | int:
    f = float(v)
    return int(f) if f.is_integer() else round(f, 2)


def _validate(lay: dict, answers: dict, points: dict) -> tuple[dict, dict]:
    """{q:정답}, {q:배점} 검증·정규화 — 1..n 모두 있어야 한다."""
    n, short = lay["n"], set(lay["short"])
    ans, pts = {}, {}
    for q in range(1, n + 1):
        a, p = answers.get(str(q), answers.get(q)), points.get(str(q), points.get(q))
        if a in (None, "") or p in (None, ""):
            raise ValueError(f"{q}번 정답·배점을 모두 입력하세요.")
        try:
            a = int(str(a).strip())
            p = _num(str(p).strip())
        except ValueError:
            raise ValueError(f"{q}번 정답·배점이 숫자가 아닙니다.")
        if q in short:
            if not 0 <= a <= 999:
                raise ValueError(f"{q}번(단답형) 정답은 0~999 사이여야 합니다.")
        elif not 1 <= a <= 5:
            raise ValueError(f"{q}번 정답은 1~5 중 하나여야 합니다.")
        if not 0 < p <= 10:
            raise ValueError(f"{q}번 배점은 0보다 크고 10 이하여야 합니다.")
        ans[q], pts[q] = a, p
    return ans, pts


def save_subject(exam_id: str, subject: str, keys_dir: Path,
                 parts: list[dict]) -> list[str]:
    """parts = [{elective|None, answers:{q:a}, points:{q:p}}] (공통+선택 합친 1..n 전체).
    반환: 저장한 파일 이름 목록."""
    meta = load_meta(exam_id, keys_dir)
    if not meta:
        raise ValueError("사설 시험을 찾을 수 없습니다.")
    grade = int(meta["grade"])
    lay = layouts(grade).get(subject)
    if not lay:
        raise ValueError(f"고{grade}에서 지원하지 않는 과목입니다: {subject}")
    electives = lay.get("electives")
    if electives:
        if not parts or any(p.get("elective") not in electives for p in parts):
            raise ValueError(f"{subject} 선택과목을 확인하세요.")
    elif len(parts) != 1:
        raise ValueError("과목 정답 형식이 올바르지 않습니다.")

    saved = []
    src = {"note": "사설 모의고사 — 교사 직접 입력", "entered_at":
           datetime.now().isoformat(timespec="seconds")}
    for part in parts:
        el = part.get("elective") if electives else None
        ans, pts = _validate(lay, part.get("answers") or {}, part.get("points") or {})
        total = _num(sum(pts.values()))
        if grade == 3 and subject == "수학":
            payload = {
                "exam": f"사설 {exam_id}", "exam_id": exam_id, "irecord": exam_id,
                "subject": "수학", "elective": el, "complete": True, "custom": True,
                "total": total,
                "questions": {str(q): {"answer": ans[q], "points": pts[q],
                                       "type": "short" if q in lay["short"] else "objective"}
                              for q in sorted(ans)},
                "source": src,
            }
        else:
            payload = {
                "exam_id": exam_id, "subject": subject, "elective": el or (
                    subject if lay.get("explore") else None),
                "complete": True, "custom": True, "item_count": len(ans),
                "max_score": total,
                "answers": {str(q): ans[q] for q in sorted(ans)},
                "points": {str(q): pts[q] for q in sorted(pts)},
                "source": src,
            }
            if grade in (1, 2):
                payload["grade_band"] = "g12"
        path = _key_path(keys_dir, exam_id, grade, subject, el)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        saved.append(path.name)
    return saved


def delete_subject(exam_id: str, subject: str, keys_dir: Path,
                   elective: str | None = None) -> int:
    meta = load_meta(exam_id, keys_dir)
    if not meta:
        raise ValueError("사설 시험을 찾을 수 없습니다.")
    lay = layouts(int(meta["grade"])).get(subject)
    if not lay:
        raise ValueError("과목을 확인하세요.")
    els = [elective] if elective else (lay.get("electives") or [None])
    n = 0
    for el in els:
        p = _key_path(keys_dir, exam_id, int(meta["grade"]), subject, el)
        if p.exists():
            p.unlink()
            n += 1
    return n


def read_exam(exam_id: str, keys_dir: Path) -> dict:
    """편집 UI 프리필용: 메타 + {과목: {선택과목|'': {answers, points}}}."""
    meta = load_meta(exam_id, keys_dir)
    if not meta:
        raise ValueError("사설 시험을 찾을 수 없습니다.")
    subjects: dict[str, dict] = {}
    for p in sorted(Path(keys_dir).glob(f"{exam_id}_*.json")):
        if p.name.endswith(META_SUFFIX):
            continue
        try:
            k = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        subj = k.get("subject")
        if not subj:
            continue
        if isinstance(k.get("questions"), dict):
            ans = {q: v["answer"] for q, v in k["questions"].items()}
            pts = {q: v["points"] for q, v in k["questions"].items()}
        else:
            ans, pts = k.get("answers") or {}, k.get("points") or {}
        el = k.get("elective") if int(meta["grade"]) == 3 and subj in ("국어", "수학") else ""
        subjects.setdefault(subj, {})[el or ""] = dict(answers=ans, points=pts)
    return dict(meta=meta, subjects=subjects)
