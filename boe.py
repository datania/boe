import argparse
import asyncio
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
from markitdown import MarkItDown
from tqdm.asyncio import tqdm

API_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{date}"
OUTPUT_DIR = Path("boe")
START_DATE = date(1961, 1, 1)
CONCURRENT_LIMIT = 10
MAX_RETRIES = 3
RETRY_DELAY = 30

Status = Literal["success", "cached", "no_boe", "error"]
Result = tuple[int, int, Status]
NETWORK_ERRORS = (httpx.HTTPError, ssl.SSLError)


@dataclass
class Stats:
    downloaded: int = 0
    generated_md: int = 0
    no_boe: int = 0
    errors: int = 0
    cached: int = 0

    def record(self, result: Result) -> None:
        downloaded, generated_md, status = result
        self.downloaded += downloaded
        self.generated_md += generated_md
        if status == "no_boe":
            self.no_boe += 1
        elif status == "error":
            self.errors += 1
        elif status == "cached":
            self.cached += 1


def retry_delay(attempt: int, error: Exception) -> float:
    if isinstance(error, httpx.HTTPStatusError):
        retry_after = error.response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return float(retry_after)
    return RETRY_DELAY * (attempt + 1)


async def get_with_retry(
    session: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    not_found_ok: bool = False,
) -> httpx.Response:
    for attempt in range(MAX_RETRIES):
        try:
            response = await session.get(url, headers=headers)
            if not_found_ok and response.status_code == 404:
                return response
            response.raise_for_status()
            return response
        except NETWORK_ERRORS as error:
            if attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(retry_delay(attempt, error))

    raise RuntimeError("Retry loop completed without a response")


async def download_pdf(session: httpx.AsyncClient, url: str, path: Path) -> None:
    response = await get_with_retry(session, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)


def convert_pdf_to_markdown(pdf_path: Path, md_path: Path) -> None:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    converter = MarkItDown(enable_plugins=False)
    result = converter.convert(str(pdf_path))
    md_path.write_text(result.text_content, encoding="utf-8")


async def ensure_markdown(pdf_path: Path, md_path: Path) -> bool:
    if md_path.exists():
        return False

    await asyncio.to_thread(convert_pdf_to_markdown, pdf_path, md_path)
    return True


async def process_date(
    session: httpx.AsyncClient, day: date, output_dir: Path
) -> Result:
    date_str = day.strftime("%Y%m%d")
    pdf_path = output_dir / day.strftime("%Y/%m/%d") / "boe.pdf"
    md_path = pdf_path.with_suffix(".md")

    if pdf_path.exists() and md_path.exists():
        return 0, 0, "cached"

    if pdf_path.exists():
        generated = await ensure_markdown(pdf_path, md_path)
        return 0, int(generated), "success"

    try:
        response = await get_with_retry(
            session,
            API_URL.format(date=date_str),
            headers={"Accept": "application/json"},
            not_found_ok=True,
        )
    except NETWORK_ERRORS as error:
        print(f"Failed to fetch {date_str} after {MAX_RETRIES} attempts: {error}")
        return 0, 0, "error"

    if response.status_code == 404:
        return 0, 0, "no_boe"

    try:
        data = response.json()
    except ValueError as error:
        print(f"Invalid API response for {date_str}: {error}")
        return 0, 0, "error"

    if not isinstance(data, dict):
        print(f"Invalid API response for {date_str}: expected an object")
        return 0, 0, "error"

    data_section = data.get("data")
    sumario_section = (
        data_section.get("sumario") if isinstance(data_section, dict) else None
    )
    diarios = (
        sumario_section.get("diario") if isinstance(sumario_section, dict) else None
    )
    if not isinstance(diarios, (dict, list)):
        print(f"Invalid API response for {date_str}: missing diario")
        return 0, 0, "error"
    if isinstance(diarios, dict):
        diarios = [diarios]

    pdfs: list[tuple[str, str]] = []
    for diario in diarios:
        if not isinstance(diario, dict):
            print(f"Invalid API response for {date_str}: invalid diario")
            return 0, 0, "error"
        sumario = diario.get("sumario_diario")
        if not isinstance(sumario, dict):
            continue
        url_pdf = sumario.get("url_pdf")
        pdf_url = url_pdf.get("texto") if isinstance(url_pdf, dict) else None
        if isinstance(pdf_url, str):
            identifier = str(sumario.get("identificador", "unknown"))
            pdfs.append((pdf_url, identifier))

    if len(pdfs) > 1:
        print(f"Invalid API response for {date_str}: expected at most one PDF")
        return 0, 0, "error"

    downloaded = 0
    generated_md = 0
    for pdf_url, identifier in pdfs:
        try:
            await download_pdf(session, pdf_url, pdf_path)
            downloaded += 1
            generated_md += int(await ensure_markdown(pdf_path, md_path))
        except NETWORK_ERRORS as error:
            print(f"Failed to download {identifier} for {date_str}: {error}")
            return downloaded, generated_md, "error"

    return downloaded, generated_md, "success"


async def download_boe_pdfs(
    start_date: date, end_date: date, concurrency: int, output_dir: Path
) -> Stats:
    output_dir.mkdir(parents=True, exist_ok=True)
    dates = [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]

    print(f"Processing {len(dates)} days from {start_date} to {end_date}\n")

    stats = Stats()
    start_time = time.monotonic()

    async with httpx.AsyncClient(timeout=60.0) as session:
        semaphore = asyncio.Semaphore(concurrency)

        async def process_with_limit(day: date) -> Result:
            async with semaphore:
                try:
                    return await process_date(session, day, output_dir)
                except Exception as error:  # noqa: BLE001 - isolate failures by date
                    print(f"Unexpected failure for {day}: {error}")
                    return 0, 0, "error"

        tasks = [process_with_limit(day) for day in dates]
        for task in tqdm.as_completed(
            tasks, total=len(tasks), desc="Processing BOE files"
        ):
            stats.record(await task)

    elapsed = time.monotonic() - start_time
    print(f"\n\nCompleted in {elapsed:.1f} seconds")
    print(f"Downloaded: {stats.downloaded} PDFs")
    print(f"Generated: {stats.generated_md} Markdown files")
    print(f"Already cached: {stats.cached} days")
    print(f"Days without BOE: {stats.no_boe}")
    print(f"Errors: {stats.errors}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download BOE PDFs and Markdown files from the Spanish Official Gazette."
    )
    parser.add_argument(
        "-s",
        "--start-date",
        type=date.fromisoformat,
        default=START_DATE,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "-e",
        "--end-date",
        type=date.fromisoformat,
        default=datetime.now(UTC).date(),
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=CONCURRENT_LIMIT,
        help=f"Number of concurrent downloads (default: {CONCURRENT_LIMIT})",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )

    args = parser.parse_args()
    if args.start_date > args.end_date:
        parser.error("--start-date must not be after --end-date")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    stats = asyncio.run(
        download_boe_pdfs(args.start_date, args.end_date, args.concurrency, args.output)
    )
    if stats.errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
