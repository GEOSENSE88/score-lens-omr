# -*- coding: utf-8 -*-
"""EBSi 풀서비스에서 국어 정답과 배점을 내려받아 채점용 JSON으로 만든다."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import fitz
import requests
import urllib3
from lxml import html


EBSI_HOSTS = {"www.ebsi.co.kr", "wdown.ebsi.co.kr"}
CHOICES = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}
SECTIONS = ("공통", "화법과 작문", "언어와 매체")


class EbsiImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadLinks:
    questions: str
    solutions: str


class EbsiClient:
    """EBSi 전용 HTTP 클라이언트.

    일부 Windows 교육청/학교망에서는 로컬 보안 인증서 때문에 TLS 검증이
    실패한다. 먼저 정상 검증을 시도하고, EBSi 고정 호스트에 한해서만 경고 후
    재시도한다.
    """

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/126 Safari/537.36",
                "Referer": "https://www.ebsi.co.kr/",
            }
        )

    def get(self, url: str) -> requests.Response:
        host = (urlparse(url).hostname or "").lower()
        if host not in EBSI_HOSTS:
            raise EbsiImportError(f"허용되지 않은 다운로드 호스트입니다: {host}")
        try:
            response = self.session.get(url, timeout=45)
        except requests.exceptions.SSLError:
            warnings.warn(
                "이 컴퓨터의 인증서 체인으로 EBSi TLS 검증에 실패하여 "
                "EBSi 고정 호스트에 한해 인증서 검증 없이 다시 연결합니다.",
                RuntimeWarning,
            )
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            response = self.session.get(url, timeout=45, verify=False)
        response.raise_for_status()
        return response

    def text(self, url: str) -> str:
        response = self.get(url)
        response.encoding = "utf-8"
        return response.text

    def download(self, url: str, path: Path) -> None:
        path.write_bytes(self.get(url).content)


def exam_id_from_url(url: str) -> str:
    exam_id = parse_qs(urlparse(url).query).get("irecord", [""])[0]
    if not re.fullmatch(r"\d{9}", exam_id):
        raise EbsiImportError("EBSi 주소에서 9자리 irecord 값을 찾지 못했습니다.")
    return exam_id


def discover_korean_links(client: EbsiClient, exam_id: str) -> DownloadLinks:
    top_url = (
        "https://www.ebsi.co.kr/ebs/xip/xipt/"
        f"RetrieveSCVMainTop.ajax?irecord={exam_id}"
    )
    document = html.fromstring(client.text(top_url))
    found: dict[str, str] = {}
    for anchor in document.xpath("//a[contains(@onclick, 'goDownMain')]"):
        if "국어" not in " ".join(anchor.text_content().split()):
            continue
        onclick = anchor.get("onclick", "")
        match = re.search(r"goDownMain([PH])\(['\"]([^'\"]+)", onclick)
        if match:
            found[match.group(1)] = match.group(2)
    if "P" not in found or "H" not in found:
        raise EbsiImportError("EBSi 페이지에서 국어 문제지/정답·해설 링크를 찾지 못했습니다.")
    return DownloadLinks(questions=found["P"], solutions=found["H"])


def _answer_table_after_heading(page_text: str, heading: str, expected: int) -> dict[int, int]:
    start = page_text.find(heading)
    if start < 0:
        return {}
    tail = page_text[start + len(heading) :]
    answers: dict[int, int] = {}
    for number, choice in re.findall(r"\b(\d{1,2})\.\s*([①②③④⑤])", tail):
        q = int(number)
        answers.setdefault(q, CHOICES[choice])
        if len(answers) == expected:
            break
    return answers


def read_korean_answers(solution_pdf: Path) -> dict[str, dict[int, int]]:
    result: dict[str, dict[int, int]] = {}
    with fitz.open(solution_pdf) as document:
        for page in document:
            text = page.get_text()
            if "■ [공통:" in text:
                result["공통"] = _answer_table_after_heading(text, "■ [공통:", 34)
            if "■ [선택: 화법과 작문]" in text:
                result["화법과 작문"] = _answer_table_after_heading(
                    text, "■ [선택: 화법과 작문]", 11
                )
            if "■ [선택: 언어와 매체]" in text:
                result["언어와 매체"] = _answer_table_after_heading(
                    text, "■ [선택: 언어와 매체]", 11
                )
    expected_ranges = {
        "공통": set(range(1, 35)),
        "화법과 작문": set(range(35, 46)),
        "언어와 매체": set(range(35, 46)),
    }
    for section, expected_questions in expected_ranges.items():
        actual = set(result.get(section, {}))
        if actual != expected_questions:
            missing = sorted(expected_questions - actual)
            raise EbsiImportError(f"{section} 정답표 추출 실패(누락: {missing})")
    return result


def _question_markers(page: fitz.Page) -> list[tuple[float, float, int]]:
    markers: list[tuple[float, float, int]] = []
    for block in page.get_text("blocks"):
        for match in re.finditer(r"(?:^|\n)(\d{1,2})\.", block[4]):
            markers.append((block[0], block[1], int(match.group(1))))
    return markers


def read_korean_points(question_pdf: Path) -> dict[str, dict[int, int]]:
    three_point: dict[str, set[int]] = {section: set() for section in SECTIONS}
    second_elective_start: int | None = None
    pages: list[tuple[int, fitz.Page]] = []

    with fitz.open(question_pdf) as document:
        # 문제 번호 35가 처음 등장하면 화작, 두 번째 등장하면 언매가 시작된다.
        q35_pages: list[int] = []
        all_markers: dict[int, list[tuple[float, float, int]]] = {}
        for page_index, page in enumerate(document):
            markers = _question_markers(page)
            all_markers[page_index] = markers
            if any(q == 35 for _, _, q in markers):
                q35_pages.append(page_index)
        if len(q35_pages) < 2:
            raise EbsiImportError("문제지에서 두 선택과목의 시작 문항(35번)을 찾지 못했습니다.")
        second_elective_start = q35_pages[1]

        for page_index, page in enumerate(document):
            width = page.rect.width
            markers = all_markers[page_index]
            for block in page.get_text("blocks"):
                if "[3점]" not in block[4]:
                    continue
                column = 0 if block[0] < width / 2 else 1
                candidates = [
                    item
                    for item in markers
                    if (0 if item[0] < width / 2 else 1) == column
                    and item[1] <= block[1] + 3
                ]
                if not candidates:
                    raise EbsiImportError(
                        f"문제지 {page_index + 1}쪽의 3점 문항 번호를 찾지 못했습니다."
                    )
                q = max(candidates, key=lambda item: item[1])[2]
                if q <= 34:
                    section = "공통"
                elif page_index < second_elective_start:
                    section = "화법과 작문"
                else:
                    section = "언어와 매체"
                three_point[section].add(q)

    points = {
        "공통": {q: (3 if q in three_point["공통"] else 2) for q in range(1, 35)},
        "화법과 작문": {
            q: (3 if q in three_point["화법과 작문"] else 2) for q in range(35, 46)
        },
        "언어와 매체": {
            q: (3 if q in three_point["언어와 매체"] else 2) for q in range(35, 46)
        },
    }
    for elective in ("화법과 작문", "언어와 매체"):
        total = sum(points["공통"].values()) + sum(points[elective].values())
        if total != 100:
            raise EbsiImportError(f"{elective} 배점 합계가 100점이 아닙니다: {total}점")
    return points


def write_key_files(
    output_dir: Path,
    exam_id: str,
    source_url: str,
    links: DownloadLinks,
    answers: dict[str, dict[int, int]],
    points: dict[str, dict[int, int]],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for elective in ("화법과 작문", "언어와 매체"):
        merged_answers = answers["공통"] | answers[elective]
        merged_points = points["공통"] | points[elective]
        payload = {
            "exam_id": exam_id,
            "subject": "국어",
            "elective": elective,
            "max_score": sum(merged_points.values()),
            "answers": {str(q): merged_answers[q] for q in range(1, 46)},
            "points": {str(q): merged_points[q] for q in range(1, 46)},
            "source": {
                "page": source_url,
                "questions_pdf": links.questions,
                "solutions_pdf": links.solutions,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        path = output_dir / f"{exam_id}_국어_{elective.replace(' ', '')}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    return written


def import_korean_keys(source_url: str, output_dir: Path) -> list[Path]:
    exam_id = exam_id_from_url(source_url)
    client = EbsiClient()
    links = discover_korean_links(client, exam_id)
    with tempfile.TemporaryDirectory(prefix="ebsi_keys_") as temp_dir:
        temp = Path(temp_dir)
        question_pdf = temp / "questions.pdf"
        solution_pdf = temp / "solutions.pdf"
        client.download(links.questions, question_pdf)
        client.download(links.solutions, solution_pdf)
        answers = read_korean_answers(solution_pdf)
        points = read_korean_points(question_pdf)
    return write_key_files(
        output_dir, exam_id, source_url, links, answers, points
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="EBSi 풀서비스 주소에서 국어 정답·배점을 자동으로 가져옵니다."
    )
    parser.add_argument("url", help="irecord가 포함된 EBSi 풀서비스 주소")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "keys",
        help="JSON 저장 폴더(기본값: omr_scorer/keys)",
    )
    args = parser.parse_args()
    try:
        paths = import_korean_keys(args.url, args.output_dir)
    except (EbsiImportError, requests.RequestException, fitz.FileDataError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    for path in paths:
        print(path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
