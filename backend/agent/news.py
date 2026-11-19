"""Evidence-bound news research over existing search and financial facts."""
import os
import re
import sqlite3
from datetime import date, timedelta
import json
from time import monotonic
from typing import Literal, get_args
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request

from pydantic import BaseModel, Field

from backend.agent.limits import ResourceLimitReached
from backend.agent.model_output import cited_prose, model_json, retain_model_response, usable_model_items
from backend.agent.models import NewsAnalysis, NewsCategory, NewsInsight, NewsItem
from backend.providers.historical import PublicationMetadata, publication_day
from backend.providers.live import NebiusModelProvider, ProviderError, open_provider_request
from backend.providers.transport import read_bounded


class NewsSelection(BaseModel):
    source_id: str
    category: NewsCategory
    summary: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(min_length=12, max_length=360)
    event_date: date | None = None
    attribution: Literal['management_statement', 'analyst_view', 'reported_event']
    related_source_ids: list[str] = Field(default_factory=list, max_length=3)


class NewsDraft(BaseModel):
    overall_assessment: Literal['positive', 'neutral', 'cautious', 'mixed', 'insufficient'] = 'insufficient'
    selections: list[NewsSelection] = Field(max_length=6)
    insights: list[NewsInsight] = Field(max_length=14)
    limitations: list[str] = Field(default_factory=list, max_length=8)


class NewsArticle(BaseModel):
    overall_assessment: Literal['positive', 'neutral', 'cautious', 'mixed', 'insufficient']
    insights: list[NewsInsight] = Field(min_length=1, max_length=6)
    limitations: list[str] = Field(default_factory=list, max_length=8)


def _decode_news_output(content, schema, references=(), *, partial=False):
    try:
        data = model_json(content, partial=partial)
    except ValueError:
        if content.lstrip().startswith(('{', '[')):
            raise
        data = {'insights': [{'kind': 'synthesis' if schema is NewsArticle else 'business',
                              'text': text[:1600], 'evidence_ids': refs[:10]}
                             for text, refs in cited_prose(content, references) if refs]}
    if isinstance(data, list):
        data = {'insights': data}
    if isinstance(data, dict) and 'insights' not in data:
        data['insights'] = data.get('paragraphs', [])
    if not isinstance(data, dict):
        raise ValueError('Expected a news analysis object')
    raw_insights = data.get('insights', [])
    if schema is NewsArticle and isinstance(raw_insights, list):
        raw_insights = [{**item, 'kind': 'synthesis'} if isinstance(item, dict) else item
                        for item in raw_insights]
    insights = usable_model_items(raw_insights, NewsInsight, 14 if schema is NewsDraft else 6)
    incomplete = not isinstance(raw_insights, list) or len(insights) != len(raw_insights)
    data['insights'] = insights
    if schema is NewsDraft:
        raw_selections = data.get('selections')
        selections = usable_model_items(raw_selections, NewsSelection, 6)
        if not selections and not insights and raw_selections != []:
            raise ValueError('No readable news selections or insights')
        incomplete |= not isinstance(raw_selections, list) or len(selections) != len(raw_selections)
        data['selections'] = selections
    assessment = data.get('overall_assessment')
    if not isinstance(assessment, str) or assessment not in get_args(NewsAnalysis.model_fields['overall_assessment'].annotation):
        data['overall_assessment'] = 'insufficient'
    notes = data.get('limitations')
    if isinstance(notes, str):
        notes = [notes]
    notes = [note for note in notes if isinstance(note, str) and note.strip()] if isinstance(notes, list) else []
    data['limitations'] = notes[:7] + ['Some incomplete model items were omitted.'] if incomplete else notes[:8]
    return schema.model_validate(data)


def news_output_cap(model):
    if model != 'zai-org/GLM-5.3-Flash':
        return 3072
    try:
        configured = int(os.getenv('NEWS_MAX_OUTPUT_TOKENS', '6144'))
    except ValueError:
        return 6144
    return max(1024, min(configured, 6144))


def _generate_phase(model, limits, payload, schema, phase, reserve, dispatched):
    failure = 'budget_unavailable'
    generate = model.synthesize_news if phase == 'analysis' else model.synthesize_news_article
    for compact in (False, True):
        if limits.model_calls >= min(limits.max_model_calls, 6) - reserve:
            break
        if compact and failure != 'generation_truncated':
            break
        if compact:
            payload = dict(payload, verified_financial_facts=payload['verified_financial_facts'][:8])
            if phase == 'analysis':
                payload['search_results'] = [
                    dict(item, excerpt=item['excerpt'][:340]) for item in payload['search_results'][:5]]
            else:
                # Keep every checked story; send less prose for each one.
                payload['checked_news'] = [dict(item, excerpt=None, summary=item['summary'][:220])
                                           for item in payload['checked_news']]
                payload['analysis_points'] = [dict(item, text=item['text'][:220])
                                               for item in payload['analysis_points']]
        source_ids = {item['source_id'] for item in
                      payload.get('search_results', payload.get('checked_news', []))}
        try:
            limits.remaining()
            stage = 'news_analysis' if phase == 'analysis' else 'news_article'
            if compact:
                stage += '_retry'
            with limits.provider_call('nebius', stage=stage) as event:
                content = None
                try:
                    content = generate(payload, limits, compact=compact)
                    data = _decode_news_output(content, schema, sorted(source_ids))
                except ValueError:
                    retain_model_response(stage, content)
                    raise
                except ProviderError as exc:
                    if getattr(exc, 'kind', None) == 'truncated' and event is not None:
                        event.error_code = 'OUTPUT_TRUNCATED'
                    if getattr(exc, 'kind', None) != 'truncated' or not getattr(exc, 'raw', None):
                        raise
                    try:
                        data = _decode_news_output(exc.raw, schema, sorted(source_ids), partial=True)
                    except ValueError:
                        retain_model_response(stage, exc.raw)
                        raise exc from None
                    if not data.insights and not getattr(data, 'selections', []):
                        raise
                finally:
                    if event is not None and event.http_attempts:
                        dispatched.update(source_ids)
                        event.news_context_source_ids = sorted(source_ids)
            return data, None
        except ProviderError as exc:
            failure = ('generation_truncated' if getattr(exc, 'kind', None) == 'truncated'
                       else 'generation_failed')
        except ValueError:
            failure = 'generation_failed'
        except ResourceLimitReached:
            break
    return None, failure


def news_queries(companies, query):
    focus = ('earnings guidance cash flow' if re.search(r'financial|earnings|cash flow|财报|利润|现金流', query, re.I) else
             'policy regulation industry' if re.search(r'policy|regulation|export|政策|监管|出口', query, re.I) else
             'strategy products competition')
    return {
        f'{company.company_name[:95]} {company.ticker or ""} recent announcements {focus}'.strip(): company.company_id
        for company in companies if company.resolution_status == 'resolved'
    }


def _normalize(text):
    return ' '.join(text.split())


class _PublisherPage(PublicationMetadata):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = set()

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag in {'script', 'style', 'head', 'noscript'}:
            self.hidden.add(tag)

    def handle_endtag(self, tag):
        self.hidden.discard(tag)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _publisher_check(source, excerpt, trusted_hosts, cutoff, timeout):
    """Match exact text against a bounded, allowlisted primary document."""
    try:
        address = urlsplit(source.url)
        if (source.source_type not in {'issuer_lead', 'regulator_lead'}
                or address.scheme != 'https' or address.hostname not in trusted_hosts
                or address.username is not None or address.password is not None
                or address.port not in {None, 443}):
            return False, None
        timeout = min(timeout, 2)
        deadline = monotonic() + timeout
        headers = {'Accept': 'text/html,application/xhtml+xml', 'User-Agent': 'Seldon Research Agent'}
        try:
            response = open_provider_request(Request(source.url, headers=headers), timeout)
        except HTTPError as redirect:
            location = redirect.headers.get('Location') if redirect.headers else None
            if redirect.code not in {301, 302, 303, 307, 308} or not location:
                raise
            redirect.close()
            canonical = urljoin(source.url, location)
            target = urlsplit(canonical)
            # Publisher links often canonicalize a trailing slash. Fetched
            # material cannot redirect the verifier to another origin.
            if target.scheme != 'https' or target.netloc.lower() != address.netloc.lower():
                return False, None
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False, None
            response = open_provider_request(Request(canonical, headers=headers), remaining)
        with response:
            if response.headers.get_content_type() not in {'text/html', 'application/xhtml+xml'}:
                return False, None
            raw = read_bounded(response, 768 * 1024, deadline)
        page = _PublisherPage()
        page.feed(raw.decode('utf-8', errors='replace'))
        if any(day is None or day > cutoff for day in page.modified):
            return False, None
        dates = set(page.published)
        published = next(iter(dates)) if len(dates) == 1 else None
        if published and published > cutoff:
            return False, published
        return bool(excerpt and _normalize(excerpt) in _normalize(' '.join(page.parts))), published
    except (OSError, ValueError):
        return False, None


def _publisher_key(source):
    # Conservative grouping. Related pages and subdomains are one publisher.
    return '.'.join(source.publisher.lower().split('.')[-2:])


def _explicit_date(day, excerpt):
    dates = {day.isoformat(), f'{day.year}年{day.month}月{day.day}日'}
    for name in (day.strftime('%B'), day.strftime('%b'), day.strftime('%b') + '.'):
        dates.add(f'{name} {day.day}, {day.year}')
        dates.add(f'{day.day} {name} {day.year}')
    return any(re.search(r'(?<![0-9])' + re.escape(value) + r'(?![0-9])', excerpt, re.I)
               for value in dates)


def _fallback(source):
    published = publication_day(source.publication_date)
    return NewsItem(source_id=source.source_id, category='other', attribution='unclassified',
                    summary=_normalize(source.quoted_text)[:500],
                    publication_date=published.isoformat() if published else None,
                    publication_basis='search_metadata' if published else 'unknown')


def _attribute_news_activity(runs, candidates, context, selected=(), cited=()):
    remaining_context, remaining_selected, remaining_cited = set(context), set(selected), set(cited)
    for event, batch in reversed(runs):
        ids = {key for key, source in candidates.items() if source.url in batch}
        event.news_context_source_ids = sorted(ids & remaining_context)
        event.news_selected_source_ids = sorted(ids & remaining_selected)
        event.news_cited_source_ids = sorted(ids & remaining_cited)
        remaining_context -= ids
        remaining_selected -= ids
        remaining_cited -= ids


def research_news(query, labels, web_sources, financial_metrics, model, limits, cutoff, trusted_hosts,
                  *, registry, reserve_model_calls=0, checkpoint=None):
    events = [event for event in limits.events if event.stage.startswith('news_') and event.provider == 'tavily']
    news_runs = [(event, urls) for event, urls in limits.search_results if event in events]
    state = NewsAnalysis(status='not_executed', as_of=cutoff.isoformat())
    if not events:
        state.limitations = ['No Tavily news operation was recorded for this task.']
        return state
    if not news_runs:
        state.status = 'search_failed'
        state.limitations = ['Tavily did not return a successful news response during this task.']
        return state
    urls = set().union(*(urls for _, urls in news_runs))
    eligible = []
    for source in web_sources:
        published = publication_day(source.publication_date)
        if (source.url in urls and source.quoted_text and
                (published is None or cutoff - timedelta(days=45) <= published <= cutoff)):
            eligible.append(source)
    eligible.sort(key=lambda source: (source.publication_date is not None,
                                      publication_day(source.publication_date) or date.min), reverse=True)
    candidates = {source.source_id: source for source in eligible[:14]}
    if not candidates:
        state.status = 'no_results'
        state.limitations = ['Tavily returned no usable news excerpts for the report window.']
        return state
    storage_failed = False

    def preserve(stage):
        nonlocal storage_failed
        if checkpoint is None:
            return
        try:
            checkpoint(stage, {'analysis': state.model_dump(mode='json'),
                               'sources': [source.model_dump(mode='json') for source in eligible]})
        except (OSError, sqlite3.Error):
            storage_failed = True

    def finish():
        if storage_failed:
            state.limitations.append('A news checkpoint could not be written; results remain in this report if saved.')
        return state

    state.status = 'partial'
    state.generation_status = 'news_collected'
    state.news_items = [_fallback(source) for source in list(candidates.values())[:6]]
    preserve('news_collected')
    if not isinstance(model, NebiusModelProvider):
        state.limitations = ['Tavily search results were preserved. A Nebius news analysis was unavailable.']
        return finish()

    facts = []
    seen = set()
    for metric in sorted(financial_metrics, key=lambda row: row.period_end, reverse=True):
        identity = (metric.company_id, metric.metric_name, metric.duration_kind, metric.capex_scope)
        if metric.actual_or_guidance != 'actual' or not metric.evidence_ids or identity in seen:
            continue
        seen.add(identity)
        facts.append(metric.model_dump(mode='json'))
        if len(facts) == 20:
            break
    context = {
        'question': query[:650], 'companies': labels, 'analysis_cutoff': cutoff.isoformat(),
        'verified_financial_facts': facts,
        'search_results': [{'source_id': source.source_id, 'title': source.title, 'publisher': source.publisher,
                            'source_kind': source.source_type, 'search_publication_date': source.publication_date,
                            'excerpt': source.quoted_text[:680]} for source in candidates.values()],
    }
    dispatched = set()
    draft, failure = _generate_phase(model, limits, context, NewsDraft, 'analysis', reserve_model_calls, dispatched)
    if draft is None:
        _attribute_news_activity(news_runs, candidates, dispatched)
        state.generation_status = failure
        state.failed_stage = 'analysis'
        state.limitations = ['News analysis did not finish; the collected headlines and excerpts are available.']
        preserve('analysis_incomplete')
        return finish()

    items = []
    for selection in draft.selections:
        source = candidates.get(selection.source_id)
        if not source or any(item.source_id == selection.source_id for item in items):
            continue
        excerpt = _normalize(selection.excerpt)
        if not excerpt or excerpt not in _normalize(source.quoted_text[:680]):
            continue
        matched, published = False, None
        if source.source_type in {'issuer_lead', 'regulator_lead'}:
            try:
                matched, published = _publisher_check(source, excerpt, trusted_hosts, cutoff, limits.remaining())
            except ResourceLimitReached:
                pass
        if published and not cutoff - timedelta(days=45) <= published <= cutoff:
            continue
        date_basis = 'publisher_metadata' if published else 'search_metadata' if source.publication_date else 'unknown'
        day = published or publication_day(source.publication_date)
        event_day = selection.event_date
        if event_day and (event_day > cutoff or not _explicit_date(event_day, excerpt)):
            event_day = None
        attribution = selection.attribution
        if attribution == 'management_statement' and source.source_type != 'issuer_lead':
            attribution = 'reported_event'
        if attribution == 'analyst_view' and not re.search(r'analyst|research|brokerage|分析师|研报', excerpt, re.I):
            attribution = 'reported_event'
        related = [key for key in dict.fromkeys(selection.related_source_ids)
                   if key in candidates and _publisher_key(candidates[key]) != _publisher_key(source)
                   and _normalize(candidates[key].quoted_text) != _normalize(source.quoted_text)]
        items.append(NewsItem(source_id=source.source_id, category=selection.category, summary=selection.summary,
                             excerpt=excerpt, attribution=attribution, publication_date=day.isoformat() if day else None,
                             publication_basis=date_basis if day else 'unknown',
                             event_date=event_day.isoformat() if event_day else None,
                             publisher_excerpt_matched=matched, related_source_ids=related))
    if not items and draft.insights:
        items = list(state.news_items)
    if not items:
        _attribute_news_activity(news_runs, candidates, dispatched)
        state.limitations = ['Tavily search results were preserved. The model did not select source-matched excerpts.']
        state.generation_status = 'evidence_insufficient'
        preserve('analysis_incomplete')
        return finish()
    selected = {item.source_id for item in items}
    for item in items:
        item.related_source_ids = [key for key in item.related_source_ids if key in selected]
    valid_finance = {identifier for fact in facts for identifier in fact['evidence_ids']}
    fact_metrics = {fact['metric_id'] for fact in facts}
    available_refs = selected | valid_finance

    def resolve_insight(insight):
        refs, _ = registry.resolve(insight.evidence_ids, available_refs, fact_metrics)
        if len(refs) > 10 or not selected.intersection(refs):
            return None
        return insight.model_copy(update={'evidence_ids': refs})

    insights = []
    for insight in draft.insights:
        insight = resolve_insight(insight)
        if insight is None:
            continue
        news_refs = selected.intersection(insight.evidence_ids)
        if insight.kind == 'cross_news' and len({_publisher_key(candidates[key]) for key in news_refs}) < 2:
            continue
        insights.append(insight)
    state.news_items = items
    state.insights = insights
    state.insights = [insight for insight in insights if insight.kind != 'synthesis']
    state.generation_status = 'news_analysis_completed'
    state.limitations = list(draft.limitations)
    preserve('news_analysis_completed')

    article_context = {
        'question': context['question'], 'companies': labels,
        'verified_financial_facts': facts,
        'checked_news': [item.model_dump(mode='json') for item in items],
        'analysis_points': [insight.model_dump(mode='json') for insight in state.insights],
        'limitations': state.limitations,
    }
    article, failure = _generate_phase(model, limits, article_context, NewsArticle, 'article',
                                       reserve_model_calls, dispatched)
    if article is None:
        state.generation_status = failure
        state.failed_stage = 'article'
        state.limitations.append('Relevant news was collected and analyzed; the synthesis article did not finish.')
    else:
        paragraphs = [resolved for insight in article.insights
                      if (resolved := resolve_insight(insight)) is not None]
        state.insights.extend(paragraphs)
        state.limitations.extend(article.limitations)
        if paragraphs:
            state.generation_status = 'synthesis_completed'
            state.overall_assessment = article.overall_assessment
            dated = {item.source_id for item in items if item.publication_date}
            if any(dated.intersection(paragraph.evidence_ids) for paragraph in paragraphs):
                state.status = 'completed'
            else:
                state.limitations.append('Publication dates were unavailable for the cited news.')
        else:
            state.generation_status = 'evidence_insufficient'
            state.limitations.append('The article did not have sufficient dated, traceable evidence.')
    cited = {identifier for insight in state.insights for identifier in insight.evidence_ids} & selected
    article_refs = {identifier for insight in state.insights if insight.kind == 'synthesis'
                    for identifier in insight.evidence_ids} & selected
    diverse = len({_publisher_key(candidates[key]) for key in article_refs}) >= 2
    primary_match = any(item.publisher_excerpt_matched and item.source_id in article_refs for item in items)
    if state.status == 'completed' and diverse and primary_match and state.overall_assessment != 'insufficient':
        state.confidence = 'medium'
    state.limitations.append('Search excerpts and attributed opinions do not alter verified financial facts.')
    if not primary_match:
        state.limitations.append('No cited news excerpt was independently matched to an allowlisted publisher page.')
    _attribute_news_activity(news_runs, candidates, dispatched, selected, cited)
    preserve('article_final')
    return finish()
