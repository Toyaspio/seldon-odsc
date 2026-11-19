import pytest
import time
from backend.agent.limits import ResearchLimits, ResourceLimitReached
from backend.agent.pipeline import ToolRouter
from backend.providers.interfaces import MockFinancialDataProvider, SearchProvider


def test_search_round_and_query_limits():
    limits = ResearchLimits(max_search_rounds=2, max_search_queries=12)
    limits.claim_search(6)
    limits.claim_search(6)
    assert limits.snapshot()['search_queries'] == 12
    with pytest.raises(ResourceLimitReached):
        limits.claim_search(1)
    with pytest.raises(ResourceLimitReached):
        ResearchLimits(max_search_queries=1).claim_search(2)


def test_model_limit_and_independent_counters():
    first, second = ResearchLimits(), ResearchLimits()
    for _ in range(6):
        first.claim_model()
    with pytest.raises(ResourceLimitReached):
        first.claim_model()
    second.claim_model()
    second.record_tokens(17, 9)
    assert first.snapshot()['model_calls'] == 6
    assert second.snapshot()['model_calls'] == 1
    assert second.snapshot()['prompt_tokens'] == 17


def test_timeout_limit():
    limits = ResearchLimits(request_timeout_seconds=0)
    with pytest.raises(ResourceLimitReached):
        limits.claim_search(1)


def test_240_second_default_deadline_blocks_new_work():
    limits = ResearchLimits()
    assert limits.request_timeout_seconds == 240
    limits.started = time.monotonic() - 241
    with pytest.raises(ResourceLimitReached, match='deadline'):
        limits.claim_model()
    assert limits.model_calls == 0


def test_provider_timeout_is_not_silently_swallowed():
    class TimeoutSearch(SearchProvider):
        def search(self, query: str, timeout_seconds: int) -> list[dict]:
            raise TimeoutError('provider timeout')
    router = ToolRouter(TimeoutSearch(), MockFinancialDataProvider())
    with pytest.raises(TimeoutError):
        router.search_once('query', ResearchLimits())
