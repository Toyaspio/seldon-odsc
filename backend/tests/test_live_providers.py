import json
from io import BytesIO
from urllib.error import HTTPError, URLError
import pytest
from backend.providers.live import NebiusModelProvider, TavilySearchProvider, ProviderError
from backend.agent.limits import ResearchLimits


class Response(BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


def test_nebius_plan_parses_bounded_json_without_exposing_key(monkeypatch):
    monkeypatch.setenv('MAX_RESEARCH_COST_USD', '0')
    payload = {'choices': [{'message': {'content': json.dumps({
        'retrieval_tasks': [{'query': 'official filing', 'purpose': 'Find cash CapEx'}],
        'companies': ['Microsoft'], 'core_question': 'Cash CapEx',
        'metrics': ['cash_capex'], 'preferred_source_types': ['filing'],
        'verification_questions': ['Is the figure reported?'], 'comparison': 'latest'})}}]}
    seen = {}
    def opener(request, timeout):
        seen['url'] = request.full_url
        seen['auth'] = request.get_header('Authorization')
        return Response(payload)
    provider = NebiusModelProvider(api_key='fixture-secret', model='fixture-model', opener=opener)
    plan = provider.plan('Research Microsoft')
    assert plan['retrieval_tasks'][0]['query'] == 'official filing'
    assert seen['url'].endswith('/chat/completions')
    assert seen['auth'] == 'Bearer fixture-secret'


def test_nebius_accepts_search_query_alias_within_existing_limits():
    queries = [f'Query {index}' for index in range(12)]
    content = json.dumps({'search_queries': queries})
    provider = NebiusModelProvider(api_key='fixture', model='fixture',
        opener=lambda request, timeout: Response({'choices': [{'message': {'content': content}}]}))
    result = provider.plan('research request')
    assert [task['query'] for task in result['retrieval_tasks']] == queries[:6]


def test_tavily_normalizes_and_filters_results():
    payload = {'results': [
        {'title': 'Filing', 'url': 'https://sec.gov/filing', 'content': 'snippet', 'published_date': '2025-01-01'},
        {'title': 'Unsafe', 'url': 'http://example.com', 'content': 'ignore'},
    ]}
    seen = {}
    def opener(request, timeout):
        seen['authorization'] = request.get_header('Authorization')
        seen['body'] = json.loads(request.data)
        return Response(payload)
    provider = TavilySearchProvider(api_key='fixture', opener=opener)
    results = provider.search('Microsoft filing', 5)
    assert len(results) == 1
    assert results[0]['url'].startswith('https://')
    assert results[0]['content'] == 'snippet'
    assert seen['authorization'] == 'Bearer fixture'
    assert 'api_key' not in seen['body']


def test_model_output_truncation_rejected():
    provider = NebiusModelProvider(api_key='fixture', model='fixture',
        opener=lambda request, timeout: Response({'choices': [
            {'finish_reason': 'length', 'message': {'content': '{}'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 4096}}))
    limits = ResearchLimits()
    with pytest.raises(ProviderError, match='truncated'):
        provider.plan('Research Oracle', limits=limits)
    assert limits.snapshot()['completion_tokens'] == 4096


def test_temporary_provider_failure_retries_without_extra_query_claim():
    calls = []
    def opener(request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 503, 'temporary', {}, None)
        return Response({'results': []})
    provider = TavilySearchProvider(api_key='fixture', opener=opener)
    assert provider.search('Oracle filing', 5) == []
    assert len(calls) == 2


@pytest.mark.parametrize('retry_after', [4, 120])
def test_nebius_529_respects_retry_after_and_the_request_deadline(monkeypatch, retry_after):
    from backend.tests.test_phase2 import PLAN

    attempts = []
    waits = []
    monkeypatch.setattr('backend.providers.live.time.sleep', waits.append)
    limits = ResearchLimits()
    limits.providers['nebius'].enabled = True

    def opener(request, timeout):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 529, 'fixture upstream error', {'Retry-After': str(retry_after)}, None)
        answer = Response({'choices': [{'message': {'content': json.dumps(PLAN)}}]})
        answer.status = 200
        return answer

    provider = NebiusModelProvider(api_key='fixture', opener=opener)
    with limits.provider_call('nebius', stage='planning') as event:
        if retry_after == 4:
            assert provider.plan('Research ACME', limits=limits) == PLAN
        else:
            with pytest.raises(ProviderError, match='request failed') as caught:
                provider.plan('Research ACME', limits=limits)
            assert 'HTTP 529' in caught.value.public_message
    assert waits == ([4] if retry_after == 4 else [])
    assert limits.model_calls == 1
    assert len(attempts) == event.http_attempts == event.http_responses == (2 if retry_after == 4 else 1)
    assert event.http_status == (200 if retry_after == 4 else 529)


@pytest.mark.parametrize(('fault', 'expected'), [
    (HTTPError('https://example.invalid', 401, 'secret-fixture-body', {}, None), 'rejected the API credentials (HTTP 401)'),
    (HTTPError('https://example.invalid', 403, 'secret-fixture-body', {}, None), 'denied access to the requested service (HTTP 403)'),
    (HTTPError('https://example.invalid', 429, 'secret-fixture-body', {}, None), 'rate-limited the request (HTTP 429)'),
    (HTTPError('https://example.invalid', 503, 'secret-fixture-body', {}, None), 'returned an upstream error (HTTP 503)'),
    (HTTPError('https://example.invalid', 529, 'secret-fixture-body', {}, None), 'is temporarily unavailable (HTTP 529)'),
    (URLError(TimeoutError('secret-fixture-body')), 'did not respond within the request time limit'),
])
def test_task_reports_safe_nebius_failure_detail(monkeypatch, fault, expected):
    from fastapi.testclient import TestClient

    from backend import app as api
    from backend.tests.test_edge_cases import _terminal
    from backend.tests.test_p1_trust import live_agent

    def opener(request, timeout):
        raise fault

    import backend.providers.live as live_module
    from types import SimpleNamespace
    monkeypatch.setattr(live_module, 'time', SimpleNamespace(monotonic=live_module.time.monotonic, sleep=lambda _: None))
    agent = live_agent(monkeypatch)
    agent.planner.model = NebiusModelProvider(api_key='secret-fixture-key', opener=opener)
    monkeypatch.setattr(api, '_live_enabled', True)
    monkeypatch.setattr(api, '_orchestrator', agent)
    with TestClient(api.app) as client:
        created = client.post('/api/research', json={'query': 'Research the unnamed issuer'})
        assert created.status_code == 202
        task = _terminal(client, created.json()['task_id'])
        assert task['status'] == 'partial' and task['report_id']
        report = client.get('/api/reports/' + task['report_id']).json()
    assert any(expected in item for item in report['limitations'])
    assert 'secret-fixture' not in json.dumps(report)
    assert task['providers']['nebius']['failed'] == 1
    assert 'secret-fixture' not in json.dumps(task)


@pytest.mark.parametrize('payload', [
    {'choices': [None]},
    {'choices': [42], 'usage': 'invalid telemetry'},
])
def test_invalid_nebius_completion_becomes_provider_error(payload):
    provider = NebiusModelProvider(api_key='fixture', opener=lambda request, timeout: Response(payload))
    with pytest.raises(ProviderError, match='invalid completion') as caught:
        provider.plan('Research Microsoft')
    assert caught.value.public_message == 'Nebius returned an invalid model response.'

@pytest.mark.parametrize(('task_seconds', 'ceiling'), [(240, 60), (20, 20)])
def test_nebius_request_timeout_respects_task_deadline(task_seconds, ceiling):
    from backend.tests.test_phase2 import PLAN

    def opener(request, timeout):
        assert ceiling - 5 < timeout <= ceiling
        return Response({'choices': [{'message': {'content': json.dumps(PLAN)}}]})

    provider = NebiusModelProvider(api_key='fixture', opener=opener)
    assert provider.plan('Research ACME', limits=ResearchLimits(request_timeout_seconds=task_seconds)) == PLAN

@pytest.mark.parametrize(('phase', 'attempts', 'notice'), [
    ('connect', 3, 'timed out connecting or negotiating HTTPS'),
    ('request_write', 1, 'timed out sending the request'),
    ('response_headers', 1, 'timed out waiting for the response to start'),
    ('response_body', 1, 'timed out reading the response body'),
])
def test_nebius_retries_only_timeouts_before_dispatch(monkeypatch, phase, attempts, notice):
    from types import SimpleNamespace
    import backend.providers.live as live_module
    from backend.providers.transport import ProviderTimeout

    calls = []
    monkeypatch.setattr(live_module, 'time', SimpleNamespace(
        monotonic=live_module.time.monotonic, sleep=lambda _: None,
    ))

    def opener(request, timeout):
        calls.append(request)
        assert request.full_url == 'https://api.tokenfactory.nebius.com/v1/chat/completions'
        raise URLError(ProviderTimeout(phase))

    provider = NebiusModelProvider(api_key='fixture', opener=opener)
    with pytest.raises(ProviderError) as caught:
        provider.plan('Research Microsoft')
    assert notice in caught.value.public_message
    assert len(calls) == attempts


def test_completion_fields_remain_bounded():
    from backend.tests.test_phase2 import PLAN

    def opener(request, timeout):
        payload = json.loads(request.data)
        assert len(request.data) < 6000
        assert payload['max_tokens'] == 4096
        assert payload['response_format'] == {'type': 'json_object'}
        assert payload['reasoning_effort'] == 'low'
        return Response({'choices': [{'message': {'content': json.dumps(PLAN)}}]})

    NebiusModelProvider(api_key='fixture', model='zai-org/GLM-5.3-Flash', opener=opener).plan(
        'Research Microsoft', limits=ResearchLimits())


def test_response_body_does_not_touch_socket_after_http_reader_closes():
    from time import monotonic
    from backend.providers.transport import read_bounded

    class FullyReadResponse(BytesIO):
        def __init__(self):
            super().__init__(b'{"ok":true}')
            self.finished = False

        def isclosed(self):
            return self.finished

        def set_read_timeout(self, remaining):
            assert not self.finished and 0 < remaining <= 1

        def read1(self, size):
            self.finished = True
            return super().read1(size)

    assert read_bounded(FullyReadResponse(), 1024, monotonic() + 1) == b'{"ok":true}'


def test_response_header_deadline_interrupts_a_blocked_read(monkeypatch):
    from http.client import HTTPSConnection
    import socket
    from time import monotonic
    from backend.providers.transport import DeadlineConnection, ProviderTimeout

    def wait_for_headers(self):
        self.sock.recv(1)
        raise OSError('fixture connection closed while awaiting headers')

    monkeypatch.setattr(HTTPSConnection, 'getresponse', wait_for_headers)
    left, right = socket.socketpair()
    with left, right:
        # The peer keeps the connection open without sending headers.
        connection = DeadlineConnection('example.invalid', deadline=monotonic() + .2)
        connection.sock = left
        with pytest.raises(ProviderTimeout) as caught:
            connection.getresponse()
        assert caught.value.phase == 'response_headers'


def test_planning_retains_valid_hints_when_optional_format_varies():
    payload = {'companies': 'Parabolic', 'retrieval_tasks': [
        None, 'Parabolic earnings', {'query': 'Parabolic SEC filings', 'purpose': 42}],
        'verification_questions': [False, 'What did revenue do?']}
    body = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(payload)}}]}
    model = NebiusModelProvider(api_key='fixture', opener=lambda *_args, **_kwargs: Response(body))
    result = model.plan('Is Parabolic a good investment?')
    assert result['companies'] == ['Parabolic']
    assert result['core_question'] == 'Is Parabolic a good investment?'
    assert [task['query'] for task in result['retrieval_tasks']] == ['Parabolic earnings', 'Parabolic SEC filings']


def test_unreadable_model_output_is_retained_privately_and_redacted(monkeypatch):
    from backend.agent.model_output import model_diagnostic_sink

    monkeypatch.setattr('backend.settings.runtime_values', lambda *_: ('fixture-secret', None))
    seen = {}
    token = model_diagnostic_sink.set(lambda stage, data: seen.update({stage: data}))
    try:
        body = {'choices': [{'message': {'content': 'Unusable planning text fixture-secret'}}]}
        provider = NebiusModelProvider(api_key='fixture', opener=lambda *_args, **_kwargs: Response(body))
        with pytest.raises(ProviderError, match='invalid research plan'):
            provider.plan('Is Parabolic a good investment?')
    finally:
        model_diagnostic_sink.reset(token)
    assert seen == {'planning': {'response': 'Unusable planning text [redacted]'}}
