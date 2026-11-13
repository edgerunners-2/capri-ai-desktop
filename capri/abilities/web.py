"""Her netrunning: web search and reading pages, boiled down to plain text she can answer from. Search goes through
Brave's Search API or a SearXNG instance when one is set in config.yaml (search:), else keyless Wikipedia +
DuckDuckGo instant answers. Nothing is logged or saved."""
from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

UA = "CapriDesktopCompanion/0.2 (fan-made desktop assistant; python-requests)"  # honest, as Wikimedia asks
_session = None


def _http():
    global _session
    if _session is None:
        import requests

        _session = requests.Session()
        _session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})
    return _session


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment or "")).strip()


CFG: dict = {}  # cfg["search"], set by the agent: brave_api_key / searxng_url


def _brave(query: str, n: int) -> list[dict]:
    """Brave Search API: real web results, free key at https://brave.com/search/api/ (about 2k searches a month)."""
    r = _http().get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": n}, timeout=10,
                    headers={"X-Subscription-Token": CFG["brave_api_key"], "Accept": "application/json"})
    r.raise_for_status()
    return [{"title": h.get("title", ""), "url": h.get("url", ""), "snippet": _text(h.get("description"))}
            for h in r.json().get("web", {}).get("results", [])[:n]]


def _searxng(query: str, n: int) -> list[dict]:
    """A SearXNG instance's JSON API (self-hosted, or one that allows format=json)."""
    r = _http().get(CFG["searxng_url"].rstrip("/") + "/search", params={"q": query, "format": "json"}, timeout=10)
    r.raise_for_status()
    return [{"title": h.get("title", ""), "url": h.get("url", ""), "snippet": _text(h.get("content"))}
            for h in r.json().get("results", [])[:n]]


def _keyless(query: str, n: int) -> list[dict]:
    """No key set up: DuckDuckGo's instant-answer API plus Wikipedia's search. Facts and lore, not news or prices."""
    out = []
    try:
        d = _http().get("https://api.duckduckgo.com/", params={"q": query, "format": "json", "no_html": 1,
                                                                "skip_disambig": 1}, timeout=8).json()
        if d.get("AbstractText"):
            out.append({"title": d.get("Heading", query), "url": d.get("AbstractURL", ""), "snippet": d["AbstractText"]})
    except Exception:
        pass
    r = _http().get("https://en.wikipedia.org/w/api.php", timeout=8, params={
        "action": "query", "list": "search", "srsearch": query, "srlimit": n, "format": "json"})
    for h in r.json().get("query", {}).get("search", []):
        out.append({"title": h["title"], "url": "https://en.wikipedia.org/wiki/" + h["title"].replace(" ", "_"),
                    "snippet": _text(h.get("snippet"))})
    return out[:n]


def provider() -> str:
    return "brave" if CFG.get("brave_api_key") else "searxng" if CFG.get("searxng_url") else "keyless"


def search(query: str, n: int = 5) -> list[dict]:
    """[{title, url, snippet}], best first. Empty if the search is down."""
    try:
        return {"brave": _brave, "searxng": _searxng, "keyless": _keyless}[provider()](query, n)
    except Exception as e:
        print(f"[capri] search ({provider()}) failed: {e}")
        return []


class _Reader(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "button", "iframe"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "tr", "br", "section", "article", "td"}

    def __init__(self):
        super().__init__()
        self.parts, self.depth, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.depth:
            self.parts.append(data)


def read(url: str, limit: int = 2500) -> tuple[str, str]:
    """(title, main text) of a web page; ('', '') if it won't load or isn't a page."""
    if m := re.match(r"https?://en\.wikipedia\.org/wiki/([^#?]+)", url):
        return _wiki(unquote(m.group(1)), limit)
    try:
        r = _http().get(url, timeout=8)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
            return "", ""
        p = _Reader()
        p.feed(r.text[:600_000])
    except Exception as e:
        print(f"[capri] couldn't read {url}: {e}")
        return "", ""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in "".join(p.parts).splitlines()]
    lines = [ln for ln in lines if len(ln) > 40 or (len(ln) > 15 and ln[-1:] in ".!?:")]  # sentences, not menus
    return p.title.strip(), "\n".join(lines)[:limit]


def _wiki(title: str, limit: int) -> tuple[str, str]:
    """A Wikipedia article's plain text through its API (what Wikimedia wants bots to use)."""
    try:
        r = _http().get("https://en.wikipedia.org/w/api.php", timeout=8, params={
            "action": "query", "prop": "extracts", "explaintext": 1, "exchars": limit, "redirects": 1,
            "titles": title.replace("_", " "), "format": "json"})
        if r.status_code != 200:  # 429: Wikipedia asks bots to slow down; the search snippet will do
            return "", ""
        page = next(iter(r.json()["query"]["pages"].values()))
        return page.get("title", ""), page.get("extract", "")[:limit]
    except Exception as e:
        print(f"[capri] couldn't read Wikipedia {title}: {e}")
        return "", ""


def research(query: str, pages: int = 2) -> str:
    """Search, then read the top pages at the same time: a short digest for her to answer from."""
    hits = search(query)
    if not hits:
        return f'(You tried to search the web for "{query}" but the search came back empty or the net is down.)'
    with ThreadPoolExecutor(1 if provider() == "keyless" else pages) as pool:  # Wikipedia: one at a time
        bodies = list(pool.map(lambda h: read(h["url"], 1400)[1], hits[:pages]))
    out = [f'(You just searched the web for "{query}". What you found:']
    for i, h in enumerate(hits):
        site = urlparse(h["url"]).netloc.removeprefix("www.")
        body = bodies[i] if i < len(bodies) and bodies[i] else h["snippet"]
        out.append(f"- {h['title']} ({site}): {body[:1400] if i < pages else h['snippet']}")
    out.append("Answer from this, in your own voice and short. Use real names and numbers from it; don't invent any. "
               "If it doesn't answer the question, say so.)")
    return "\n".join(out)


def news_url(topic: str = "") -> str:
    """Google News in the browser: the front page, or a search for the topic."""
    from urllib.parse import quote_plus

    return f"https://news.google.com/search?q={quote_plus(topic)}" if topic else "https://news.google.com/"


def headlines(topic: str = "", n: int = 6) -> list[tuple[str, str]]:
    """[(headline, outlet)] from Google News' public RSS feed (no key), newest stories first. Empty when offline."""
    import xml.etree.ElementTree as ET
    from urllib.parse import quote_plus

    url = (f"https://news.google.com/rss/search?q={quote_plus(topic)}&hl=en-US&gl=US&ceid=US:en" if topic
           else "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en")
    try:
        r = _http().get(url, timeout=10)
        r.raise_for_status()
        out = []
        for item in ET.fromstring(r.content).iter("item"):
            title = (item.findtext("title") or "").strip()
            outlet = (item.findtext("source") or "").strip()
            if outlet and title.endswith(" - " + outlet):
                title = title[: -len(outlet) - 3]
            if title:
                out.append((html.unescape(title), outlet))
            if len(out) >= n:
                break
        return out
    except Exception as e:
        print(f"[capri] news failed: {e}")
        return []
