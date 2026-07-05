# -*- coding: utf-8 -*-
"""
산남고 OMR 채점실 — 웹앱 (score_lens / omr_scorer)

업로드(학년·과목별 PDF) → 백그라운드 채점 → 진행상황 폴링 → 결과 다운로드.
전 과목 채점이 끝나면 consolidate 로 '통합성적표.xlsx'(대교협 + UNIV + 정오표)를
자동 생성해 다운로드 최상단에 노출한다.

    python web_app.py            # 교내망/PC 모드 (localhost 무인증)

환경변수 (서버 배포 시):
    OMR_BEHIND_PROXY=1   nginx 등 리버스 프록시 뒤 — 실제 클라이언트 IP 사용,
                         localhost 무인증 면제 해제(전원 접속코드 필요), 보안쿠키
    OMR_ACCESS_CODE=...  접속 코드 지정(미지정 시 access_code.txt 자동생성)
    OMR_DATA_TTL_MIN=60  업로드·결과 파일 보존 시간(분). 지나면 자동 삭제(0=끄기)
    OMR_HOST / OMR_PORT  바인딩 (기본 0.0.0.0:5050)
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.utils import secure_filename

import consolidate as cons
import exam_registry as er


ROOT = Path(__file__).resolve().parent
UPLOAD_ROOT = ROOT / "uploads"
WEB_OUTPUT_ROOT = ROOT / "output" / "web_runs"
ALLOWED_EXTENSIONS = {".pdf", ".csv"}

# ── 배포 설정 (환경변수) ──────────────────────────────────────────
BEHIND_PROXY = os.environ.get("OMR_BEHIND_PROXY", "") == "1"
DATA_TTL_MIN = int(os.environ.get("OMR_DATA_TTL_MIN", "0") or "0")
OPEN_ACCESS = os.environ.get("OMR_OPEN_ACCESS", "") == "1"   # 접속 코드 없이 누구나 사용

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=BEHIND_PROXY,     # HTTPS 전용 쿠키 (프록시 뒤=TLS 종단)
    PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
)
if BEHIND_PROXY:
    # nginx 가 넘기는 X-Forwarded-* 를 신뢰 → request.remote_addr 가 실제 클라이언트 IP.
    # (없으면 모든 요청이 127.0.0.1 로 보여 localhost 무인증 게이트가 통째로 우회된다.)
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

JOBS: dict[str, dict] = {}


# ── 인증: 세션 비밀키 + 접속 코드 ─────────────────────────────────
def _persist_secret(path: Path, gen) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    val = gen()
    path.write_text(val, encoding="utf-8")
    return val


app.secret_key = _persist_secret(ROOT / ".flask_secret", lambda: secrets.token_hex(32))
# 접속 코드: 환경변수 우선, 없으면 access_code.txt (자동 6자리). 프록시 뒤면 8자리 권장.
ACCESS_CODE = os.environ.get("OMR_ACCESS_CODE", "").strip() or _persist_secret(
    ROOT / "access_code.txt",
    lambda: f"{secrets.randbelow(90000000) + 10000000}" if BEHIND_PROXY
    else f"{secrets.randbelow(900000) + 100000}")

# 로그인 무차별 대입 방어 (IP별 실패 카운트 + 잠금)
_LOGIN_FAILS: dict[str, list] = {}          # ip -> [fail_count, locked_until_ts]
LOCK_THRESHOLD, LOCK_SECONDS = 5, 300


def _client_ip(req) -> str:
    return req.remote_addr or "?"


def _is_local(req) -> bool:
    # 프록시 뒤(서버 모드)에서는 localhost 무인증 면제를 끈다 — 전원 접속코드 필요.
    if BEHIND_PROXY:
        return False
    return _client_ip(req) in ("127.0.0.1", "::1")


def lan_ip() -> str:
    """교내망에서 접근 가능한 이 PC 의 IP (베스트 에포트)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))       # 실제 전송 없음 — 라우팅 조회용
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


@app.after_request
def _no_cache_html(resp):
    # HTML 은 캐시하지 않는다 — 업데이트 후 브라우저가 옛 페이지를 보여주는 문제 방지.
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.before_request
def _gate():
    """오픈 액세스면 무인증. 아니면 PC(localhost) 무인증 / 원격은 접속 코드."""
    if OPEN_ACCESS or _is_local(request) or session.get("auth") \
            or request.endpoint in ("login", "static"):
        return None
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    ip = _client_ip(request)
    if request.method == "POST":
        cnt, until = _LOGIN_FAILS.get(ip, [0, 0])
        if until and time.time() < until:
            wait = int(until - time.time())
            return render_template("login.html",
                                   error=f"시도가 많아 잠겼습니다. {wait}초 후 다시 시도하세요."), 429
        # 타이밍 공격 방지: 상수시간 비교
        if hmac.compare_digest(request.form.get("code", "").strip(), ACCESS_CODE):
            _LOGIN_FAILS.pop(ip, None)
            session["auth"] = True
            session.permanent = True
            nxt = request.args.get("next") or ""
            # open-redirect 방지: 내부 경로만 허용
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
        cnt += 1
        _LOGIN_FAILS[ip] = [cnt, time.time() + LOCK_SECONDS if cnt >= LOCK_THRESHOLD else 0]
        error = "접속 코드가 올바르지 않습니다."
        if cnt >= LOCK_THRESHOLD:
            error = f"시도가 너무 많습니다. {LOCK_SECONDS // 60}분 후 다시 시도하세요."
    return render_template("login.html", error=error)


# ── 데이터 보존: TTL 지난 업로드·결과 폴더 자동 삭제 (개인정보 최소화) ──
def _sweep_old_data() -> None:
    if DATA_TTL_MIN <= 0:
        return
    cutoff = time.time() - DATA_TTL_MIN * 60
    for base in (UPLOAD_ROOT, WEB_OUTPUT_ROOT):
        if not base.exists():
            continue
        for d in base.iterdir():
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass


def _start_sweeper() -> None:
    """새 업로드가 없어도 결과가 방치되지 않도록 주기적으로 정리한다."""
    if DATA_TTL_MIN <= 0:
        return

    def loop():
        while True:
            time.sleep(300)
            try:
                _sweep_old_data()
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True).start()


_start_sweeper()


# ── 학년별 과목 카탈로그 ──────────────────────────────────────────
# csv_kind → consolidate_paths 의 인자 이름 (통합성적표 조립용)
SUBJECTS_BY_GRADE: dict[int, list[dict]] = {
    3: [
        dict(key="korean", label="국어", icon="가", script="run.py",
             names=False, template=None, id_layout=None, csv_kind="korean",
             csv_suffix="_점수표.csv"),
        dict(key="math", label="수학", icon="∑", script="run_math.py",
             names=True, template="templates/math_eval_2027_06.json",
             id_layout=None, csv_kind="math", csv_suffix="_수학_점수표.csv"),
        dict(key="english", label="영어", icon="A", script="run_english.py",
             names=True, template="templates/english_eval_2027_06.json",
             id_layout=None, csv_kind="english", csv_suffix="_영어_판독표.csv"),
        dict(key="history", label="한국사", icon="史", script="run_history.py",
             names=True, template="templates/history_eval_2027_06.json",
             id_layout=None, csv_kind="history", csv_suffix="_한국사_판독표.csv"),
        dict(key="explore", label="탐구", icon="探", script="run_explore.py",
             names=True, template="templates/explore_eval_2027_06.json",
             id_layout=None, csv_kind="explore", csv_suffix="_탐구_판독표.csv"),
    ],
}
# 고1·2 공통 카탈로그 (run_objective 기반; 템플릿은 첫 스캔 보정 후 생성됨)
_G12 = [
    dict(key="korean", label="국어", icon="가", script="run_objective.py",
         subject_arg="국어", names=True, template="templates/korean_g12.json",
         id_layout="g12", csv_kind="korean_obj", csv_suffix="_국어_판독표.csv"),
    dict(key="math", label="수학", icon="∑", script="run_objective.py",
         subject_arg="수학", names=True, template="templates/math_g12.json",
         id_layout="g12", csv_kind="math", csv_suffix="_수학_판독표.csv"),
    dict(key="english", label="영어", icon="A", script="run_objective.py",
         subject_arg="영어", names=True, template="templates/english_g12.json",
         id_layout="g12", csv_kind="english", csv_suffix="_영어_판독표.csv"),
    dict(key="history", label="한국사", icon="史", script="run_objective.py",
         subject_arg="한국사", names=True, template="templates/history_g12.json",
         id_layout="g12", csv_kind="history", csv_suffix="_한국사_판독표.csv"),
    dict(key="social", label="통합사회", icon="社", script="run_objective.py",
         subject_arg="통합사회", names=True, template="templates/social_g12.json",
         id_layout="g12", csv_kind="social", csv_suffix="_통합사회_판독표.csv"),
    dict(key="science", label="통합과학", icon="科", script="run_objective.py",
         subject_arg="통합과학", names=True, template="templates/science_g12.json",
         id_layout="g12", csv_kind="science", csv_suffix="_통합과학_판독표.csv"),
]
SUBJECTS_BY_GRADE[1] = _G12
SUBJECTS_BY_GRADE[2] = _G12


def subject_available(spec: dict) -> bool:
    """판독에 필요한 좌표 템플릿이 준비됐는가. (고3 국어는 자동보정이라 템플릿 불요)"""
    t = spec.get("template")
    return t is None or (ROOT / t).exists()


def catalog_for_ui() -> dict:
    out = {}
    for grade, specs in SUBJECTS_BY_GRADE.items():
        out[grade] = [
            dict(key=s["key"], label=s["label"], icon=s["icon"],
                 available=subject_available(s))
            for s in specs
        ]
    return out


@dataclass
class SubjectRun:
    subject: str
    label: str
    pdf: str
    out_dir: str
    returncode: int
    command: list[str]
    log: str
    files: list[str]

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _existing_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(p for p in path.rglob("*") if p.is_file())


def run_reader(spec: dict, pdf: Path, *, exam_id: str | None, names: Path | None,
               out_root: Path, dpi: int) -> SubjectRun:
    """과목 판독 스크립트를 서브프로세스로 실행하고 산출물을 수집."""
    script = ROOT / spec["script"]
    out_dir = out_root / f"{spec['key']}_{pdf.stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    before = set(_existing_files(out_dir))

    cmd = [sys.executable, str(script), str(pdf),
           "--keys-dir", str(ROOT / "keys"), "--dpi", str(dpi), "--out", str(out_dir)]
    if spec.get("subject_arg"):
        cmd += ["--subject", spec["subject_arg"]]
    if exam_id:
        cmd += ["--irecord", str(exam_id)]
    if names and spec.get("names"):
        cmd += ["--names", str(names)]
    if spec.get("template"):
        cmd += ["--template", str(ROOT / spec["template"])]
    if spec.get("id_layout") and spec["script"] == "run_objective.py":
        cmd += ["--id-layout", spec["id_layout"]]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True, encoding="utf-8",
                          errors="replace", stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)

    log_path = out_dir / "run_log.txt"
    log_path.write_text(proc.stdout, encoding="utf-8")
    produced = sorted(set(_existing_files(out_dir)) - before)
    if log_path not in produced:
        produced.append(log_path)
    return SubjectRun(subject=spec["key"], label=spec["label"], pdf=str(pdf),
                      out_dir=str(out_dir), returncode=proc.returncode,
                      command=cmd, log=str(log_path),
                      files=[str(p) for p in produced])


def _find_csv(run: SubjectRun, suffix: str) -> str | None:
    for f in run.files:
        if f.endswith(suffix):
            return f
    for f in run.files:                    # suffix 불일치 시 첫 CSV 폴백
        if f.lower().endswith(".csv"):
            return f
    return None


def allowed_file(path: str) -> bool:
    return Path(path).suffix.lower() in ALLOWED_EXTENSIONS


def save_upload(run_upload_dir: Path, field: str) -> Path | None:
    file = request.files.get(field)
    if not file or not file.filename:
        return None
    if not allowed_file(file.filename):
        raise ValueError(f"{field}: PDF/CSV 파일만 업로드할 수 있습니다.")
    # ⚠️ 파일명은 반드시 필드명 기반으로 저장한다.
    #    secure_filename('국어.pdf') 는 한글을 전부 제거해 'pdf' 만 남기므로,
    #    원본 파일명을 쓰면 과목별 업로드가 같은 이름으로 서로 덮어쓴다.
    path = run_upload_dir / f"{field}{Path(file.filename).suffix.lower()}"
    file.save(path)
    return path


def collect_downloads(run_dir: Path) -> list[dict]:
    files = []
    for p in sorted(run_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() != ".xlsx":
            continue
        is_combined = p.name.startswith("통합성적표")
        subject = ""
        if not is_combined:
            key = p.parent.name.split("_", 1)[0]
            for specs in SUBJECTS_BY_GRADE.values():
                for s in specs:
                    if s["key"] == key:
                        subject = s["label"]
                        break
                if subject:
                    break
        files.append(dict(
            name=p.name,
            title="통합성적표 (대교협 · UNIV · 정오표)" if is_combined
                  else f"{subject or p.parent.name} 점수표/정오표",
            kind="combined" if is_combined else "subject",
            subject=subject,
            size=p.stat().st_size,
            url=f"/download/{run_dir.name}/{p.relative_to(run_dir).as_posix()}",
        ))
    files.sort(key=lambda d: (d["kind"] != "combined", d["title"]))
    return files


def update_job(job_id: str, **values) -> None:
    JOBS.setdefault(job_id, {}).update(values)


def append_event(job_id: str, message: str, *, kind: str = "info") -> None:
    job = JOBS.setdefault(job_id, {})
    job.setdefault("events", []).append(dict(t=datetime.now().strftime("%H:%M:%S"),
                                             kind=kind, text=message))
    job["message"] = message


def process_job(job_id: str, run_output_dir: Path, grade: int,
                requested: list[tuple[dict, Path]], exam_id: str | None,
                exam_year: str | None, dpi: int, names: Path | None) -> None:
    runs: list[SubjectRun] = []
    csv_by_kind: dict[str, str] = {}
    try:
        update_job(job_id, status="running", total=len(requested), completed=0, ok=None)
        append_event(job_id, f"고{grade} 답안지 {len(requested)}과목 채점을 시작합니다.")

        # 국어를 먼저 돌려 성명 CSV 를 나머지 과목에 자동 연결 (별도 업로드 없을 때)
        ordered = sorted(requested, key=lambda x: x[0]["key"] != "korean")
        effective_names = names
        done = 0
        for spec, pdf in ordered:
            append_event(job_id, f"{spec['label']}: OMR 마킹 판독 중…", kind="run")
            run = run_reader(spec, pdf, exam_id=exam_id, names=effective_names,
                             out_root=run_output_dir, dpi=dpi)
            runs.append(run)
            done += 1
            update_job(job_id, completed=done,
                       runs=[asdict(r) | {"ok": r.ok} for r in runs])
            if not run.ok:
                append_event(job_id, f"{spec['label']}: 판독 실패 — 실행 로그를 확인하세요.", kind="error")
                continue
            csv_path = _find_csv(run, spec["csv_suffix"])
            if csv_path:
                csv_by_kind[spec["csv_kind"]] = csv_path
            if spec["key"] == "korean" and effective_names is None and csv_path:
                effective_names = Path(csv_path)   # 성명 자동 체이닝
                append_event(job_id, "국어 점수표의 성명을 나머지 과목에 자동 연결합니다.")
            append_event(job_id, f"{spec['label']}: 채점 완료 ✓", kind="ok")

        # 통합성적표 (대교협 + UNIV + 정오표)
        combined_ok = False
        if csv_by_kind:
            append_event(job_id, "통합성적표(대교협·UNIV·정오표)를 만드는 중…", kind="run")
            try:
                out = run_output_dir / f"통합성적표_고{grade}.xlsx"
                cons.consolidate_paths(
                    grade, keys_dir=str(ROOT / "keys"),
                    korean=csv_by_kind.get("korean") or csv_by_kind.get("korean_obj"),
                    math=csv_by_kind.get("math"),
                    english=csv_by_kind.get("english"),
                    history=csv_by_kind.get("history"),
                    explore=csv_by_kind.get("explore"),
                    social=csv_by_kind.get("social"),
                    science=csv_by_kind.get("science"),
                    out=out, exam_year=exam_year or None,
                    exam_id=exam_id)   # ⚠️ keys 에 여러 시험 공존 — 키 혼선 방지
                combined_ok = True
                append_event(job_id, "통합성적표 생성 완료 ✓", kind="ok")
            except Exception as exc:
                append_event(job_id, f"통합성적표 생성 실패: {exc}", kind="error")

        manifest = dict(grade=grade, exam_id=exam_id, exam_year=exam_year, dpi=dpi,
                        subjects=[s["key"] for s, _ in requested],
                        names=str(names) if names else "")
        (run_output_dir / "job_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        all_ok = all(r.ok for r in runs) and (combined_ok or not csv_by_kind)
        update_job(job_id, status="done", ok=all_ok,
                   downloads=collect_downloads(run_output_dir),
                   completed=len(requested))
        append_event(job_id,
                     "모든 작업이 끝났습니다. 아래에서 결과를 내려받으세요."
                     if all_ok else
                     "작업이 끝났지만 일부 과목에 문제가 있습니다. 로그를 확인하세요.",
                     kind="ok" if all_ok else "error")
    except Exception as exc:
        (run_output_dir / "error.txt").write_text(repr(exc), encoding="utf-8")
        update_job(job_id, status="error", ok=False, error=repr(exc),
                   downloads=collect_downloads(run_output_dir))
        append_event(job_id, f"오류가 발생했습니다: {exc!r}", kind="error")
    finally:
        # 채점이 끝나면 원본 답안지(업로드 PDF)와 페이지 이미지를 즉시 삭제한다.
        # 서버에는 다운로드용 결과 엑셀만 잠시 남고, 그것도 TTL 후 자동 삭제된다.
        try:
            shutil.rmtree(UPLOAD_ROOT / run_output_dir.name, ignore_errors=True)
            for pages_dir in run_output_dir.rglob("_pages_*"):
                shutil.rmtree(pages_dir, ignore_errors=True)
        except OSError:
            pass


@app.get("/")
def index():
    # 공유 정보(주소·접속코드)는 본인 PC 접속(PC/교내망 모드)에서만 노출.
    # 서버 모드(프록시 뒤)에서는 노출하지 않는다.
    share = None
    if _is_local(request):
        port = int(os.environ.get("OMR_PORT", "5050"))
        share = dict(url=f"http://{lan_ip()}:{port}", code=ACCESS_CODE)
    return render_template("index.html", catalog=catalog_for_ui(),
                           exams=er.list_exams(ROOT / "keys"), share=share)


@app.get("/api/exams")
def api_exams():
    return jsonify(ok=True, exams=er.list_exams(ROOT / "keys"))


@app.post("/api/exams/register")
def api_exam_register():
    """(연도, 월, 학년)으로 EBSi 에서 시험을 찾아 정답키를 자동 생성 (백그라운드 잡)."""
    try:
        year = int(request.form.get("year", ""))
        month = int(request.form.get("month", ""))
        grade = int(request.form.get("grade", ""))
    except ValueError:
        return jsonify(ok=False, message="연도/월/학년을 확인하세요."), 400
    if not (2000 <= year <= 2100 and 1 <= month <= 12 and grade in (1, 2, 3)):
        return jsonify(ok=False, message="연도/월/학년 범위를 확인하세요."), 400

    job_id = "reg_" + uuid.uuid4().hex[:8]
    JOBS[job_id] = dict(status="running", ok=None, kind="register",
                        message="EBSi에서 시험을 찾는 중…",
                        events=[], result=None, exams=None)

    def work():
        try:
            res = er.register_exam(year, month, grade, keys_dir=ROOT / "keys",
                                   progress=lambda m: append_event(job_id, m, kind="run"))
            ok = bool(res.get("irecord")) and not res.get("errors")
            update_job(job_id, status="done", ok=ok, result=res,
                       exams=er.list_exams(ROOT / "keys"))
            if res.get("irecord"):
                made = ", ".join(res.get("created") or []) or "새로 만든 키 없음"
                append_event(job_id, f"등록 완료 — 생성: {made}", kind="ok" if ok else "error")
            else:
                append_event(job_id, res["errors"][0] if res.get("errors") else "시험을 찾지 못했습니다.", kind="error")
        except Exception as exc:
            update_job(job_id, status="error", ok=False, error=repr(exc))
            append_event(job_id, f"등록 실패: {exc}", kind="error")

    threading.Thread(target=work, daemon=True).start()
    return jsonify(ok=True, job_id=job_id,
                   status_url=url_for("api_job", run_id=job_id)), 202


@app.post("/api/score")
def api_score():
    _sweep_old_data()                       # 오래된 업로드·결과 자동 정리 (TTL 설정 시)
    run_id = uuid.uuid4().hex[:12]
    run_upload_dir = UPLOAD_ROOT / run_id
    run_output_dir = WEB_OUTPUT_ROOT / run_id
    run_upload_dir.mkdir(parents=True, exist_ok=True)
    run_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        grade = int(request.form.get("grade", "3"))
        if grade not in SUBJECTS_BY_GRADE:
            return jsonify(ok=False, message="학년은 1·2·3 중 하나여야 합니다."), 400
        exam_id = request.form.get("exam_id", "").strip() or None
        exam_year = request.form.get("exam_year", "").strip() or None
        names = save_upload(run_upload_dir, "names")

        requested: list[tuple[dict, Path]] = []
        unavailable: list[str] = []
        for spec in SUBJECTS_BY_GRADE[grade]:
            uploaded = save_upload(run_upload_dir, f"pdf_{spec['key']}")
            if not uploaded:
                continue
            if not subject_available(spec):
                unavailable.append(spec["label"])
                continue
            requested.append((spec, uploaded))
        if unavailable:
            return jsonify(ok=False, message=(
                f"{', '.join(unavailable)}: 고{grade} 판독 좌표가 아직 보정되지 않았습니다. "
                "첫 실물 스캔으로 보정 후 사용할 수 있습니다.")), 400
        if not requested:
            return jsonify(ok=False, message="채점할 PDF를 한 개 이상 업로드하세요."), 400

        JOBS[run_id] = dict(status="queued", ok=None, run_id=run_id, grade=grade,
                            total=len(requested), completed=0,
                            message="작업을 대기열에 올렸습니다.",
                            events=[dict(t=datetime.now().strftime("%H:%M:%S"),
                                         kind="info", text="작업을 대기열에 올렸습니다.")],
                            downloads=[], runs=[])
        threading.Thread(target=process_job,
                         args=(run_id, run_output_dir, grade, requested,
                               exam_id, exam_year, 300, names),
                         daemon=True).start()
        return jsonify(ok=True, run_id=run_id,
                       status_url=url_for("api_job", run_id=run_id)), 202
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    except Exception as exc:
        (run_output_dir / "error.txt").write_text(repr(exc), encoding="utf-8")
        return jsonify(ok=False, message=repr(exc), run_id=run_id), 500


@app.get("/api/job/<run_id>")
def api_job(run_id: str):
    job = JOBS.get(run_id)
    if not job:
        return jsonify(ok=False, message="작업을 찾을 수 없습니다."), 404
    return jsonify(job)


@app.get("/download/<run_id>/<path:relpath>")
def download_file(run_id: str, relpath: str):
    # run_id 는 uuid4().hex[:12] 형태 — 경로 조작 방지를 위해 형식부터 검증
    if not re.fullmatch(r"[0-9a-f]{8,32}", run_id):
        abort(404)
    run_dir = (WEB_OUTPUT_ROOT / run_id).resolve()
    path = (run_dir / relpath).resolve()
    if run_dir not in path.parents and path != run_dir:
        abort(403)
    if not path.exists() or not path.is_file():
        abort(404)
    # 서버 무흔적: 서버 모드에서 통합성적표(모든 결과 포함)를 내려받으면
    # 잠시 후 이 채점 건을 서버에서 완전히 삭제한다. → 남는 사본은 브라우저뿐.
    # (call_on_close 는 sendfile/file_wrapper 경로에서 안 불려 타이머로 처리.)
    if (OPEN_ACCESS or BEHIND_PROXY) and path.name.startswith("통합성적표"):
        def _wipe_run():
            shutil.rmtree(run_dir, ignore_errors=True)
            shutil.rmtree(UPLOAD_ROOT / run_id, ignore_errors=True)
            JOBS.pop(run_id, None)
        threading.Timer(20.0, _wipe_run).start()
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    host = os.environ.get("OMR_HOST", "0.0.0.0")
    port = int(os.environ.get("OMR_PORT", "5050"))
    print("스코어렌즈 · Score Lens — 모의고사 OMR 스캔·채점")
    if BEHIND_PROXY:
        print(f"  서버 모드 (리버스 프록시 뒤) — bind {host}:{port}")
        print(f"  접속 코드: {'(OMR_ACCESS_CODE 환경변수)' if os.environ.get('OMR_ACCESS_CODE') else ACCESS_CODE}")
    else:
        print(f"  본인 PC   : http://127.0.0.1:{port}")
        print(f"  교내망 공유: http://{lan_ip()}:{port}   (접속 코드: {ACCESS_CODE})")
        print(f"  ※ 동료가 접속이 안 되면 Windows 방화벽에서 python 허용 필요")
    # 0.0.0.0 = 공유. threaded = 여러 명 동시 사용. (서버는 gunicorn 권장 — DEPLOY.md)
    app.run(host=host, port=port, debug=False, threaded=True)
