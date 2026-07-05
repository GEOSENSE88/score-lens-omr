# -*- coding: utf-8 -*-
"""
Score Lens OMR program entrypoint.

This file is intentionally a thin orchestration layer over the subject readers
that already work. It gives the school workflow one stable command:

    python omr_program.py batch --manifest examples/mock_exam_manifest.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent


SUBJECTS: dict[str, dict[str, Any]] = {
    "korean": {
        "label": "국어",
        "script": "run.py",
        "aliases": {"korean", "kor", "국어"},
        "template": None,
        "needs_names": False,
    },
    "math": {
        "label": "수학",
        "script": "run_math.py",
        "aliases": {"math", "mathematics", "수학"},
        "template": "templates/math_eval_2027_06.json",
        "needs_names": True,
    },
    "english": {
        "label": "영어",
        "script": "run_english.py",
        "aliases": {"english", "eng", "영어"},
        "template": "templates/english_eval_2027_06.json",
        "needs_names": True,
    },
    "history": {
        "label": "한국사",
        "script": "run_history.py",
        "aliases": {"history", "korean_history", "한국사"},
        "template": "templates/history_eval_2027_06.json",
        "needs_names": True,
    },
    "explore": {
        "label": "탐구",
        "script": "run_explore.py",
        "aliases": {"explore", "inquiry", "research", "탐구"},
        "template": "templates/explore_eval_2027_06.json",
        "needs_names": True,
    },
}


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


def normalize_subject(value: str) -> str:
    token = value.strip().lower()
    for key, spec in SUBJECTS.items():
        aliases = {str(a).lower() for a in spec["aliases"]}
        if token in aliases:
            return key
    known = ", ".join(spec["label"] for spec in SUBJECTS.values())
    raise SystemExit(f"알 수 없는 과목입니다: {value} (가능: {known})")


def default_run_dir(base_out: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base_out / "program_runs" / stamp


def resolve_path(value: str | Path | None, base: Path = ROOT) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    p = Path(value)
    return p if p.is_absolute() else (base / p)


def existing_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted([p for p in path.rglob("*") if p.is_file()])


def run_subject(
    subject: str,
    pdf: Path,
    *,
    exam_id: str | None,
    names: Path | None,
    keys_dir: Path,
    out_root: Path,
    dpi: int,
) -> SubjectRun:
    spec = SUBJECTS[subject]
    script = ROOT / spec["script"]
    if not script.exists():
        raise FileNotFoundError(script)
    if not pdf.exists():
        raise FileNotFoundError(pdf)

    out_dir = out_root / f"{subject}_{pdf.stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    before = set(existing_files(out_dir))

    cmd = [
        sys.executable,
        str(script),
        str(pdf),
        "--keys-dir",
        str(keys_dir),
        "--dpi",
        str(dpi),
        "--out",
        str(out_dir),
    ]
    if exam_id:
        cmd += ["--irecord", str(exam_id)]
    if names and spec.get("needs_names"):
        cmd += ["--names", str(names)]
    template = resolve_path(spec.get("template"))
    if template and template.exists():
        cmd += ["--template", str(template)]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    log_path = out_dir / "run_log.txt"
    log_path.write_text(proc.stdout, encoding="utf-8")
    after = set(existing_files(out_dir))
    produced = sorted(after - before)
    if log_path not in produced:
        produced.append(log_path)

    return SubjectRun(
        subject=subject,
        label=spec["label"],
        pdf=str(pdf),
        out_dir=str(out_dir),
        returncode=proc.returncode,
        command=cmd,
        log=str(log_path),
        files=[str(p) for p in produced],
    )


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data.get("subjects"), dict):
        raise SystemExit("manifest에는 subjects 객체가 필요합니다.")
    return data


def write_summary(run_dir: Path, runs: list[SubjectRun], manifest: dict[str, Any] | None = None) -> Path:
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "manifest": manifest or {},
        "ok": all(r.ok for r in runs),
        "runs": [asdict(r) | {"ok": r.ok} for r in runs],
    }
    path = run_dir / "batch_summary.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = run_dir / "batch_report.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["과목", "상태", "PDF", "출력폴더", "XLSX", "CSV", "로그"])
        for run in runs:
            xlsx = next((p for p in run.files if p.lower().endswith(".xlsx")), "")
            csv_file = next((p for p in run.files if p.lower().endswith(".csv")), "")
            writer.writerow([
                run.label,
                "완료" if run.ok else "실패",
                run.pdf,
                run.out_dir,
                xlsx,
                csv_file,
                run.log,
            ])
    return path


def command_score(args: argparse.Namespace) -> int:
    subject = normalize_subject(args.subject)
    out_root = resolve_path(args.out) or default_run_dir(ROOT / "output")
    out_root.mkdir(parents=True, exist_ok=True)
    run = run_subject(
        subject,
        resolve_path(args.pdf) or Path(args.pdf),
        exam_id=args.irecord,
        names=resolve_path(args.names),
        keys_dir=resolve_path(args.keys_dir) or ROOT / "keys",
        out_root=out_root,
        dpi=args.dpi,
    )
    summary = write_summary(out_root, [run])
    print(f"[{run.label}] {'완료' if run.ok else '실패'}: {run.out_dir}")
    print(f"요약: {summary}")
    return run.returncode


def command_batch(args: argparse.Namespace) -> int:
    manifest_path = resolve_path(args.manifest) or Path(args.manifest)
    manifest = load_manifest(manifest_path)
    out_root = resolve_path(args.out) or default_run_dir(ROOT / "output")
    out_root.mkdir(parents=True, exist_ok=True)

    exam_id = str(args.irecord or manifest.get("exam_id") or manifest.get("irecord") or "")
    names = resolve_path(args.names or manifest.get("names"), manifest_path.parent)
    keys_dir = resolve_path(args.keys_dir or manifest.get("keys_dir"), manifest_path.parent) or ROOT / "keys"
    dpi = int(args.dpi or manifest.get("dpi") or 300)

    runs: list[SubjectRun] = []
    for subject_name, pdf_value in manifest["subjects"].items():
        subject = normalize_subject(subject_name)
        pdf = resolve_path(pdf_value, manifest_path.parent) or Path(pdf_value)
        print(f"[{SUBJECTS[subject]['label']}] 처리 시작: {pdf}")
        run = run_subject(subject, pdf, exam_id=exam_id or None, names=names, keys_dir=keys_dir, out_root=out_root, dpi=dpi)
        runs.append(run)
        print(f"[{run.label}] {'완료' if run.ok else '실패'}: {run.out_dir}")

    summary = write_summary(out_root, runs, manifest)
    print(f"전체 요약: {summary}")
    return 0 if all(r.ok for r in runs) else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Score Lens OMR 통합 실행 프로그램")
    sub = ap.add_subparsers(dest="command", required=True)

    score = sub.add_parser("score", help="과목 PDF 하나를 판독/채점합니다.")
    score.add_argument("--subject", required=True, help="예: 국어, 수학, 영어, 한국사, 탐구")
    score.add_argument("--pdf", required=True, help="스캔 PDF 경로")
    score.add_argument("--irecord", default=None, help="EBSi 시험 ID")
    score.add_argument("--names", default=None, help="이름 매칭용 CSV")
    score.add_argument("--keys-dir", default="keys")
    score.add_argument("--dpi", type=int, default=300)
    score.add_argument("--out", default=None, help="출력 폴더")
    score.set_defaults(func=command_score)

    batch = sub.add_parser("batch", help="manifest에 적힌 여러 과목을 한 번에 처리합니다.")
    batch.add_argument("--manifest", required=True, help="batch manifest JSON")
    batch.add_argument("--irecord", default=None, help="manifest의 exam_id를 덮어씁니다.")
    batch.add_argument("--names", default=None, help="manifest의 names를 덮어씁니다.")
    batch.add_argument("--keys-dir", default=None)
    batch.add_argument("--dpi", type=int, default=None)
    batch.add_argument("--out", default=None, help="출력 루트 폴더")
    batch.set_defaults(func=command_batch)
    return ap


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
