import asyncio
from pathlib import Path
import threading
import time

import httpx

from app.providers.catalog import parse_nlc, nlc_candidates, lookup
from app.workers import LookupWorker

FIXTURE = Path(__file__).parent / "fixtures" / "nlc_record.html"


def test_synthetic_nlc_fixture_preserves_roles_and_classification():
    record = parse_nlc(FIXTURE.read_text(encoding="utf-8"), "9787111544937")
    assert record.title == "深入理解计算机系统"
    assert record.publisher == "机械工业出版社"
    assert record.clc_code == "TP303"
    assert record.reference_pubdate == "2016"
    assert "龚奕利" in record.translators and "贺莲" in record.translators
    assert "龚奕利" not in record.authors
    assert len(record.summary) > 20
    assert record.source_info["responsibility"]
    assert parse_nlc(FIXTURE.read_text(encoding="utf-8"), "9780306406157") is None


def test_nlc_candidates_are_links_not_books():
    html = '''<table><tr><td>书名甲 出版社甲 2020</td><td><a href="/F/SESSION?func=full-set-set&amp;set_number=1&amp;set_entry=1">详情</a></td></tr>
    <tr><td>书名乙 出版社乙 2021</td><td><a href="/F/SESSION?func=full-set-set&amp;set_number=1&amp;set_entry=2">详情</a></td></tr></table>'''
    items = nlc_candidates(html, "http://opac.nlc.cn/F")
    assert len(items) == 2
    assert "书名甲" in items[0].title
    assert items[0].detail_url.startswith("http://opac.nlc.cn/")
    assert items[0].isbn == ""


def test_nlc_request_has_no_application_timeout():
    async def handler(request):
        assert all(v is None for v in request.extensions["timeout"].values())
        assert request.url.params["request"] == "9787111544937"
        return httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))
    records, note = asyncio.run(lookup("9787111544937", "nlc", transport=httpx.MockTransport(handler)))
    assert records[0]["clc_code"] == "TP303"


def test_failed_cover_does_not_discard_edition_or_use_work_year():
    async def handler(request):
        path = request.url.path
        if path.startswith("/isbn/"):
            return httpx.Response(200, json={"title": "Edition Title", "publish_date": "2019", "publishers": ["Publisher"], "works": [{"key": "/works/W1"}]})
        if path == "/works/W1.json":
            return httpx.Response(200, json={"description": "Work description", "first_publish_date": "1880"})
        return httpx.Response(404)
    records, _ = asyncio.run(lookup("9780306406157", "openlibrary", transport=httpx.MockTransport(handler)))
    assert records[0]["reference_pubdate"] == "2019"
    assert records[0]["summary"] == "Work description"
    assert records[0]["clc_code"] == ""
    assert records[0]["cover_blob"] is None


def test_google_does_not_convert_categories_to_clc():
    async def handler(request):
        return httpx.Response(200, json={"items": [{"volumeInfo": {"title": "A Book", "categories": ["Computers"], "industryIdentifiers": [{"identifier": "9780306406157"}]}}]})
    records, _ = asyncio.run(lookup("9780306406157", "google", key="test-key", transport=httpx.MockTransport(handler)))
    assert records[0]["clc_code"] == ""


def test_actual_worker_cancellation_closes_inflight_stream():
    entered, closed = threading.Event(), threading.Event()
    class BlockingBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.sleep(120)
            yield b"not reached"
        async def aclose(self):
            closed.set()
    async def handler(request):
        return httpx.Response(200, stream=BlockingBody())
    async def fake_lookup(*args, **kwargs):
        return await lookup(*args, **kwargs, transport=httpx.MockTransport(handler))
    worker = LookupWorker(1, "9787111544937", "nlc", lookup_func=fake_lookup)
    worker.start()
    assert entered.wait(3)
    start = time.monotonic()
    worker.cancel()
    assert worker.wait(3000)
    assert time.monotonic() - start < 3
    assert closed.is_set()
