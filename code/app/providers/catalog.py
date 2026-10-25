import asyncio
from dataclasses import asdict
import re
import time
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup
import httpx

from ..domain import Metadata, normalize_isbn

NLC_URL = "http://opac.nlc.cn/F"
AGENT = "PersonalLibrary/1.0 (desktop personal catalog; sequential ISBN lookup)"
_last_openlibrary = 0.0


def text_value(value):
    return value.get("value", "") if isinstance(value, dict) else str(value or "")


def isbn_matches(values, requested):
    for value in values:
        try:
            if normalize_isbn(value) == requested:
                return True
        except ValueError:
            continue
    return False


def parse_nlc(html, requested, url=NLC_URL):
    """Parse labeled record cells without executing the catalog's scripts."""
    soup = BeautifulSoup(html, "html.parser")
    fields, last_label = {}, ""
    known = {"题名与责任", "出版项", "著者", "中图分类号", "内容提要", "ISBN", "ISBN/价格", "版本项"}
    for row in soup.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 2:
            continue
        label = cells[0].get_text(" ", strip=True).replace("\xa0", " ").strip(" :：")
        if label in known:
            last_label = label
        elif label:
            last_label = ""
        if last_label:
            value = " ".join(cells[1].get_text(" ", strip=True).split())
            if value:
                fields.setdefault(last_label, []).append(value)
    if not fields.get("题名与责任"):
        return None
    raw_isbns = re.findall(r"(?<!\d)(?:97[89][\-\s]?)?\d[\d\-\s]{7,18}[\dXx](?!\d)",
                           " ".join(fields.get("ISBN", []) + fields.get("ISBN/价格", [])))
    for hidden in soup.find_all("input"):
        if "ISBN" in str(hidden.get("name", "")).upper():
            raw_isbns += str(hidden.get("value", "")).split()
    # Require the returned record to identify the requested edition.
    if not isbn_matches(raw_isbns, requested):
        return None
    responsibility = fields["题名与责任"][0]
    title = re.split(r"[=/／]", responsibility, maxsplit=1)[0]
    title = re.sub(r"\[[^\]]*\]", "", title).strip()
    authors, translators, unknown = [], [], []
    for person in fields.get("著者", []):
        if re.search(r"译(?:者)?$", person):
            translators.append(re.sub(r"\s*译(?:者)?$", "", person))
        elif re.search(r"(?:著|编著|主编|编|编者)$", person):
            authors.append(re.sub(r"\s*(?:编著|主编|编者|著|编)$", "", person))
        else:
            unknown.append(person)
    if not translators:
        for part in re.split(r"[;；]", responsibility.split("/", 1)[-1]):
            if re.search(r"译\s*$", part):
                translators.append(re.sub(r"译\s*$", "", part).strip())
    publication = " ; ".join(fields.get("出版项", []))
    publisher = re.split(r"[,，]", re.split(r"[:：]", publication, maxsplit=1)[-1])[0].strip()
    years = re.findall(r"(?<!\d)(?:1[0-9]{3}|20[0-9]{2})(?!\d)", publication)
    classifications = fields.get("中图分类号", [])
    codes = list(dict.fromkeys(re.findall(r"[A-Z][A-Z0-9.\-/=():+]*", " ; ".join(classifications))))
    info = {"provider": "国家图书馆", "url": url, "responsibility": responsibility,
            "raw_classification": " ; ".join(classifications), "clc_candidates": codes,
            "unassigned_contributors": unknown, "edition": " ; ".join(fields.get("版本项", []))}
    return Metadata(isbn=requested, title=title, authors="; ".join(authors),
                    translators="; ".join(translators), publisher=publisher,
                    clc_code=codes[0] if len(codes) == 1 else "",
                    summary="\n".join(fields.get("内容提要", [])),
                    reference_pubdate=years[-1] if years else "", source_info=info)


def nlc_candidates(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    results, seen = [], set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        match = re.search(r"[\"'](https?://[^\"']+)[\"']", href)
        if href.lower().startswith("javascript:"):
            if not match:
                continue
            href = match.group(1)
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if parsed.hostname != "opac.nlc.cn" or params.get("func", [""])[0] not in ("full-set-set", "full-set-set_body"):
            continue
        identity = (params.get("set_number", [""])[0], params.get("set_entry", [""])[0])
        if identity in seen:
            continue
        seen.add(identity)
        row = link.find_parent("tr")
        label = " ".join((row or link).get_text(" ", strip=True).split())[:500]
        results.append(Metadata(title=label or f"候选 {len(results)+1}", detail_url=url,
                                source_info={"provider": "国家图书馆"}))
    return results


async def nlc(client, isbn, detail_url=""):
    if detail_url:
        if urlparse(detail_url).hostname != "opac.nlc.cn":
            raise ValueError("无效的国图详情地址")
        response = await client.get(detail_url, timeout=None)
    else:
        response = await client.get(NLC_URL, params={"func": "find-b", "find_code": "ISB", "request": isbn,
                                                    "local_base": "NLC01"}, timeout=None)
    response.raise_for_status()
    record = parse_nlc(response.text, isbn, str(response.url))
    if record:
        return [record]
    candidates = nlc_candidates(response.text, str(response.url))
    if candidates and not detail_url:
        return candidates
    plain = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
    if any(marker in plain for marker in ("未找到", "没有命中", "无记录", "没有记录", "检索结果为零", "No records", "no records")):
        return []
    raise ValueError("国图未返回 ISBN 匹配的可解析书目；可改用其他来源或手工录入")


async def ol_get(client, url):
    global _last_openlibrary
    await asyncio.sleep(max(0, 1.05 - (time.monotonic() - _last_openlibrary)))
    _last_openlibrary = time.monotonic()
    response = await client.get(url, timeout=25)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


async def fetch_cover(client, url):
    try:
        async with client.stream("GET", url, timeout=20) as response:
            response.raise_for_status()
            if not response.headers.get("content-type", "").startswith("image/"):
                return None
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 12_000_000:
                    return None
                chunks.append(chunk)
        from ..imaging import decode_image, encode_cover
        return encode_cover(decode_image(b"".join(chunks)))
    except (httpx.HTTPError, ValueError):
        return None


async def openlibrary(client, isbn):
    edition = await ol_get(client, f"https://openlibrary.org/isbn/{isbn}.json")
    if not edition or not edition.get("title"):
        return []
    info = {"provider": "Open Library", "url": "https://openlibrary.org" + edition.get("key", "")}
    data = Metadata(isbn=isbn, title=edition["title"], publisher="; ".join(edition.get("publishers", [])),
                    reference_pubdate=edition.get("publish_date", ""), summary=text_value(edition.get("description")), source_info=info)
    # Optional enrichment must never discard a successfully fetched edition.
    try:
        contributors = edition.get("authors", [])
        if edition.get("works"):
            work = await ol_get(client, "https://openlibrary.org" + edition["works"][0]["key"] + ".json")
            if work:
                data.summary = data.summary or text_value(work.get("description"))
                contributors = contributors or [a.get("author", a) for a in work.get("authors", [])]
        authors = []
        for author in contributors[:8]:
            item = await ol_get(client, "https://openlibrary.org" + author["key"] + ".json")
            if item and item.get("name"):
                authors.append(item["name"])
        data.authors = "; ".join(authors)
    except (httpx.HTTPError, ValueError, KeyError):
        info["enrichment"] = "部分补充字段未能获取"
    data.translators = "; ".join(c.get("name", "") for c in edition.get("contributions", [])
                                 if isinstance(c, dict) and "translat" in c.get("role", "").lower())
    covers = [c for c in edition.get("covers", []) if c > 0]
    url = f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg?default=false" if covers else f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
    data.cover_blob = await fetch_cover(client, url)
    if data.cover_blob:
        data.cover_mime = "image/jpeg"
    return [data]


async def google(client, isbn, key):
    if not key:
        raise ValueError("Google Books 尚未配置 Key，请选择其他免费来源")
    response = await client.get("https://www.googleapis.com/books/v1/volumes",
                                params={"q": f"isbn:{isbn}", "key": key, "maxResults": 20}, timeout=25)
    response.raise_for_status()
    records = []
    for item in response.json().get("items", []):
        info = item.get("volumeInfo", {})
        if not isbn_matches([i.get("identifier", "") for i in info.get("industryIdentifiers", [])], isbn):
            continue
        record = Metadata(isbn=isbn, title=info.get("title", ""), authors="; ".join(info.get("authors", [])),
                          publisher=info.get("publisher", ""), reference_pubdate=info.get("publishedDate", ""),
                          summary=BeautifulSoup(info.get("description", ""), "html.parser").get_text(),
                          source_info={"provider": "Google Books", "url": info.get("infoLink", "")})
        url = info.get("imageLinks", {}).get("thumbnail", "").replace("http://", "https://", 1)
        if url:
            record.cover_blob = await fetch_cover(client, url)
            if record.cover_blob:
                record.cover_mime = "image/jpeg"
        records.append(record)
    return records


async def lookup(isbn, source="auto", key="", detail_url="", status=None, transport=None):
    isbn = normalize_isbn(isbn)
    sources = (["nlc", "openlibrary"] + (["google"] if key else [])) if source == "auto" else [source]
    messages = []
    async with httpx.AsyncClient(headers={"User-Agent": AGENT}, follow_redirects=True, transport=transport) as client:
        for name in sources:
            if status:
                status({"nlc": "国家图书馆（无应用层超时）", "openlibrary": "Open Library", "google": "Google Books", "manual": "仅手工"}[name])
            try:
                if name == "manual":
                    return [], "仅手工模式，请直接填写资料"
                records = await (nlc(client, isbn, detail_url) if name == "nlc" else
                                 openlibrary(client, isbn) if name == "openlibrary" else google(client, isbn, key))
                if records:
                    return [asdict(r) for r in records], "；".join(messages)
                messages.append(f"{name}：未找到匹配书目")
            except httpx.HTTPStatusError as exc:
                messages.append(f"{name}：HTTP {exc.response.status_code}，请稍后重试或手工录入")
            except httpx.HTTPError as exc:
                messages.append(f"{name}：网络连接失败（{type(exc).__name__}）")
            except (ValueError, KeyError, TypeError) as exc:
                # Avoid including request URLs/API keys in errors or logs.
                messages.append(f"{name}：{str(exc) if isinstance(exc, ValueError) and not key else '来源暂不可用'}")
    return [], "；".join(messages)
