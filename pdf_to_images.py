# -*- coding: utf-8 -*-
"""
① PDF -> 페이지별 고해상도 PNG 변환
산남고 OMR 채점기 / score_lens

사용법:
    python pdf_to_images.py input/scan.pdf
    python pdf_to_images.py input/scan.pdf --dpi 300 --out work

여러 학생 답안지가 한 PDF에 묶여 있을 때, 페이지마다 PNG 한 장씩 떨굽니다.
OMR 판독 정확도는 해상도에 민감하므로 기본 300 DPI(권장 200~300)로 변환합니다.
"""
import argparse
import sys
from pathlib import Path

import fitz  # PyMuPDF


def pdf_to_images(pdf_path: Path, out_dir: Path, dpi: int = 300) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0  # PDF 기본 72dpi 기준 배율
    mat = fitz.Matrix(zoom, zoom)
    saved: list[Path] = []
    stem = pdf_path.stem
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=mat, alpha=False)
        out = out_dir / f"{stem}_p{i:03d}.png"
        pix.save(out)
        saved.append(out)
        print(f"  [{i:>3}/{len(doc)}] {out.name}  ({pix.width}x{pix.height})")
    doc.close()
    return saved


def main() -> int:
    ap = argparse.ArgumentParser(description="OMR PDF -> 페이지별 PNG")
    ap.add_argument("pdf", type=Path, help="입력 PDF 경로")
    ap.add_argument("--dpi", type=int, default=300, help="변환 해상도 (기본 300)")
    ap.add_argument("--out", type=Path, default=Path("work"), help="출력 폴더 (기본 work/)")
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"[오류] 파일 없음: {args.pdf}", file=sys.stderr)
        return 1

    print(f"PDF 변환: {args.pdf}  ({args.dpi} DPI) -> {args.out}/")
    pages = pdf_to_images(args.pdf, args.out, args.dpi)
    print(f"완료: {len(pages)}장 변환됨 -> {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
