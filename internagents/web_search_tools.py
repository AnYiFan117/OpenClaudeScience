"""Web search tools for 天玄·千枢科学发现平台."""

from __future__ import annotations

import html
import ipaddress
import json
import os
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain.tools import tool
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage


DEFAULT_PROVIDER = "duckduckgo"
DEFAULT_MAX_RESULTS = 5
DEFAULT_TIMEOUT_SECONDS = 10
JINA_MIN_TIMEOUT_SECONDS = 30
DEFAULT_MAX_FETCH_CHARS = 12000
MAX_RESULTS_LIMIT = 10
DEFAULT_MAX_WEB_SEARCH_CALLS_PER_RUN = 20
DUCKDUCKGO_HTML_URL = "https://duckduckgo.com/html/"
BING_SEARCH_URL = "https://www.bing.com/search"
ARXIV_SEARCH_URL = "https://export.arxiv.org/api/query"
SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
JINA_READER_URL = "https://r.jina.ai/"
ACADEMIC_PROVIDERS = ("semantic_scholar", "openalex", "arxiv", "pubmed")
SUPPORTED_WEB_SEARCH_PROVIDERS = (
    "duckduckgo",
    "bing",
    "academic",
    "arxiv",
    "semantic_scholar",
    "pubmed",
    "openalex",
)
PROVIDER_ALIASES = {
    "ddg": "duckduckgo",
    "semantic-scholar": "semantic_scholar",
    "semanticscholar": "semantic_scholar",
    "semantic_scholar": "semantic_scholar",
    "s2": "semantic_scholar",
    "ncbi": "pubmed",
    "open_alex": "openalex",
    "open-alex": "openalex",
    "papers": "academic",
    "scholar": "academic",
}
WEB_SEARCH_REFERENCE_INSTRUCTIONS = (
    "When you use information from web_search results, include the relevant source "
    "URL in the final answer body near the claim it supports. Prefer inline links "
    "or a short References section, but every referenced source must include the "
    "actual URL as a Markdown link like [title](url) or as a plain URL. Do not list "
    "source names without URLs, and do not summarize search-derived facts without "
    "exposing at least one source URL. For ordinary factual, news, or market questions, "
    "one relevant web_search result set is usually enough; if the returned sources are "
    "relevant, answer from them instead of issuing semantically similar follow-up "
    "searches. Do not use file-reading tools on web URLs."
)
WEB_SEARCH_RESULT_FOLLOWUP_INSTRUCTIONS = (
    "Search usage guidance: If these results contain relevant sources for the user's "
    "question, stop searching and answer with citations from the URLs above. Do not "
    "repeat web_search with small query variations just to confirm the same point. "
    "Do not call read_file or filesystem tools on http/https URLs."
)
WEB_SEARCH_BUDGET_RETRY_INSTRUCTIONS = (
    "The web_search budget for this user request has already been used. Do not call "
    "web_search again. Answer now using the existing web_search tool result(s), cite "
    "their URLs, and clearly say when the existing sources do not contain an exact "
    "number or detail."
)


@dataclass(frozen=True)
class WebSearchSettings:
    enabled: bool
    provider: str
    max_results: int
    timeout_seconds: int
    max_fetch_chars: int


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str


def _message_name(message: Any) -> str | None:
    name = getattr(message, "name", None)
    if name is None and isinstance(message, dict):
        name = message.get("name")
    return name if isinstance(name, str) else None


def _message_content(message: Any) -> Any:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content


def _is_web_search_result_message(message: Any) -> bool:
    if _message_name(message) == "web_search":
        return True
    content = _message_content(message)
    return isinstance(content, str) and (
        " web search result(s) for: " in content
        or content.startswith("web_search call budget reached")
    )


def _count_web_search_results(state: Any) -> int:
    messages = state.get("messages") if isinstance(state, dict) else None
    if not isinstance(messages, list):
        return 0
    return _count_web_search_result_messages(messages)


def _count_web_search_result_messages(messages: list[Any]) -> int:
    return sum(1 for message in messages if _is_web_search_result_message(message))


def _tool_call_name(tool_call: Any) -> str | None:
    name = getattr(tool_call, "name", None)
    if name is None and isinstance(tool_call, dict):
        name = tool_call.get("name")
    return name if isinstance(name, str) else None


def _tool_call_id(tool_call: Any) -> str | None:
    tool_call_id = getattr(tool_call, "id", None)
    if tool_call_id is None and isinstance(tool_call, dict):
        tool_call_id = tool_call.get("id")
    return tool_call_id if isinstance(tool_call_id, str) else None


def _tool_definition_name(tool_definition: Any) -> str | None:
    name = getattr(tool_definition, "name", None)
    if name is None and isinstance(tool_definition, dict):
        name = tool_definition.get("name")
        function = tool_definition.get("function")
        if name is None and isinstance(function, dict):
            name = function.get("name")
    return name if isinstance(name, str) else None


def _message_has_text(message: Any) -> bool:
    content = _message_content(message)
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str) and block.strip():
                return True
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return True
    return False


def _message_tool_calls(message: Any) -> list[Any]:
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls is None and isinstance(message, dict):
        tool_calls = message.get("tool_calls")
    return tool_calls if isinstance(tool_calls, list) else []


def _count_web_search_calls_before_current(state: Any, current_tool_call_id: str) -> int:
    completed_calls = _count_web_search_results(state)
    messages = state.get("messages") if isinstance(state, dict) else None
    if not isinstance(messages, list):
        return completed_calls

    for message in messages:
        calls_before_current = 0
        for tool_call in _message_tool_calls(message):
            if _tool_call_id(tool_call) == current_tool_call_id:
                return completed_calls + calls_before_current
            if _tool_call_name(tool_call) == "web_search":
                calls_before_current += 1

    return completed_calls


def _count_web_search_results_for_model_request(request: ModelRequest) -> int:
    messages = getattr(request, "messages", [])
    if not isinstance(messages, list):
        messages = []
    return max(
        _count_web_search_results(request.state),
        _count_web_search_result_messages(messages),
    )


def _limit_web_search_tool_calls(message: Any, remaining_calls: int) -> tuple[Any, int]:
    tool_calls = _message_tool_calls(message)
    if not tool_calls:
        return message, remaining_calls

    kept_tool_calls: list[Any] = []
    trimmed = False
    for tool_call in tool_calls:
        if _tool_call_name(tool_call) != "web_search":
            kept_tool_calls.append(tool_call)
            continue
        if remaining_calls > 0:
            kept_tool_calls.append(tool_call)
            remaining_calls -= 1
            continue
        trimmed = True

    if not trimmed:
        return message, remaining_calls

    if isinstance(message, AIMessage):
        return message.model_copy(update={"tool_calls": kept_tool_calls}), remaining_calls
    if isinstance(message, dict):
        updated_message = dict(message)
        updated_message["tool_calls"] = kept_tool_calls
        return updated_message, remaining_calls
    return message, remaining_calls


def _response_requests_only_web_search(response: Any) -> bool:
    messages = response.result if isinstance(response, ModelResponse) else [response]
    saw_web_search = False
    for message in messages:
        if _message_has_text(message):
            return False
        for tool_call in _message_tool_calls(message):
            if _tool_call_name(tool_call) != "web_search":
                return False
            saw_web_search = True
    return saw_web_search


def _max_web_search_calls_per_run() -> int:
    return _positive_int(
        _env_value("INTERNAGENTS_WEB_SEARCH_MAX_CALLS_PER_RUN"),
        DEFAULT_MAX_WEB_SEARCH_CALLS_PER_RUN,
    )


@dataclass
class WebSearchBudgetMiddleware(AgentMiddleware):
    """Prevents repeated web_search loops in one agent run."""

    max_calls: int | None = None

    @property
    def name(self) -> str:
        return "WebSearchBudgetMiddleware"

    def _limit(self) -> int:
        return self.max_calls or _max_web_search_calls_per_run()

    def _budget_message(self, request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=(
                "web_search call budget reached for this run. Use the first "
                "web_search result for this question to answer the user with "
                "citations. Do not issue another similar web_search for this "
                "question."
            ),
            tool_call_id=request.tool_call["id"],
            name="web_search",
        )

    def _trim_model_response(self, request: ModelRequest, response: Any) -> Any:
        remaining_calls = max(
            0, self._limit() - _count_web_search_results_for_model_request(request)
        )
        if remaining_calls <= 0:
            return response

        if isinstance(response, ModelResponse):
            result: list[Any] = []
            for message in response.result:
                updated_message, remaining_calls = _limit_web_search_tool_calls(
                    message, remaining_calls
                )
                result.append(updated_message)
            return ModelResponse(
                result=result,
                structured_response=response.structured_response,
            )

        updated_response, _ = _limit_web_search_tool_calls(response, remaining_calls)
        return updated_response

    def _retry_request_without_web_search(self, request: ModelRequest) -> ModelRequest:
        tools = [
            tool_definition
            for tool_definition in request.tools
            if _tool_definition_name(tool_definition) != "web_search"
        ]
        if request.system_message is None:
            system_message = SystemMessage(content=WEB_SEARCH_BUDGET_RETRY_INSTRUCTIONS)
        else:
            system_message = SystemMessage(
                content=f"{request.system_message.text}\n\n{WEB_SEARCH_BUDGET_RETRY_INSTRUCTIONS}"
            )
        return request.override(tools=tools, system_message=system_message)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        response = self._trim_model_response(request, handler(request))
        if (
            _count_web_search_results_for_model_request(request) >= self._limit()
            and _response_requests_only_web_search(response)
        ):
            return handler(self._retry_request_without_web_search(request))
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        response = self._trim_model_response(request, await handler(request))
        if (
            _count_web_search_results_for_model_request(request) >= self._limit()
            and _response_requests_only_web_search(response)
        ):
            return await handler(self._retry_request_without_web_search(request))
        return response

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        tool_name = getattr(request.tool, "name", None)
        prior_calls = _count_web_search_calls_before_current(
            request.state, request.tool_call["id"]
        )
        if tool_name == "web_search" and prior_calls >= self._limit():
            return self._budget_message(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        tool_name = getattr(request.tool, "name", None)
        prior_calls = _count_web_search_calls_before_current(
            request.state, request.tool_call["id"]
        )
        if tool_name == "web_search" and prior_calls >= self._limit():
            return self._budget_message(request)
        return await handler(request)


class DuckDuckGoHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[WebSearchResult] = []
        self._current_title: list[str] = []
        self._current_snippet: list[str] = []
        self._current_url = ""
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        classes = set(attr_map.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._flush_result()
            self._current_url = _normalize_duckduckgo_url(attr_map.get("href", ""))
            self._current_title = []
            self._current_snippet = []
            self._capture_title = True
            return
        if "result__snippet" in classes:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            self._capture_title = False
        if tag in {"a", "div"} and self._capture_snippet:
            self._capture_snippet = False

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._current_title.append(data)
        elif self._capture_snippet:
            self._current_snippet.append(data)

    def close(self) -> None:
        super().close()
        self._flush_result()

    def _flush_result(self) -> None:
        title = _clean_text(" ".join(self._current_title))
        if not title or not self._current_url:
            return
        self.results.append(
            WebSearchResult(
                title=title,
                url=self._current_url,
                snippet=_clean_text(" ".join(self._current_snippet)),
            )
        )
        self._current_title = []
        self._current_snippet = []
        self._current_url = ""


class BingHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[WebSearchResult] = []
        self._current_title: list[str] = []
        self._current_snippet: list[str] = []
        self._current_url = ""
        self._in_result = False
        self._in_h2 = False
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        classes = set(attr_map.get("class", "").split())
        if tag == "li" and "b_algo" in classes:
            self._flush_result()
            self._in_result = True
            self._current_title = []
            self._current_snippet = []
            self._current_url = ""
            return
        if not self._in_result:
            return
        if tag == "h2":
            self._in_h2 = True
            return
        if tag == "a" and self._in_h2 and not self._current_url:
            href = attr_map.get("href", "")
            if href.startswith(("http://", "https://")):
                self._current_url = html.unescape(href)
                self._capture_title = True
            return
        if tag == "p" and not self._current_snippet:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if not self._in_result:
            return
        if tag == "a" and self._capture_title:
            self._capture_title = False
        if tag == "h2":
            self._in_h2 = False
        if tag == "p" and self._capture_snippet:
            self._capture_snippet = False
        if tag == "li":
            self._flush_result()

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._current_title.append(data)
        elif self._capture_snippet:
            self._current_snippet.append(data)

    def close(self) -> None:
        super().close()
        self._flush_result()

    def _flush_result(self) -> None:
        title = _clean_text(" ".join(self._current_title))
        if title and self._current_url:
            self.results.append(
                WebSearchResult(
                    title=title,
                    url=self._current_url,
                    snippet=_clean_text(" ".join(self._current_snippet)),
                )
            )
        self._current_title = []
        self._current_snippet = []
        self._current_url = ""
        self._in_result = False
        self._in_h2 = False
        self._capture_title = False
        self._capture_snippet = False


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value and value.strip():
        return value.strip()
    return None


def _bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip():
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return default


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _normalize_provider(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "_")
    return PROVIDER_ALIASES.get(normalized, normalized)


def web_search_settings(config: dict[str, Any] | None = None) -> WebSearchSettings:
    config = config or {}
    raw_settings = config.get("web_search")
    settings = raw_settings if isinstance(raw_settings, dict) else {}
    enabled = _bool_value(
        _env_value("INTERNAGENTS_WEB_SEARCH_ENABLED")
        or settings.get("enabled"),
        True,
    )
    provider = _normalize_provider(
        _env_value("INTERNAGENTS_WEB_SEARCH_PROVIDER")
        or str(settings.get("provider") or DEFAULT_PROVIDER)
    )
    max_results = min(
        MAX_RESULTS_LIMIT,
        _positive_int(
            _env_value("INTERNAGENTS_WEB_SEARCH_MAX_RESULTS")
            or settings.get("max_results"),
            DEFAULT_MAX_RESULTS,
        ),
    )
    timeout_seconds = _positive_int(
        _env_value("INTERNAGENTS_WEB_SEARCH_TIMEOUT_SECONDS")
        or settings.get("timeout_seconds"),
        DEFAULT_TIMEOUT_SECONDS,
    )
    max_fetch_chars = _positive_int(
        _env_value("INTERNAGENTS_WEB_FETCH_MAX_CHARS")
        or settings.get("max_fetch_chars"),
        DEFAULT_MAX_FETCH_CHARS,
    )
    return WebSearchSettings(
        enabled=enabled,
        provider=provider or DEFAULT_PROVIDER,
        max_results=max_results,
        timeout_seconds=timeout_seconds,
        max_fetch_chars=max_fetch_chars,
    )


def _clean_text(value: str) -> str:
    return " ".join(html.unescape(value).split())


def _normalize_duckduckgo_url(value: str) -> str:
    if not value:
        return ""
    parsed = urllib.parse.urlparse(html.unescape(value))
    query = urllib.parse.parse_qs(parsed.query)
    redirect = query.get("uddg", [""])[0]
    if redirect:
        return urllib.parse.unquote(redirect)
    if parsed.scheme in {"http", "https"}:
        return urllib.parse.urlunparse(parsed)
    return value


def _fetch_url(
    url: str,
    timeout_seconds: int,
    headers: dict[str, str] | None = None,
) -> str:
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        url,
        headers=request_headers,
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def duckduckgo_search(
    query: str,
    *,
    max_results: int,
    timeout_seconds: int,
) -> list[WebSearchResult]:
    params = urllib.parse.urlencode({"q": query})
    parser = DuckDuckGoHtmlParser()
    parser.feed(_fetch_url(f"{DUCKDUCKGO_HTML_URL}?{params}", timeout_seconds))
    parser.close()
    return parser.results[:max_results]


def bing_search(
    query: str,
    *,
    max_results: int,
    timeout_seconds: int,
) -> list[WebSearchResult]:
    params = urllib.parse.urlencode({"q": query})
    parser = BingHtmlParser()
    parser.feed(_fetch_url(f"{BING_SEARCH_URL}?{params}", timeout_seconds))
    parser.close()
    return parser.results[:max_results]


def _snippet(*parts: str, max_chars: int = 500) -> str:
    text = _clean_text(" ".join(part for part in parts if part))
    return text[:max_chars].rstrip()


def _source_title(source: str, title: str) -> str:
    clean_title = _clean_text(title)
    return f"[{source}] {clean_title}" if clean_title else f"[{source}] Untitled"


def _author_names(authors: Any, *, limit: int = 5) -> str:
    names: list[str] = []
    if isinstance(authors, list):
        for author in authors:
            name = ""
            if isinstance(author, dict):
                raw_name = author.get("name")
                if raw_name is None and isinstance(author.get("author"), dict):
                    raw_name = author["author"].get("display_name")
                name = str(raw_name or "")
            else:
                name = str(author or "")
            if name.strip():
                names.append(_clean_text(name))
            if len(names) >= limit:
                break
    return ", ".join(names)


def _dedupe_results(results: list[WebSearchResult]) -> list[WebSearchResult]:
    seen: set[str] = set()
    deduped: list[WebSearchResult] = []
    for result in results:
        key = result.url.strip().lower() or result.title.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def arxiv_search(
    query: str,
    *,
    max_results: int,
    timeout_seconds: int,
) -> list[WebSearchResult]:
    params = urllib.parse.urlencode(
        {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
        }
    )
    payload = _fetch_url(f"{ARXIV_SEARCH_URL}?{params}", timeout_seconds)
    root = ET.fromstring(payload.strip())
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    results: list[WebSearchResult] = []
    for entry in root.findall("atom:entry", ns):
        title = _clean_text(entry.findtext("atom:title", default="", namespaces=ns))
        summary = _clean_text(entry.findtext("atom:summary", default="", namespaces=ns))
        source_url = _clean_text(entry.findtext("atom:id", default="", namespaces=ns))
        for link in entry.findall("atom:link", ns):
            if link.attrib.get("rel") in {"alternate", None} and link.attrib.get("href"):
                source_url = _clean_text(link.attrib["href"])
                break
        authors = _author_names(
            [
                author.findtext("atom:name", default="", namespaces=ns)
                for author in entry.findall("atom:author", ns)
            ]
        )
        published = _clean_text(
            entry.findtext("atom:published", default="", namespaces=ns)
        )
        doi = _clean_text(entry.findtext("arxiv:doi", default="", namespaces=ns))
        details = []
        if authors:
            details.append(f"Authors: {authors}.")
        if published:
            details.append(f"Published: {published[:10]}.")
        if doi:
            details.append(f"DOI: {doi}.")
        details.append(summary)
        if title and source_url:
            results.append(
                WebSearchResult(
                    title=_source_title("arXiv", title),
                    url=source_url,
                    snippet=_snippet(*details),
                )
            )
    return results[:max_results]


def semantic_scholar_search(
    query: str,
    *,
    max_results: int,
    timeout_seconds: int,
) -> list[WebSearchResult]:
    params = urllib.parse.urlencode(
        {
            "query": query,
            "limit": max_results,
            "fields": "title,url,abstract,year,authors,venue,externalIds",
        }
    )
    payload = json.loads(_fetch_url(f"{SEMANTIC_SCHOLAR_SEARCH_URL}?{params}", timeout_seconds))
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    results: list[WebSearchResult] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = _clean_text(str(item.get("title") or ""))
        url = _clean_text(str(item.get("url") or ""))
        external_ids = item.get("externalIds")
        if not url and isinstance(external_ids, dict):
            doi = _clean_text(str(external_ids.get("DOI") or ""))
            arxiv_id = _clean_text(str(external_ids.get("ArXiv") or ""))
            pmid = _clean_text(str(external_ids.get("PubMed") or ""))
            if doi:
                url = f"https://doi.org/{doi}"
            elif arxiv_id:
                url = f"https://arxiv.org/abs/{arxiv_id}"
            elif pmid:
                url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        if not title or not url:
            continue
        authors = _author_names(item.get("authors"))
        venue = _clean_text(str(item.get("venue") or ""))
        year = str(item.get("year") or "")
        abstract = _clean_text(str(item.get("abstract") or ""))
        results.append(
            WebSearchResult(
                title=_source_title("Semantic Scholar", title),
                url=url,
                snippet=_snippet(
                    f"Authors: {authors}." if authors else "",
                    f"Venue: {venue}." if venue else "",
                    f"Year: {year}." if year else "",
                    abstract,
                ),
            )
        )
    return results[:max_results]


def pubmed_search(
    query: str,
    *,
    max_results: int,
    timeout_seconds: int,
) -> list[WebSearchResult]:
    search_params = urllib.parse.urlencode(
        {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": max_results,
            "sort": "relevance",
        }
    )
    search_payload = json.loads(_fetch_url(f"{PUBMED_ESEARCH_URL}?{search_params}", timeout_seconds))
    id_list = (
        search_payload.get("esearchresult", {}).get("idlist", [])
        if isinstance(search_payload, dict)
        else []
    )
    ids = [str(item) for item in id_list if str(item).strip()]
    if not ids:
        return []
    summary_params = urllib.parse.urlencode(
        {
            "db": "pubmed",
            "id": ",".join(ids[:max_results]),
            "retmode": "json",
        }
    )
    summary_payload = json.loads(_fetch_url(f"{PUBMED_ESUMMARY_URL}?{summary_params}", timeout_seconds))
    result = summary_payload.get("result") if isinstance(summary_payload, dict) else None
    if not isinstance(result, dict):
        return []
    uids = result.get("uids") if isinstance(result.get("uids"), list) else ids
    results: list[WebSearchResult] = []
    for uid in uids:
        item = result.get(str(uid))
        if not isinstance(item, dict):
            continue
        title = _clean_text(str(item.get("title") or ""))
        if not title:
            continue
        authors = _author_names(item.get("authors"))
        journal = _clean_text(
            str(item.get("fulljournalname") or item.get("source") or "")
        )
        pubdate = _clean_text(str(item.get("pubdate") or ""))
        doi = ""
        article_ids = item.get("articleids")
        if isinstance(article_ids, list):
            for article_id in article_ids:
                if (
                    isinstance(article_id, dict)
                    and str(article_id.get("idtype") or "").lower() == "doi"
                ):
                    doi = _clean_text(str(article_id.get("value") or ""))
                    break
        details = [
            f"Authors: {authors}." if authors else "",
            f"Journal: {journal}." if journal else "",
            f"Published: {pubdate}." if pubdate else "",
            f"DOI: {doi}." if doi else "",
        ]
        results.append(
            WebSearchResult(
                title=_source_title("PubMed", title),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                snippet=_snippet(*details),
            )
        )
    return results[:max_results]


def _openalex_abstract(inverted_index: Any) -> str:
    if not isinstance(inverted_index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in inverted_index.items():
        if not isinstance(indexes, list):
            continue
        for index in indexes:
            if isinstance(index, int):
                positions.append((index, str(word)))
    return " ".join(word for _, word in sorted(positions))


def openalex_search(
    query: str,
    *,
    max_results: int,
    timeout_seconds: int,
) -> list[WebSearchResult]:
    params = urllib.parse.urlencode({"search": query, "per-page": max_results})
    payload = json.loads(_fetch_url(f"{OPENALEX_WORKS_URL}?{params}", timeout_seconds))
    data = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    results: list[WebSearchResult] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = _clean_text(str(item.get("display_name") or ""))
        doi = _clean_text(str(item.get("doi") or ""))
        source_url = doi or _clean_text(str(item.get("id") or ""))
        if not title or not source_url:
            continue
        authors = _author_names(item.get("authorships"))
        year = str(item.get("publication_year") or "")
        source = ""
        primary_location = item.get("primary_location")
        if isinstance(primary_location, dict):
            source_obj = primary_location.get("source")
            if isinstance(source_obj, dict):
                source = _clean_text(str(source_obj.get("display_name") or ""))
        abstract = _openalex_abstract(item.get("abstract_inverted_index"))
        results.append(
            WebSearchResult(
                title=_source_title("OpenAlex", title),
                url=source_url,
                snippet=_snippet(
                    f"Authors: {authors}." if authors else "",
                    f"Source: {source}." if source else "",
                    f"Year: {year}." if year else "",
                    abstract,
                ),
            )
        )
    return results[:max_results]


def academic_search(
    query: str,
    *,
    max_results: int,
    timeout_seconds: int,
) -> list[WebSearchResult]:
    searchers = {
        "semantic_scholar": semantic_scholar_search,
        "openalex": openalex_search,
        "arxiv": arxiv_search,
        "pubmed": pubmed_search,
    }
    per_source = max(1, (max_results + len(ACADEMIC_PROVIDERS) - 1) // len(ACADEMIC_PROVIDERS))
    results: list[WebSearchResult] = []
    errors: list[str] = []
    for provider in ACADEMIC_PROVIDERS:
        try:
            results.extend(
                searchers[provider](
                    query,
                    max_results=per_source,
                    timeout_seconds=timeout_seconds,
                )
            )
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
    deduped = _dedupe_results(results)
    if not deduped and errors:
        raise RuntimeError("; ".join(errors))
    return deduped[:max_results]


def _is_blocked_host(hostname: str) -> bool:
    normalized = hostname.strip("[]").lower().rstrip(".")
    if not normalized:
        return True
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    if normalized.endswith((".localhost", ".local", ".internal")):
        return True

    addresses: set[str] = set()
    try:
        addresses.add(str(ipaddress.ip_address(normalized)))
    except ValueError:
        try:
            for family, _, _, _, sockaddr in socket.getaddrinfo(normalized, None):
                if family in {socket.AF_INET, socket.AF_INET6}:
                    addresses.add(str(sockaddr[0]))
        except OSError:
            addresses = set()

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def validate_fetch_web_url(url: str) -> tuple[bool, str]:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False, "fetch_web_url only supports http and https URLs."
    if not parsed.hostname:
        return False, "fetch_web_url requires a URL with a hostname."
    if parsed.username or parsed.password:
        return False, "fetch_web_url does not support URLs containing credentials."
    if _is_blocked_host(parsed.hostname):
        return False, "fetch_web_url cannot access localhost, private, or internal hosts."
    return True, urllib.parse.urlunparse(parsed)


def jina_fetch_url(
    url: str,
    *,
    timeout_seconds: int,
    max_chars: int,
) -> str:
    payload = _fetch_url(
        f"{JINA_READER_URL}{urllib.parse.quote(url, safe=':/?&=%[]@!$&()*+,;-._~')}",
        max(timeout_seconds, JINA_MIN_TIMEOUT_SECONDS),
        headers={
            "Accept": "application/json",
            "User-Agent": "天玄·千枢科学发现平台-WebFetch",
        },
    )

    title = ""
    source_url = url
    content = payload
    try:
        parsed_payload = json.loads(payload)
    except json.JSONDecodeError:
        parsed_payload = None
    if isinstance(parsed_payload, dict):
        data = parsed_payload.get("data")
        if isinstance(data, dict):
            title = _clean_text(str(data.get("title") or ""))
            source_url = _clean_text(str(data.get("url") or source_url))
            content = str(data.get("content") or data.get("text") or "")
        else:
            title = _clean_text(str(parsed_payload.get("title") or ""))
            source_url = _clean_text(str(parsed_payload.get("url") or source_url))
            content = str(parsed_payload.get("content") or parsed_payload.get("text") or "")

    clean_content = content.strip()
    truncated = len(clean_content) > max_chars
    if truncated:
        clean_content = clean_content[:max_chars].rstrip()

    lines = [
        f"Fetched web page: {title or source_url}",
        f"URL: {source_url}",
        (
            "Use this page content as a source and cite the URL in the final answer "
            "when relying on it."
        ),
        "",
        clean_content or "No readable page content was returned.",
    ]
    if truncated:
        lines.append(f"\n[Content truncated to {max_chars} characters.]")
    return "\n".join(lines)


def format_search_results(query: str, results: list[WebSearchResult]) -> str:
    if not results:
        return f"No web search results found for: {query}"
    lines = [
        f"Found {len(results)} web search result(s) for: {query}",
        WEB_SEARCH_REFERENCE_INSTRUCTIONS,
        WEB_SEARCH_RESULT_FOLLOWUP_INSTRUCTIONS,
    ]
    for index, result in enumerate(results, start=1):
        lines.extend(
            [
                "",
                f"Source [{index}]: {result.title}",
                f"URL [{index}]: {result.url}",
                f"Citation [{index}]: [{result.title}]({result.url})",
            ]
        )
        if result.snippet:
            lines.append(f"Snippet [{index}]: {result.snippet}")
    return "\n".join(lines)


def web_search_reference_prompt(config: dict[str, Any] | None = None) -> str:
    settings = web_search_settings(config)
    return WEB_SEARCH_REFERENCE_INSTRUCTIONS if settings.enabled else ""


def web_search_tools(config: dict[str, Any] | None = None) -> list[Any]:
    settings = web_search_settings(config)
    if not settings.enabled:
        return []

    @tool("web_search")
    def web_search(query: str, max_results: int = settings.max_results) -> str:
        """Search the web for current information and return titles, URLs, and snippets.

        When using search-derived facts in the final answer, include the relevant
        source URL inline near the supported claim or in a short References section.

        Args:
            query: Search query to execute.
            max_results: Maximum number of results to return. The configured default is used when omitted.
        """

        normalized_query = query.strip()
        if not normalized_query:
            return "Web search query must not be empty."
        requested_results = min(
            MAX_RESULTS_LIMIT,
            settings.max_results,
            max(1, _positive_int(max_results, settings.max_results)),
        )
        if settings.provider in {"duckduckgo", "ddg"}:
            errors: list[str] = []
            try:
                results = duckduckgo_search(
                    normalized_query,
                    max_results=requested_results,
                    timeout_seconds=settings.timeout_seconds,
                )
            except Exception as exc:
                errors.append(f"DuckDuckGo: {exc}")
                results = []
            if not results:
                try:
                    results = bing_search(
                        normalized_query,
                        max_results=requested_results,
                        timeout_seconds=settings.timeout_seconds,
                    )
                except Exception as exc:
                    errors.append(f"Bing: {exc}")
                    results = []
            if not results:
                try:
                    results = academic_search(
                        normalized_query,
                        max_results=requested_results,
                        timeout_seconds=settings.timeout_seconds,
                    )
                except Exception as exc:
                    errors.append(f"academic sources: {exc}")
                    results = []
            if not results and errors:
                return (
                    "Web search failed with DuckDuckGo, Bing, and academic sources. "
                    + "; ".join(errors)
                )
            return format_search_results(normalized_query, results)

        if settings.provider == "bing":
            try:
                results = bing_search(
                    normalized_query,
                    max_results=requested_results,
                    timeout_seconds=settings.timeout_seconds,
                )
            except Exception as exc:
                return f"Web search failed with Bing: {exc}"
            return format_search_results(normalized_query, results)

        if settings.provider == "academic":
            try:
                results = academic_search(
                    normalized_query,
                    max_results=requested_results,
                    timeout_seconds=settings.timeout_seconds,
                )
            except Exception as exc:
                return f"Web search failed with academic sources: {exc}"
            return format_search_results(normalized_query, results)

        academic_searchers = {
            "arxiv": arxiv_search,
            "semantic_scholar": semantic_scholar_search,
            "pubmed": pubmed_search,
            "openalex": openalex_search,
        }
        if settings.provider in academic_searchers:
            try:
                results = academic_searchers[settings.provider](
                    normalized_query,
                    max_results=requested_results,
                    timeout_seconds=settings.timeout_seconds,
                )
            except Exception as exc:
                return f"Web search failed with {settings.provider}: {exc}"
            return format_search_results(normalized_query, results)

        return json.dumps(
            {
                "error": "unsupported_web_search_provider",
                "provider": settings.provider,
                "supported_providers": list(SUPPORTED_WEB_SEARCH_PROVIDERS),
            },
            ensure_ascii=False,
        )

    @tool("fetch_web_url")
    def fetch_web_url(url: str, max_chars: int = settings.max_fetch_chars) -> str:
        """Fetch a public web URL and return readable page text via Jina Reader.

        Use this after web_search when a specific result URL needs more detail.
        This tool does not search; it only parses the exact URL provided. Do not
        use file-reading tools for http or https URLs.

        Args:
            url: Public http or https URL to fetch.
            max_chars: Maximum number of characters of readable content to return.
        """

        is_valid, normalized_url = validate_fetch_web_url(url)
        if not is_valid:
            return normalized_url
        requested_chars = max(1, _positive_int(max_chars, settings.max_fetch_chars))
        try:
            return jina_fetch_url(
                normalized_url,
                timeout_seconds=settings.timeout_seconds,
                max_chars=requested_chars,
            )
        except Exception as exc:
            return f"fetch_web_url failed with Jina Reader: {exc}"

    return [web_search, fetch_web_url]
