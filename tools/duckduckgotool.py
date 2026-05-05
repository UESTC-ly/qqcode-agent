from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from tools.base import BaseTool


class DuckduckgoTool(BaseTool):
    name = "duckduckgotool"
    description = """
    Performs a search using DuckDuckGo and returns the top search results.
    Returns titles, snippets, and URLs of the search results.
    Use this tool when you need to search for current information on the internet.
    """
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to look up"
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results to return (default: 8)",
                "default": 8
            }
        },
        "required": ["query"]
    }

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _clean_url(self, href: str | None) -> str | None:
        if not href:
            return None

        href = href.strip()
        if href.startswith("//"):
            href = f"https:{href}"
        elif href.startswith("/"):
            return None

        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        if "uddg" in query and query["uddg"]:
            return unquote(query["uddg"][0])

        return href

    def _parse_html_results(self, html: str, num_results: int) -> list[dict[str, str | None]]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, str | None]] = []

        for result in soup.select(".result"):
            title_link = result.select_one("a.result__a")
            title_elem = title_link or result.select_one(".result__title")
            snippet_elem = result.select_one(".result__snippet")
            url_elem = title_link or result.select_one(".result__url")

            if not title_elem:
                continue

            title = title_elem.get_text(" ", strip=True)
            snippet = snippet_elem.get_text(" ", strip=True) if snippet_elem else ""
            url = self._clean_url(url_elem.get("href") if url_elem else None)

            if title and url:
                results.append({"title": title, "snippet": snippet, "url": url})

            if len(results) >= num_results:
                break

        return results

    def _parse_lite_results(self, html: str, num_results: int) -> list[dict[str, str | None]]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[dict[str, str | None]] = []
        seen_urls: set[str] = set()

        for link in soup.select('a[href]'):
            href = link.get("href")
            title = link.get_text(" ", strip=True)
            url = self._clean_url(href)

            netloc = urlparse(url).netloc.lower()
            if not title or not url or netloc.endswith("duckduckgo.com"):
                continue
            if url in seen_urls:
                continue

            snippet = ""
            row = link.find_parent("tr")
            next_row = row.find_next_sibling("tr") if row else None
            if next_row:
                snippet_elem = next_row.select_one(".result-snippet") or next_row.find("td")
                if snippet_elem:
                    snippet = snippet_elem.get_text(" ", strip=True)

            seen_urls.add(url)
            results.append({"title": title, "snippet": snippet, "url": url})

            if len(results) >= num_results:
                break

        return results


    def _is_anomaly_page(self, html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        for form in soup.find_all("form"):
            action = form.get("action") or ""
            if "anomaly.js" in action:
                return True
        text = soup.get_text(" ", strip=True).lower()
        return "anomaly" in text and "duckduckgo" in text

    def _instant_answer_results(
        self, query: str, num_results: int
    ) -> list[dict[str, str | None]]:
        response = requests.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_redirect": "1",
                "no_html": "1",
                "skip_disambig": "1",
            },
            headers=self._headers(),
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        results: list[dict[str, str | None]] = []
        heading = data.get("Heading")
        abstract = data.get("AbstractText") or data.get("Definition") or data.get("Answer")
        url = data.get("AbstractURL") or data.get("DefinitionURL")
        if heading and (abstract or url):
            results.append({"title": heading, "snippet": abstract or "", "url": url})

        for topic in data.get("RelatedTopics", []):
            if "Topics" in topic:
                candidates = topic.get("Topics") or []
            else:
                candidates = [topic]
            for item in candidates:
                title = item.get("Text")
                item_url = item.get("FirstURL")
                if title and item_url:
                    results.append({
                        "title": title,
                        "snippet": title,
                        "url": item_url,
                    })
                    if len(results) >= num_results:
                        return results

        return results[:num_results]

    def _format_results(self, results: list[dict[str, str | None]]) -> str:
        return "\n".join(
            f"Title: {result['title']}\n"
            f"Snippet: {result.get('snippet') or ''}\n"
            f"URL: {result.get('url') or ''}\n"
            for result in results
        )

    def execute(self, **kwargs) -> str:
        query = kwargs.get("query")
        num_results = kwargs.get("num_results", 8)

        if not query:
            return "Error performing search: query is required"

        encoded_query = quote_plus(str(query))
        endpoints = [
            (
                f"https://html.duckduckgo.com/html/?q={encoded_query}",
                self._parse_html_results,
            ),
            (
                f"https://lite.duckduckgo.com/lite/?q={encoded_query}",
                self._parse_lite_results,
            ),
        ]

        errors = []
        for url, parser in endpoints:
            try:
                response = requests.get(url, headers=self._headers(), timeout=15)
                response.raise_for_status()
                if self._is_anomaly_page(response.text):
                    continue
                results = parser(response.text, int(num_results))
                if results:
                    return self._format_results(results)
            except requests.RequestException as e:
                errors.append(str(e))

        try:
            results = self._instant_answer_results(str(query), int(num_results))
            if results:
                return self._format_results(results)
        except requests.RequestException as e:
            errors.append(str(e))

        if errors:
            return f"Error performing search: {'; '.join(errors)}"
        return "No results found. DuckDuckGo may have blocked HTML search for this request."
