import argparse
import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

API_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{date}"
OUTPUT_DIR = Path("boe")
START_DATE = datetime(1961, 1, 1)
CONCURRENT_LIMIT = 50
MAX_RETRIES = 3
RETRY_DELAY = 30


async def download_pdf(session: httpx.AsyncClient, url: str, path: Path) -> None:
    """Download a PDF file with retry logic."""
    for attempt in range(MAX_RETRIES):
        try:
            response = await session.get(url)
            response.raise_for_status()

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            return
        except httpx.HTTPError as e:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise e


async def process_date(
    session: httpx.AsyncClient, date: datetime, output_dir: Path
) -> tuple[int, str]:
    """Process BOE for a specific date with retry logic."""
    date_str = date.strftime("%Y%m%d")

    # Check if PDF already exists (caching)
    pdf_path = output_dir / date.strftime("%Y/%m/%d") / "boe.pdf"
    if pdf_path.exists():
        return 0, "cached"

    data = None

    for attempt in range(MAX_RETRIES):
        try:
            response = await session.get(
                API_URL.format(date=date_str), headers={"Accept": "application/json"}
            )

            if response.status_code == 404:
                return 0, "no_boe"

            response.raise_for_status()
            data = response.json()
            break
        except (httpx.HTTPError, httpx.RemoteProtocolError) as e:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
            else:
                print(f"Failed after {MAX_RETRIES} attempts for {date_str}: {e}")
                return 0, "error"

    if data is None:
        return 0, "error"

    # Extract PDFs from response
    pdfs = []
    diarios = data.get("data", {}).get("sumario", {}).get("diario", [])
    if not isinstance(diarios, list):
        diarios = [diarios]

    for diario in diarios:
        sumario = diario.get("sumario_diario", {})
        if pdf_url := sumario.get("url_pdf", {}).get("texto"):
            identifier = sumario.get("identificador", "unknown")
            pdfs.append((pdf_url, identifier))

    # Download PDFs
    downloaded = 0
    for pdf_url, identifier in pdfs:
        pdf_path = output_dir / date.strftime("%Y/%m/%d") / "boe.pdf"

        try:
            await download_pdf(session, pdf_url, pdf_path)
            downloaded += 1
        except Exception as e:
            print(f"Failed to download {identifier} for {date_str}: {e}")

    return downloaded, "success"


async def download_boe_pdfs(
    start_date: datetime, end_date: datetime, concurrency: int, output_dir: Path
):
    """Download all BOE PDFs from start_date to end_date."""
    output_dir.mkdir(exist_ok=True)

    # Generate all dates
    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current)
        current += timedelta(days=1)

    print(
        f"Processing {len(dates)} days from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
    )

    stats = {"downloaded": 0, "no_boe": 0, "errors": 0, "cached": 0}
    start_time = time.time()

    async with httpx.AsyncClient(timeout=60.0) as session:
        sem = asyncio.Semaphore(concurrency)

        async def process_with_limit(date):
            async with sem:
                return await process_date(session, date, output_dir)

        # Process all dates with progress indication
        tasks = [process_with_limit(date) for date in dates]
        results = await asyncio.gather(*tasks)

        # Aggregate results
        for i, result in enumerate(results):
            count, status = result
            stats["downloaded"] += count
            if status == "no_boe":
                stats["no_boe"] += 1
            elif status == "error":
                stats["errors"] += 1
            elif status == "cached":
                stats["cached"] += 1

            # Progress indicator every 365 days
            if (i + 1) % 365 == 0:
                elapsed = time.time() - start_time
                print(
                    f"Progress: {i + 1}/{len(dates)} days processed in {elapsed:.1f}s"
                )

    elapsed = time.time() - start_time
    print(f"\nCompleted in {elapsed:.1f} seconds")
    print(f"Downloaded: {stats['downloaded']} PDFs")
    print(f"Already cached: {stats['cached']} PDFs")
    print(f"Days without BOE: {stats['no_boe']}")
    print(f"Errors: {stats['errors']}")


def main():
    parser = argparse.ArgumentParser(
        description="Download BOE PDFs from the Spanish Official Gazette."
    )
    parser.add_argument(
        "-s",
        "--start-date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d"),
        default=START_DATE,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "-e",
        "--end-date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d"),
        default=datetime.now(),
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
    asyncio.run(
        download_boe_pdfs(args.start_date, args.end_date, args.concurrency, args.output)
    )


if __name__ == "__main__":
    main()
