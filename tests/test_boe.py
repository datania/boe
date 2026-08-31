import ssl
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx

import boe


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_ssl_failure(self) -> None:
        requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            if requests == 1:
                raise ssl.SSLError("temporary failure")
            return httpx.Response(200, request=request, content=b"ok")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as session:
            with patch.object(boe, "RETRY_DELAY", 0):
                response = await boe.get_with_retry(session, "https://example.test")

        self.assertEqual(response.content, b"ok")
        self.assertEqual(requests, 2)

    async def test_raises_after_retry_limit(self) -> None:
        requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            raise ssl.SSLError("persistent failure")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as session:
            with (
                patch.object(boe, "RETRY_DELAY", 0),
                self.assertRaises(ssl.SSLError),
            ):
                await boe.get_with_retry(session, "https://example.test")

        self.assertEqual(requests, boe.MAX_RETRIES)


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_no_boe_for_404(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, request=request)

        with tempfile.TemporaryDirectory() as directory:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as session:
                result = await boe.process_date(
                    session, date(2025, 1, 1), Path(directory)
                )

        self.assertEqual(result, (0, 0, "no_boe"))

    async def test_preserves_multiple_issues_for_one_date(self) -> None:
        api_response = {
            "data": {
                "sumario": {
                    "diario": [
                        {
                            "sumario_diario": {
                                "identificador": "BOE-S-2026-210",
                                "url_pdf": {"texto": "https://files.test/210.pdf"},
                            }
                        },
                        {
                            "sumario_diario": {
                                "identificador": "BOE-S-2026-209",
                                "url_pdf": {"texto": "https://files.test/209.pdf"},
                            }
                        },
                    ]
                }
            }
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "www.boe.es":
                return httpx.Response(200, request=request, json=api_response)
            return httpx.Response(200, request=request, content=b"pdf")

        async def ensure_markdown(pdf_path: Path, md_path: Path) -> bool:
            md_path.write_text(pdf_path.name)
            return True

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(boe, "ensure_markdown", side_effect=ensure_markdown),
        ):
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as session:
                result = await boe.process_date(
                    session, date(2026, 8, 26), Path(directory)
                )

            files = sorted(
                path.name for path in Path(directory).rglob("*") if path.is_file()
            )

        self.assertEqual(result, (2, 2, "success"))
        self.assertEqual(
            files,
            [
                "BOE-S-2026-209.md",
                "BOE-S-2026-209.pdf",
                "BOE-S-2026-210.md",
                "BOE-S-2026-210.pdf",
            ],
        )

    async def test_isolates_unexpected_date_failure(self) -> None:
        async def process_date(
            session: httpx.AsyncClient, day: date, output_dir: Path
        ) -> boe.Result:
            if day == date(2025, 1, 1):
                raise RuntimeError("broken date")
            return 1, 1, "success"

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(boe, "process_date", side_effect=process_date),
        ):
            stats = await boe.download_boe_pdfs(
                date(2025, 1, 1), date(2025, 1, 2), 2, Path(directory)
            )

        self.assertEqual(stats.downloaded, 1)
        self.assertEqual(stats.generated_md, 1)
        self.assertEqual(stats.errors, 1)


if __name__ == "__main__":
    unittest.main()
