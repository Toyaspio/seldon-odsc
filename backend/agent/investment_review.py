"""Bounded investment review. Checkpoints contain no HTTP credentials or headers."""
import json
import os
import random
import time
from datetime import datetime, timezone
from hashlib import sha256

from backend.providers.live import ProviderError, retry_after_seconds
from .model_output import model_json
from .limits import ResourceLimitReached, record_provider_detail
from .models import (InvestmentAnalysis, InvestmentNarrative, InvestmentNarrativeSection, InvestmentPoint,
                     InvestmentReviewStatus, ProviderEvent)


GROUPS = (
    ('operations', {'operating_state', 'financial_quality', 'outlook', 'key_factor'}),
    ('thesis', {'thesis', 'market_expectations', 'supporting_case', 'opposing_case', 'tradeoff', 'horizon'}),
    ('scenarios', {'valuation', 'bull_case', 'base_case', 'bear_case'}),
)
VERDICTS = {'supported', 'partially_supported', 'unsupported', 'contradicted',
            'needs_qualification', 'unverifiable'}
POSITION_TOPICS = {'thesis', 'operating_state', 'financial_quality', 'outlook', 'key_factor',
                   'supporting_case', 'opposing_case', 'tradeoff', 'counterargument'}
QUALIFIED_VERDICTS = {'partially_supported', 'needs_qualification'}


def bounded_setting(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except ValueError:
        return default


def prepare_review(draft, context, required, citations, model, *, unmatched_citations=None):
    body = draft.model_dump(mode='json')
    queue = []
    verdicts = {}
    reasons = {}
    groups = dict((name, []) for name, _ in GROUPS)
    groups['risks'] = []
    for index, point in enumerate(draft.points):
        key = str(index)
        if key not in citations:
            verdicts[key] = 'unverifiable'
            reasons[key] = 'The paragraph’s citations could not be matched to the supplied evidence.'
            continue
        group = next((name for name, topics in GROUPS if point.topic in topics), 'risks')
        groups[group].append(key)
    for group, ids in groups.items():
        for offset in range(0, len(ids), 4):
            queue.append({'id': f'{group}-{offset // 4 + 1}', 'items': ids[offset:offset + 4],
                          'depth': 0, 'attempts': 0, 'repair': None, 'errors': []})
    return {'version': 1, 'draft': body, 'draft_hash': sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'model': model, 'context': context, 'required': sorted(required),
            'queue': queue, 'verdicts': verdicts,
            'batch_status': {}, 'reasons': reasons, 'resolved_citations': citations,
            'unmatched_citations': unmatched_citations or {},
            'suggested_revisions': {}, 'attempt_log': [], 'reserved': 0, 'in_flight': None}


def _review_point(job, index):
    point = job['draft']['points'][int(index)]
    return {**point, 'evidence_ids': job.get('resolved_citations', {}).get(index, point['evidence_ids'])}


def _financial_anchors(facts, refs):
    anchors = {}
    for fact in facts:
        source = next((ref for ref in fact['evidence_ids'] if ref in refs), None)
        if source and fact.get('company_id'):
            anchors.setdefault(fact['company_id'], source)
    return anchors


def supported_direction(stance, points, facts, required):
    if stance in {'insufficient', 'provisional_neutral'} or not required or not any(
            point.topic == 'thesis' for point in points):
        return False
    refs = {ref for point in points if point.topic in POSITION_TOPICS for ref in point.evidence_ids}
    return set(required) <= _financial_anchors(facts, refs).keys()


def _fundamental_basis(facts, refs):
    """Render verified inputs without reusing figures from an unqualified paragraph."""
    labels = {'revenue': 'Revenue', 'operating_cash_flow': 'Operating cash flow',
              'free_cash_flow': 'Free cash flow', 'cash_capex': 'Cash capital expenditure'}
    points = []
    used = set()
    for fact in facts:
        metric = fact.get('metric_name')
        ids = fact['evidence_ids']
        value = fact.get('value')
        start, end = fact.get('period_start'), fact.get('period_end')
        currency = fact.get('currency')
        key = (fact.get('company_id'), metric)
        if (key in used or metric not in labels or not ids or not set(ids) <= refs
                or type(value) not in (int, float) or not start or not end or not currency
                or fact.get('unit') != currency):
            continue
        points.append(InvestmentPoint(
            topic='financial_quality', evidence_ids=ids,
            text=f'{labels[metric]} was {currency} {value:,.0f} for the period {start} to {end}.'))
        used.add(key)
        if len(points) == 3:
            break
    return points


def summarize(job, *, pending=False):
    draft = InvestmentAnalysis.model_validate(job['draft'])
    supported = [point.model_copy(update={'evidence_ids': _review_point(job, str(index))['evidence_ids']})
                 for index, point in enumerate(draft.points)
                 if job['verdicts'].get(str(index)) == 'supported']
    for index, point in enumerate(draft.points):
        key = str(index)
        if job['verdicts'].get(key) in QUALIFIED_VERDICTS and key in job.get('resolved_citations', {}):
            qualification = (job.get('suggested_revisions', {}).get(key)
                             or job.get('reasons', {}).get(key))
            if qualification:
                supported.append(point.model_copy(update={
                    'evidence_ids': _review_point(job, key)['evidence_ids']}))
    eligible = job.get('resolved_citations')
    theses = [str(index) for index, point in enumerate(draft.points)
              if point.topic == 'thesis' and (eligible is None or str(index) in eligible)]
    technical = any(status in {'failed', 'interrupted', 'budget_blocked'}
                    for status in job['batch_status'].values())
    unfinished = bool(job['queue']) or technical
    facts = [*job['context']['verified_financial_facts'],
             *(claim for archive in job['context'].get('saved_report_context', [])
               for claim in archive.get('verified_claims', []) if claim.get('company_id'))]
    comparative = bool(job['context'].get('saved_report_context'))
    primary = next((str(i) for i, point in enumerate(draft.points) if point.topic == 'thesis'), None)
    blocked_thesis = job['verdicts'].get(primary) in {'contradicted', 'unsupported'}
    # A qualified thesis is deliverable with its qualification and provisional status.
    fully_supported = [point.model_copy(update={'evidence_ids': _review_point(job, str(i))['evidence_ids']})
                       for i, point in enumerate(draft.points) if job['verdicts'].get(str(i)) == 'supported']
    approved = not blocked_thesis and supported_direction(draft.stance, fully_supported, facts, job['required'])
    provisional_thesis = next((draft.points[int(index)].model_copy(
        update={'evidence_ids': _review_point(job, index)['evidence_ids']})
        for index in theses if job['verdicts'].get(index) in {None, 'supported'} or
        (job['verdicts'].get(index) in QUALIFIED_VERDICTS and
         (job.get('suggested_revisions', {}).get(index) or job.get('reasons', {}).get(index)))), None)
    preliminary = (not approved and not blocked_thesis and provisional_thesis is not None
                   and draft.stance not in {'insufficient', 'provisional_neutral'})
    qualified = {'supported', 'partially_supported'}
    contextual_refs = {ref for index, refs in (eligible or {}).items()
                       if job['verdicts'].get(index) in qualified for ref in refs}
    contextual_view = (primary is None and draft.stance not in {'insufficient', 'provisional_neutral'}
                       and bool(_financial_anchors(facts, contextual_refs)))
    narrative_view = (primary is None and bool(draft.narrative) and not blocked_thesis
                      and draft.stance not in {'insufficient', 'provisional_neutral'}
                      and not any(verdict == 'contradicted' for verdict in job['verdicts'].values()))
    preliminary = preliminary or contextual_view or narrative_view
    if pending:
        state = 'review_pending'
    elif unfinished:
        state = ('review_partial' if job['verdicts'] and any(
                 value == 'completed' for value in job['batch_status'].values()) else 'review_failed_technical')
    else:
        state = 'review_completed' if job['batch_status'] else 'review_blocked'
    reason = draft.observation_reason
    if not approved and not reason:
        reason = next((job['reasons'][index] for index in theses
                       if job['verdicts'].get(index) != 'supported' and job.get('reasons', {}).get(index)), None)
    if preliminary:
        if provisional_thesis is not None:
            supported = [provisional_thesis, *(p for p in supported if p.topic != 'thesis')][:16]
        if contextual_view:
            if not supported:
                supported = _fundamental_basis(facts, contextual_refs)
            reason = reason or ('This is a fundamental assessment. The market reaction depends on how results '
                                'and guidance compare with investor expectations.')
        elif narrative_view:
            reason = reason or 'This is a tentative interpretation. Its factual premises still need confirmation.'
        else:
            reason = reason or 'This view depends on confirmation of the cited premises.'
    elif comparative and not blocked_thesis:
        if provisional_thesis and unfinished and provisional_thesis not in supported:
            supported.insert(0, provisional_thesis)
        if provisional_thesis and unfinished:
            reason = reason or 'The comparison depends on confirmation of its cited premises.'
    elif not approved:
        supported = [p for p in supported if p.topic != 'thesis']
        if not reason:
            reason = 'The available evidence does not establish an overall direction.'
    opinion = 'approved' if approved else 'provisional' if preliminary or unfinished else 'evidence_insufficient'
    analysis = draft.model_copy(update={
        'stance': draft.stance if approved or preliminary else 'insufficient',
        'observation_reason': reason,
        'confidence': draft.confidence if approved else 'low',
        'points': supported,
        'narrative': None,
    })
    generation = 'generation_partial' if job.get('generation_partial') else 'generation_completed'
    return analysis, InvestmentReviewStatus(generation=generation, review=state, opinion=opinion,
                                            verdicts=job['verdicts'], reasons=job.get('reasons', {}),
                                            suggested_revisions=job.get('suggested_revisions', {}),
                                            batch_status={**dict.fromkeys((item['id'] for item in job['queue']), 'pending'),
                                                          **job['batch_status']})


def investment_article(job):
    language = job['context'].get('article_language')
    if language not in ('en', 'zh') or not job['draft']['points']:
        return None
    return InvestmentNarrative(language=language, sections=[
        InvestmentNarrativeSection(**_review_point(job, str(index)),
            review_verdict=job['verdicts'].get(str(index), 'pending'),
            review_reason=job.get('reasons', {}).get(str(index)),
            suggested_revision=job.get('suggested_revisions', {}).get(str(index)))
        for index in range(len(job['draft']['points']))
    ]).model_dump(mode='json')


def _decode(raw, ids, *, partial=False):
    try:
        items = model_json(raw, partial=partial)['verdicts']
        if not isinstance(items, list):
            raise ValueError('verdicts must be an array')
        found = {}
        for item in items:
            if partial and (not isinstance(item, dict) or 'item_id' not in item or 'verdict' not in item):
                continue
            key = item['item_id']
            verdict = item['verdict']
            reason = item.get('short_reason', '')
            if not isinstance(key, str) or not isinstance(verdict, str) or key not in ids or key in found or verdict not in VERDICTS:
                if partial:
                    continue
                raise ValueError('invalid item ID or verdict')
            if not isinstance(reason, str):
                if partial:
                    reason = ''
                else:
                    raise ValueError('invalid short_reason type')
            # Explanatory length is a presentation constraint; keep the complete valid verdict.
            found[key] = (verdict, reason[:320] + ('…' if len(reason) > 320 else ''))
        if not partial and found.keys() != set(ids):
            raise ValueError('missing item IDs')
        return found
    except json.JSONDecodeError:
        raise ValueError('invalid JSON') from None
    except (KeyError, TypeError):
        raise ValueError('missing required verdict fields') from None


def _safe_reason(text):
    for name in ('NEBIUS_API_KEY', 'TAVILY_API_KEY'):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, '[redacted]')
    return text


def run_review(job, model, limits, checkpoint):
    if job['in_flight']:
        # A crash after sending has ambiguous billing. Never replay that batch automatically.
        job['batch_status'][job['in_flight']] = 'interrupted'
        event = ProviderEvent(provider='nebius', stage='investment_review', outcome='failed',
                              batch_id=job['in_flight'], error_code='dispatch_status_unknown', model=job['model'])
        job['attempt_log'].append({'event': event.model_dump(mode='json'),
                                   'failure_kind': 'interrupted', 'response': None})
        job['queue'].pop(0)
        job['in_flight'] = None
        checkpoint()
    limits.model = job['model']
    budget = bounded_setting('INVESTMENT_REVIEW_TOTAL_TOKENS', 16384, 256, 16384)
    hard = bounded_setting('INVESTMENT_REVIEW_HARD_TOKENS', 4096, 256, 4096)
    max_attempts = bounded_setting('INVESTMENT_REVIEW_ATTEMPTS', 3, 1, 3)
    split_depth = bounded_setting('INVESTMENT_REVIEW_SPLITS', 2, 0, 2)
    while job['queue']:
        batch = job['queue'][0]
        key, ids = batch['id'], batch['items']
        preferred = (bounded_setting('INVESTMENT_REVIEW_COMPLEX_TOKENS', 3072, 256, hard)
                     if len(ids) >= 4 and any(job['draft']['points'][int(i)]['topic'] in
                                             {'valuation', 'bull_case', 'bear_case'} for i in ids)
                     else bounded_setting('INVESTMENT_REVIEW_BATCH_TOKENS', 2048, 256, hard))
        cap = min(preferred, hard, budget - job['reserved'])
        wait = batch.get('retry_wait', 0)
        try:
            available = limits.remaining()
        except ResourceLimitReached:
            available = 0
        if available <= wait:
            # Keep unsent work queued for startup recovery within the same token ledger.
            checkpoint()
            break
        if cap < 256:
            for queued in job['queue']:
                job['batch_status'][queued['id']] = 'budget_blocked'
            job['queue'].clear()
            checkpoint()
            break
        if wait:
            time.sleep(wait)
        points = [{'item_id': i, **_review_point(job, i)} for i in ids]
        facts = job['context']['verified_financial_facts']
        ratios = job['context'].get('cash_conversion_calculations', [])
        for point in points:
            citations = set(point['evidence_ids'])
            point['cited_financial_facts'] = [fact['metric_id'] for fact in facts
                                              if fact.get('metric_id') and fact['evidence_ids']
                                              and set(fact['evidence_ids']) <= citations]
            point['cited_cash_conversion'] = [ratio for ratio in ratios
                                               if set(ratio['evidence_ids']) <= citations]
        unmatched = job.get('unmatched_citations', {})
        for point in points:
            if point['item_id'] in unmatched:
                point['unmatched_citation_count'] = len(unmatched[point['item_id']])
        citations = {ref for point in points for ref in point['evidence_ids']}
        payload = {'batch_id': key, 'question': job['context']['question'], 'items': points,
                   'stance': job['draft']['stance'], 'required_issuers': job['required'],
                   'facts': [fact for fact in job['context']['verified_financial_facts']
                             if citations.intersection(fact['evidence_ids'])],
                   'valuation_research': [item for item in job['context'].get('valuation_research', [])
                                          if item.get('price_source_id') in citations
                                          or any(citations.intersection(measure['evidence_ids'])
                                                 for measure in item['measures'])],
                   'external_leads': [lead for lead in job['context']['unverified_external_leads']
                                      if lead['source_id'] in citations]}
        payload['saved_report_context'] = [
            {**archive, 'verified_claims': [claim for claim in archive['verified_claims']
                if isinstance(claim, dict) and citations.intersection(claim['evidence_ids'])]}
            for archive in job['context'].get('saved_report_context', [])
            if any(isinstance(claim, dict) and citations.intersection(claim['evidence_ids'])
                   for claim in archive['verified_claims'])
        ]
        payload['article_thesis'] = [
            {'text': point['text'][:450], 'evidence_ids': _review_point(job, str(index))['evidence_ids']}
            for index, point in enumerate(job['draft']['points']) if point['topic'] == 'thesis'
        ][:2]
        if batch['repair']:
            payload['repair'] = {'schema_error': batch['repair'], 'instruction': 'Return complete verdict JSON only.'}
        job['reserved'] += cap
        batch['attempts'] += 1
        job['in_flight'] = key
        checkpoint()
        raw = ''
        retry_after = None
        found = None
        with_error = None
        try:
            with limits.provider_call('nebius', stage='investment_review') as event:
                record_provider_detail('nebius', batch_id=key, retry_wait_seconds=wait,
                                       max_output_tokens=cap)
                try:
                    raw = model.review_batch(payload, limits, cap)
                    found = _decode(raw, ids)
                except ValueError as error:
                    with_error = str(error)
                    record_provider_detail('nebius', error_code='review_format_error')
                    raise ProviderError('Review format error') from None
        except (ProviderError, ResourceLimitReached) as error:
            kind = 'format' if with_error else getattr(error, 'kind', 'permanent')
            retry_after = getattr(error, 'retry_after', None)
            if kind == 'format' and not with_error:
                with_error = 'Invalid provider JSON or completion envelope'
            raw = getattr(error, 'raw', raw)
        else:
            kind = None
        event_data = limits.events[-1].model_dump(mode='json')
        # Raw response is private to the checkpoint. It is never returned by the report API.
        if kind:
            for key_name in ('NEBIUS_API_KEY', 'TAVILY_API_KEY'):
                secret = os.environ.get(key_name)
                if secret and isinstance(raw, str):
                    raw = raw.replace(secret, '[redacted]')
        job['attempt_log'].append({'event': event_data, 'failure_kind': kind,
                                   'response': str(raw)[:24000] if kind else None})
        job['in_flight'] = None
        if kind in {'format', 'truncated'} and raw:
            try:
                recovered = _decode(raw, ids, partial=True)
            except ValueError:
                recovered = {}
            if recovered:
                job['verdicts'].update({i: verdict for i, (verdict, _) in recovered.items()})
                job['reasons'].update({i: _safe_reason(reason) for i, (_, reason) in recovered.items()})
                ids = [i for i in ids if i not in recovered]
                batch['items'] = ids
                if ids and not batch['repair']:
                    batch['repair'] = 'Only the remaining item IDs need verdicts.'
                    batch['retry_wait'] = 0
                    checkpoint()
                    continue
                if not ids:
                    job['batch_status'][key] = 'completed'
                    job['queue'].pop(0)
                    checkpoint()
                    continue
        if found is not None:
            job['verdicts'].update({key: verdict for key, (verdict, _) in found.items()})
            job.setdefault('reasons', {}).update({key: _safe_reason(reason) for key, (_, reason) in found.items()})
            response = model_json(raw)
            for item in response['verdicts']:
                revision = item.get('suggested_revision')
                if item['verdict'] in {'needs_qualification', 'partially_supported'} and isinstance(revision, str):
                    job.setdefault('suggested_revisions', {})[item['item_id']] = _safe_reason(revision[:360])
            job['batch_status'][key] = 'completed'
            job['queue'].pop(0)
        elif kind == 'temporary' and batch['attempts'] < max_attempts:
            batch['retry_wait'] = max(2 ** (batch['attempts'] - 1) + random.uniform(0, .35),
                                      retry_after_seconds(retry_after))
        elif kind == 'format' and not batch['repair'] and batch['attempts'] < max_attempts and with_error != 'missing item IDs':
            batch['repair'] = with_error
            batch['retry_wait'] = 0
        elif kind in {'format', 'truncated'} and len(ids) > 1 and batch['depth'] < split_depth:
            mid = len(ids) // 2
            job['batch_status'][key] = 'split'
            job['queue'][:1] = [{'id': f'{key}.{i}', 'items': part, 'depth': batch['depth'] + 1,
                                 'attempts': 0, 'repair': None, 'errors': []}
                                for i, part in enumerate((ids[:mid], ids[mid:]), 1)]
        else:
            job['batch_status'][key] = 'failed'
            job['queue'].pop(0)
            if kind == 'permanent' and event_data.get('http_status') in {400, 401, 403, 404}:
                for queued in job['queue']:
                    job['batch_status'][queued['id']] = 'failed'
                job['queue'].clear()
        checkpoint()
    return summarize(job)
