# -*- coding: utf-8 -*-
"""Import full answer/point keys from EBS XIP APIs."""
from __future__ import annotations

import argparse
import base64
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

XIP_BASE = "https://ai-plus.ebs.co.kr/ebs/xip"
SUBJECT_IDS = {
    "영어": "80003",
    "한국사": "63004",
    "생활과윤리": "63002",
    "윤리와사상": "63003",
    "한국지리": "141",
    "세계지리": "142",
    "동아시아사": "63001",
    "세계사": "146",
    "경제": "66002",
    "정치와법": "140112",
    "사회문화": "66001",
    "물리학Ⅰ": "140115",
    "화학Ⅰ": "156",
    "생명과학Ⅰ": "158",
    "지구과학Ⅰ": "154",
    "물리학Ⅱ": "140116",
    "화학Ⅱ": "157",
    "생명과학Ⅱ": "159",
    "지구과학Ⅱ": "155",
}
PAPER_IDS = {
    ("202606043", "영어"): "27037323",
    ("202606043", "한국사"): "27037324",
    ("202606043", "생활과윤리"): "27037325",
    ("202606043", "윤리와사상"): "27037326",
    ("202606043", "한국지리"): "27037327",
    ("202606043", "세계지리"): "27037328",
    ("202606043", "동아시아사"): "27037329",
    ("202606043", "세계사"): "27037330",
    ("202606043", "정치와법"): "27037331",
    ("202606043", "경제"): "27037332",
    ("202606043", "사회문화"): "27037333",
    ("202606043", "물리학Ⅰ"): "27037334",
    ("202606043", "화학Ⅰ"): "27037336",
    ("202606043", "생명과학Ⅰ"): "27037338",
    ("202606043", "지구과학Ⅰ"): "27037340",
    ("202606043", "물리학Ⅱ"): "27037335",
    ("202606043", "화학Ⅱ"): "27037337",
    ("202606043", "생명과학Ⅱ"): "27037339",
    ("202606043", "지구과학Ⅱ"): "27037341",
}


def _session() -> requests.Session:
    session = requests.Session()
    session.verify = False
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://ai-plus.ebs.co.kr/ebs/ai/xipa/loadSharePaper.ebs",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return session


def fetch_answers(session: requests.Session, paper_id: str) -> tuple[dict[int, int], dict[int, str], str]:
    url = f"{XIP_BASE}/selectCorrectAnswerInfo.ajax"
    response = session.post(
        url,
        data={"paperId": paper_id, "isMoc": "1", "mocType": "MOC", "site": "HSC"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("result") != 0:
        raise RuntimeError(f"answer API failed: {data}")

    answers: dict[int, int] = {}
    item_ids: dict[int, str] = {}
    for idx, row in enumerate(data.get("answerInfo") or [], 1):
        answers[idx] = int(row["correctAnswer"])
        item_ids[idx] = str(row["itemNo"])
    return answers, item_ids, url


def fetch_points(session: requests.Session, paper_id: str) -> tuple[dict[int, int], dict[int, str], str]:
    url = f"{XIP_BASE}/webservice/Paper.ebs"
    response = session.get(
        url,
        params={
            "paperId": paper_id,
            "isMoc": "1",
            "IsEncrypt": "false",
            "site": "HSC",
            "fmySiteDsCd": "HSC",
            "Action": "SelectGroup",
            "pMode": "false",
            "childrenUserId": "",
            "timeStamp": str(int(time.time() * 1000)),
        },
        timeout=30,
    )
    response.raise_for_status()
    raw = "".join(response.text.split())
    xml_text = base64.urlsafe_b64decode(raw).decode("utf-8", errors="replace")
    root = ET.fromstring(xml_text)

    points: dict[int, int] = {}
    item_ids: dict[int, str] = {}
    for detail in root.findall(".//PaperDetail"):
        number = int(detail.attrib.get("ItemNumber", "0") or "0")
        if number <= 0:
            continue
        item = detail.find("Item")
        if item is None:
            continue
        points[number] = int(item.attrib.get("Point", "0") or "0")
        item_ids[number] = str(item.attrib.get("ItemNo") or item.attrib.get("ID") or "")
    return points, item_ids, url


def write_key(exam_id: str, subject: str, paper_id: str, output_dir: Path) -> Path:
    session = _session()
    answers, answer_item_ids, answer_url = fetch_answers(session, paper_id)
    points, point_item_ids, paper_url = fetch_points(session, paper_id)

    missing = sorted(set(answers) ^ set(points))
    if missing:
        raise RuntimeError(f"answer/point item mismatch: {missing}")

    item_ids = {str(q): answer_item_ids.get(q) or point_item_ids.get(q, "") for q in sorted(answers)}
    payload = {
        "exam_id": exam_id,
        "subject": subject,
        "elective": subject,
        "paper_id": paper_id,
        "complete": len(answers) == len(points),
        "item_count": len(answers),
        "max_score": sum(points.values()),
        "answers": {str(q): answers[q] for q in sorted(answers)},
        "points": {str(q): points[q] for q in sorted(points)},
        "item_ids": item_ids,
        "source": {
            "answers_api": answer_url,
            "paper_api": paper_url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "note": "Answers from selectCorrectAnswerInfo.ajax; points from base64 Paper.ebs XML Item Point attributes.",
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{exam_id}_{subject}_xip_api.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Import full keys from EBS XIP APIs.")
    parser.add_argument("exam_id")
    parser.add_argument("--subject", default="영어", choices=sorted(SUBJECT_IDS))
    parser.add_argument("--paper-id", default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("keys"))
    args = parser.parse_args()

    paper_id = args.paper_id or PAPER_IDS.get((args.exam_id, args.subject))
    if not paper_id:
        raise SystemExit("paper id is required for this exam/subject")

    path = write_key(args.exam_id, args.subject, paper_id, args.output_dir)
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(path)
    print(f"{payload['item_count']} items, max_score={payload['max_score']}, complete={payload['complete']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
