# -*- coding: utf-8 -*-
"""Import answer/point data exposed by EBSi's wrong-answer-rate tab."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests
from lxml import html

from ebsi_keys import EbsiClient


CHOICES = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5, "Ёз": 1, "Ёи": 2, "Ёй": 3, "Ёк": 4, "Ёл": 5}
SUBJECT_IDS = {"영어": "80003"}


def fetch_wrong_rate_rows(exam_id: str, subject_id: str) -> tuple[list[dict], str]:
    url = f"https://www.ebsi.co.kr/ebs/xip/xipa/retrieveMainWrongList.ajax?irecord={exam_id}"
    text = EbsiClient().text(url)
    doc = html.fromstring(text)
    rows = []
    seen = set()
    for tr in doc.xpath(f'//tr[contains(@class, "subj_{subject_id}") and @name="orderWrong"]'):
        cells = [" ".join(td.text_content().split()) for td in tr.xpath("./td")]
        if len(cells) < 5:
            continue
        q = int(cells[1])
        if q in seen:
            continue
        seen.add(q)
        answer = CHOICES.get(cells[4])
        if answer is None:
            raise ValueError(f"unknown answer marker for q{q}: {cells[4]!r}")
        rows.append(
            {
                "rank": int(cells[0]),
                "number": q,
                "wrong_rate": float(cells[2]),
                "points": int(cells[3]),
                "answer": answer,
            }
        )
    return rows, url


def write_key(exam_id: str, subject: str, rows: list[dict], source_url: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    answers = {str(r["number"]): r["answer"] for r in rows}
    points = {str(r["number"]): r["points"] for r in rows}
    payload = {
        "exam_id": exam_id,
        "subject": subject,
        "elective": subject,
        "complete": len(rows) == 45,
        "item_count": len(rows),
        "max_score": sum(points.values()),
        "answers": answers,
        "points": points,
        "wrong_rate_rows": rows,
        "source": {
            "page": source_url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "note": "EBSi wrong-answer-rate tab exposes TOP15 rows for this subject.",
        },
    }
    path = output_dir / f"{exam_id}_{subject}_오답률분석.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Import key rows from EBSi wrong-answer-rate tab.")
    parser.add_argument("exam_id")
    parser.add_argument("--subject", default="영어", choices=sorted(SUBJECT_IDS))
    parser.add_argument("--output-dir", type=Path, default=Path("keys"))
    args = parser.parse_args()
    rows, source_url = fetch_wrong_rate_rows(args.exam_id, SUBJECT_IDS[args.subject])
    if not rows:
        raise SystemExit(f"no rows found for {args.subject}")
    path = write_key(args.exam_id, args.subject, rows, source_url, args.output_dir)
    print(path)
    print(f"{len(rows)} rows, complete={len(rows) == 45}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
