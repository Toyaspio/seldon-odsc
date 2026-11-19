from contextlib import contextmanager
from contextvars import ContextVar
import os
import re
import time
from dataclasses import dataclass, field


from backend.agent.models import ProviderActivity, ProviderEvent


_active_provider = ContextVar('active_research_provider', default=None)


def record_provider_detail(name: str, **fields) -> None:
    active = _active_provider.get()
    if active is not None and active[0] == name:
        for key, value in fields.items():
            setattr(active[2], key, value)


def count_provider_filing(name: str, field_name: str = 'filings_retrieved') -> None:
    active = _active_provider.get()
    if active is not None and active[0] == name:
        event = active[2]
        setattr(event, field_name, (getattr(event, field_name) or 0) + 1)


def record_provider_http(name: str, *, response: bool = False) -> None:
    active = _active_provider.get()
    if active is not None and active[0] == name:
        activity = active[1]
        field_name = 'http_responses' if response else 'http_attempts'
        setattr(activity, field_name, (getattr(activity, field_name) or 0) + 1)
        event = active[2]
        setattr(event, field_name, getattr(event, field_name) + 1)


def _safe_search_label(text: str | None) -> str | None:
    if not text:
        return None
    for key in ('NEBIUS_API_KEY', 'TAVILY_API_KEY'):
        secret = os.getenv(key)
        if secret:
            text = text.replace(secret, '[redacted]')
    return re.sub(r'(?i)(bearer\s+[^\s]+|(?:tvly-|sk-)[\w-]+)', '[redacted]', text)[:300]


class ResourceLimitReached(RuntimeError):
    pass


@dataclass
class ResearchLimits:
    max_search_rounds: int = field(default_factory=lambda: int(os.getenv('MAX_SEARCH_ROUNDS', '2')))
    max_search_queries: int = field(default_factory=lambda: int(os.getenv('MAX_SEARCH_QUERIES', '12')))
    request_timeout_seconds: int = field(default_factory=lambda: int(os.getenv('REQUEST_TIMEOUT_SECONDS', '240')))
    max_model_calls: int = field(default_factory=lambda: int(os.getenv('MAX_MODEL_CALLS', '6')))
    rounds_used: int = 0
    queries_used: int = 0
    model_calls: int = 0
    review_calls: int = 0
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    providers: dict[str, ProviderActivity] = field(default_factory=lambda: {
        name: ProviderActivity(http_attempts=0, http_responses=0) for name in ('nebius', 'tavily', 'sec', 'esef')
    })
    events: list[ProviderEvent] = field(default_factory=list)
    search_results: list[tuple[ProviderEvent, set[str]]] = field(default_factory=list)
    news_results: list[dict] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    @contextmanager
    def provider_call(self, name: str, *, mock: bool = False, stage: str = 'research',
                      query: str | None = None, company_id: str | None = None, topic: str | None = None):
        activity = self.providers[name]
        if mock:
            activity.mock_used = True
        if not activity.enabled:
            yield
            return
        activity.calls += 1
        event = ProviderEvent(provider=name, stage=stage, query=_safe_search_label(query),
                              topic=_safe_search_label(topic), company_id=company_id)
        self.events.append(event)
        started = time.monotonic()
        token = _active_provider.set((name, activity, event))
        try:
            yield event
        except Exception as exc:
            event.outcome = 'failed'
            event.error_code = event.error_code or type(exc).__name__
            activity.failed += 1
            raise
        else:
            activity.succeeded += 1
        finally:
            event.elapsed_ms = round((time.monotonic() - started) * 1000)
            _active_provider.reset(token)

    def attribute_search_citations(self, adopted_urls: dict[str, str]) -> None:
        remaining = dict(adopted_urls)
        # The evidence extractor retains the last result for each URL.
        for event, returned_urls in reversed(self.search_results):
            event.adopted_source_ids = [remaining.pop(url) for url in sorted(returned_urls) if url in remaining]

    def provider_snapshot(self) -> dict:
        return {name: item.model_dump() for name, item in self.providers.items()}

    def remaining(self) -> float:
        seconds = self.request_timeout_seconds - (time.monotonic() - self.started)
        if seconds <= 0:
            raise ResourceLimitReached('Research deadline reached')
        return seconds

    def claim_search(self, query_count: int) -> None:
        self.remaining()
        if not 0 <= self.max_search_rounds <= 2 or self.rounds_used >= self.max_search_rounds:
            raise ResourceLimitReached('Search round limit reached')
        if query_count < 1 or self.queries_used + query_count > min(self.max_search_queries, 12):
            raise ResourceLimitReached('Search query limit reached')
        self.rounds_used += 1
        self.queries_used += query_count

    def claim_model(self) -> None:
        self.remaining()
        if self.model_calls >= min(self.max_model_calls, 6):
            raise ResourceLimitReached('Model call limit reached')
        self.model_calls += 1

    def record_tokens(self, prompt: int, completion: int) -> None:
        if type(prompt) is int and type(completion) is int and prompt >= 0 and completion >= 0:
            self.prompt_tokens += prompt
            self.completion_tokens += completion

    def snapshot(self) -> dict:
        cost = None
        if self.model == 'zai-org/GLM-5.3-Flash' and self.prompt_tokens + self.completion_tokens > 0:
            # Published uncached input and output rates. The provider invoice can differ.
            cost = round((self.prompt_tokens * 0.15 + self.completion_tokens * 0.50) / 1_000_000, 8)
        return {'estimated_cost_usd': cost, 'model': self.model, 'model_calls': self.model_calls + self.review_calls, 'prompt_tokens': self.prompt_tokens,
                'completion_tokens': self.completion_tokens, 'search_queries': self.queries_used,
                'search_rounds': self.rounds_used,
                'elapsed_seconds': round(time.monotonic() - self.started, 3)}
