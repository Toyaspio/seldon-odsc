from __future__ import annotations

from datetime import date, datetime, timezone
from threading import Lock
from uuid import uuid4
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from backend.settings import (
    ConnectionDiagnostics, SettingsStatus, SettingsUpdate, connection_diagnostics,
    load_startup_settings, runtime_values, require_local_request, save_settings, status as settings_status,
)

load_startup_settings()

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from backend.agent.models import ProviderActivity, Report, TaskStatus
from backend.agent.pipeline import (ResearchOrchestrator, ResearchPlanner, CompanyResolver, CompanyUnresolved,
    ToolRouter, EvidenceExtractor, FinancialValidator, EvidenceVerifier, ReportBuilder)
from backend.providers.interfaces import (MockModelProvider, MockSearchProvider,
    MockFinancialDataProvider, NebiusModelProvider, TavilySearchProvider)
from backend.storage.store import (load_all, save_task, save_completion, stage_completion,
                                   save_review_checkpoint, load_pending_reviews, finish_review, ReviewCheckpointConflict,
                                   save_news_checkpoint)
from backend.agent.investment_review import investment_article, run_review, summarize
from backend.agent.models import InvestmentReviewStatus
from backend.agent.model_output import model_diagnostic_sink
from backend.storage.store import load_review_for_report
from backend.agent.limits import ResourceLimitReached, ResearchLimits, InvalidResearchConfiguration
from backend.evidence.verifier import qualify_claim
from backend.providers.live import ProviderError
from backend.providers.sec import SecFinancialDataProvider, available_by

@asynccontextmanager
async def _lifespan(_app):
    _recover_investment_reviews()
    yield


app = FastAPI(title="SELDON SYSTEM", version="0.1.0", lifespan=_lifespan)
_tasks, _reports = load_all()
_lock = Lock()
_executor = ThreadPoolExecutor(max_workers=4)
RECOMMENDED = ["Microsoft", "Alphabet", "Amazon", "Meta"]
_model_mode = os.getenv('MODEL_PROVIDER', 'nebius').lower()
_search_mode = os.getenv('SEARCH_PROVIDER', 'tavily').lower()
_financial_mode = os.getenv('FINANCIAL_DATA_PROVIDER', 'sec').lower()
_model_provider = NebiusModelProvider() if _model_mode == 'nebius' else MockModelProvider()
_search_provider = TavilySearchProvider() if _search_mode == 'tavily' else MockSearchProvider()
_financial_provider = SecFinancialDataProvider() if _financial_mode == 'sec' else MockFinancialDataProvider()
_live_enabled = _model_mode == 'nebius' or _search_mode == 'tavily' or _financial_mode == 'sec'
_orchestrator = ResearchOrchestrator(ResearchPlanner(_model_provider), CompanyResolver(),
    ToolRouter(_search_provider, _financial_provider), EvidenceExtractor(),
    FinancialValidator(), EvidenceVerifier(), ReportBuilder())

# Log the adapters that were actually constructed, using fixed class names only.
if _live_enabled:
    _startup_mode = ('LIVE configured' if (_model_mode, _search_mode, _financial_mode)
                     == ('nebius', 'tavily', 'sec') else 'Mixed')
else:
    _startup_mode = 'Mock'
logging.getLogger('uvicorn.error').info(
    'Research providers (%s): model=%s, search=%s, financial=%s',
    _startup_mode, type(_model_provider).__name__, type(_search_provider).__name__,
    type(_financial_provider).__name__,
)


class ResearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    companies: list[str] = Field(default_factory=list)
    as_of_date: date | None = None
    report_ids: list[str] = Field(default_factory=list, max_length=4)


class ProviderHealth(BaseModel):
    status: str
    mode: str
    provider: str
    model: str | None


class TaskResourceUsage(BaseModel):
    model: str | None = None
    estimated_cost_usd: float | None = None
    model_calls: int
    prompt_tokens: int
    completion_tokens: int
    search_queries: int
    search_rounds: int
    elapsed_seconds: float


class ResearchTaskStatus(BaseModel):
    task_id: str
    status: TaskStatus
    progress: int
    current_step: str
    created_at: str
    updated_at: str
    report_id: str | None
    error_code: str | None
    error_message: str | None
    resource_usage: TaskResourceUsage | None = None
    providers: dict[str, ProviderActivity] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_saved_report(report):
    try:
        return Report.model_validate(report)
    except ValueError as validation:
        recovered = dict(report)
        rows = {}
        for error in validation.errors():
            field, *path = error['loc']
            if field in {'companies', 'metrics', 'claims', 'sources', 'provider_events', 'valuations'}:
                if path and isinstance(path[0], int):
                    rows.setdefault(field, set()).add(path[0])
                else:
                    recovered[field] = []
            elif field in {'investment_analysis', 'investment_draft', 'investment_review', 'investment_narrative',
                           'news_analysis', 'research_quality', 'valuation_status'}:
                recovered[field] = None
        for field, removed in rows.items():
            recovered[field] = [row for index, row in enumerate(report[field]) if index not in removed]
        return Report.model_validate(recovered)


def _restore_saved_investment_view(report):
    quality = report.get('research_quality') or {}
    old_gap = 'The requested investment judgment could not be established.'
    qualified_thesis = any(section.get('topic') == 'thesis' and
                           section.get('review_verdict') in {'partially_supported', 'needs_qualification'}
                           for section in (report.get('investment_narrative') or {}).get('sections', []))
    if old_gap not in quality.get('core_gaps', []) and not qualified_thesis:
        return report
    saved = load_review_for_report(report['task_id'])
    if saved is None:
        draft = report.get('investment_draft')
        review = report.get('investment_review')
        narrative = report.get('investment_narrative') or {}
        if not (qualified_thesis and draft and review):
            return report
        # Imported reports retain their parsed review even without private checkpoints.
        sections = narrative.get('sections', [])
        job = dict(draft=draft, context={'verified_financial_facts': report.get('metrics', []),
                                        'article_language': narrative.get('language', 'en')},
                   required=[company['company_id'] for company in report.get('companies', [])
                             if company.get('resolution_status') == 'resolved'],
                   queue=[], verdicts=review.get('verdicts', {}), reasons=review.get('reasons', {}),
                   suggested_revisions=review.get('suggested_revisions', {}),
                   batch_status=review.get('batch_status', {}),
                   resolved_citations={str(i): section['evidence_ids'] for i, section in enumerate(sections)
                                       if section.get('evidence_ids')},
                   generation_partial=review.get('generation') == 'generation_partial')
        saved = report, job
    base, job = saved
    analysis, state = summarize(job)
    return dict(report, status=base['status'] if old_gap in quality.get('core_gaps', []) else report['status'],
                research_quality=base.get('research_quality') if old_gap in quality.get('core_gaps', []) else quality,
                investment_analysis=analysis.model_dump(mode='json'),
                investment_review=state.model_dump(mode='json'), investment_narrative=investment_article(job))


def _review_report(task_id, base, job, limits):
    def checkpoint():
        save_review_checkpoint(task_id, base, job)

    blocked = False
    try:
        checkpoint()
        analysis, state = run_review(job, _model_provider, limits, checkpoint)
    except (OSError, sqlite3.Error, ValueError):
        logging.getLogger(__name__).warning('Could not persist investment review checkpoint for task %s', task_id)
        analysis, state = summarize(job, pending=True)
        state = state.model_copy(update={'review': 'review_blocked', 'opinion': 'provisional'})
        blocked = True
    draft = job['draft']
    # Draft narrative and unreviewed points have their own explicitly provisional surface.
    result = dict(base, investment_analysis=analysis.model_dump(mode='json'), investment_draft=draft,
                  investment_review=state.model_dump(mode='json'), investment_narrative=investment_article(job),
                  provider_events=[*base.get('provider_events', []),
                                   *(item['event'] for item in job['attempt_log'])])
    result['providers'] = {key: dict(value) for key, value in base['providers'].items()}
    if job['attempt_log']:
        result['providers']['nebius'] = aggregate_review_provider(base, job)
    if blocked:
        result['limitations'] = [*base['limitations'], 'Investment review checkpoint could not be saved.']
    if job.get('unmatched_citations'):
        result['limitations'] = [*result['limitations'],
                                 'Some draft citations could not be located. Review used only sources that could be matched.']
    return result


def aggregate_review_provider(base, job):
    totals = dict(base['providers']['nebius'])
    for attempt in job['attempt_log']:
        event = attempt['event']
        totals['calls'] += 1
        totals[event['outcome']] += 1
        for field in ('http_attempts', 'http_responses'):
            totals[field] = (totals.get(field) or 0) + event[field]
    return totals


def _review_finished(job, result):
    state = result['investment_review']
    return not job['queue'] and not (state['review'] == 'review_blocked' and state['opinion'] == 'provisional')


def _resume_review(task_id, report, job):
    limits = ResearchLimits()
    limits.providers['nebius'].enabled = True
    try:
        result = _review_report(task_id, report, job, limits)
    except ReviewCheckpointConflict:
        return
    with _lock:
        task = dict(_tasks[task_id], status=result['status'], progress=100, current_step='done',
                    report_id=result['report_id'], error_code=None, error_message=None,
                    providers=result['providers'], updated_at=_now())
        save_completion(task, result)
        _reports[result['report_id']] = result
        _tasks[task_id] = task
        if _review_finished(job, result):
            finish_review(task_id)


def _recover_investment_reviews():
    if not isinstance(_model_provider, NebiusModelProvider) or not runtime_values('NEBIUS_API_KEY')[0]:
        return
    try:
        pending = load_pending_reviews()
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).warning('Investment review recovery store unavailable')
        return
    for task_id, report, job in pending:
        if task_id in _tasks:
            _executor.submit(_resume_review, task_id, report, job)


def _run(task_id: str, request: ResearchRequest, inputs: tuple[Report, ...] = ()) -> None:
    limits = ResearchLimits(max_search_rounds=0, max_search_queries=0,
                            request_timeout_seconds=0, max_model_calls=0)
    jobs = []
    partial_report = None

    def checkpoint_research(stage, data):
        nonlocal partial_report
        partial_report = data.get('report')
        save_news_checkpoint(task_id, stage, data)

    steps = {'planning': (10, 'researching'), 'resolving_companies': (25, 'researching'),
             'retrieving': (40, 'researching'), 'checking_evidence': (70, 'verifying'),
             'follow_up': (80, 'verifying'), 'investment_analysis': (88, 'verifying'),
             'building_report': (95, 'verifying')}

    def progress(step: str) -> None:
        percent, status = steps[step]
        with _lock:
            _tasks[task_id].update(status=status, progress=percent, current_step=step, updated_at=_now())
            save_task(_tasks[task_id])

    def fail(code: str, message: str) -> None:
        with _lock:
            if partial_report and (partial_report['metrics'] or partial_report.get('news_analysis')):
                report = dict(partial_report, task_id=task_id)
                report['limitations'] = [*report['limitations'], message]
                task = dict(_tasks[task_id], status='partial', progress=100, current_step='done',
                            report_id=report['report_id'], resource_usage=limits.snapshot(),
                            providers=limits.provider_snapshot(), updated_at=_now())
                try:
                    save_completion(task, report)
                except (OSError, sqlite3.Error):
                    logging.getLogger(__name__).warning('Partial report could not be saved for task %s', task_id)
                _reports[report['report_id']] = report
                _tasks[task_id] = task
                return
            _tasks[task_id].update(status="failed", current_step="failed", error_code=code,
                                   error_message=message, resource_usage=limits.snapshot(),
                                   providers=limits.provider_snapshot(), updated_at=_now())
            save_task(_tasks[task_id])

    try:
        limits = ResearchLimits()
        cutoff = request.as_of_date.isoformat() if request.as_of_date else None
        if _live_enabled:
            diagnostic = model_diagnostic_sink.set(
                lambda stage, data: save_news_checkpoint(task_id, 'model_output:' + stage, data))
            try:
                fields = _orchestrator.run_agent(request.query, request.companies, cutoff,
                                                 progress=progress, limits=limits, input_reports=inputs, on_draft=jobs.append,
                                                 on_news_checkpoint=checkpoint_research).model_dump(mode='json')
            finally:
                model_diagnostic_sink.reset(diagnostic)
        else:
            progress('resolving_companies')
            fields = _orchestrator.run_mock(request.query, request.companies)
            fields['data_as_of'] = cutoff
        if inputs and any(not any(company.resolution_status == 'resolved' for company in item.companies)
                          for item in inputs):
            fields['status'] = 'partial'
            fields['limitations'].append('Some selected reports do not identify a company. The comparison covers the identified issuers.')
        fields['task_id'] = task_id
        report = Report.model_validate(fields).model_dump(mode='json')
        if jobs:
            report = _review_report(task_id, report, jobs[0], limits)
        elif report.get('investment_analysis'):
            synthesis = next((event for event in report['provider_events']
                              if event['stage'] == 'investment_synthesis'), None)
            failed = synthesis is not None and synthesis['outcome'] == 'failed'
            report['investment_review'] = InvestmentReviewStatus(
                generation='generation_failed' if failed else 'generation_partial',
                review='review_blocked', opinion='provisional' if failed else 'evidence_insufficient'
            ).model_dump(mode='json')
        with _lock:
            report_id = report['report_id']
            task = dict(_tasks[task_id], status=report['status'], progress=100, current_step="done",
                        report_id=report_id, resource_usage=limits.snapshot(),
                        providers=report['providers'], updated_at=_now())
            try:
                save_completion(task, report)
            except (OSError, sqlite3.Error):
                try:
                    stage_completion(task, report)
                    warning = 'The report is available. Database recovery is pending.'
                except (OSError, ValueError):
                    warning = 'The report is available only until the service stops. Export it now.'
                logging.getLogger(__name__).warning('Report database write failed for task %s', task_id)
                report = dict(report, limitations=[warning, *report['limitations']])
            _reports[report_id] = report
            _tasks[task_id] = task
            if jobs and _review_finished(jobs[0], report):
                try:
                    finish_review(task_id)
                except (OSError, sqlite3.Error):
                    logging.getLogger(__name__).warning('Investment review completion marker could not be saved')
    except InvalidResearchConfiguration as exc:
        fail('INVALID_RESEARCH_CONFIGURATION', str(exc))
    except CompanyUnresolved:
        fail('COMPANY_UNRESOLVED', 'Specify a company name, ticker or filing identifier')
    except ResourceLimitReached:
        fail('RESEARCH_LIMIT_REACHED', 'The research exceeded its configured limits')
    except ProviderError as exc:
        fail('PROVIDER_FAILED', exc.public_message or
             'A required research provider is unavailable or returned invalid data')
    except Exception:
        fail('RESEARCH_EXECUTION_FAILED' if _live_enabled else 'MOCK_EXECUTION_FAILED', 'Research task failed')


@app.get("/api/health", response_model=ProviderHealth)
def health():
    return {"status": "ok", "mode": "live-unverified" if _live_enabled else "mock",
            "provider": "Nebius Token Factory" if _model_mode == "nebius" else "Mock",
            "model": _model_provider.active_model() if isinstance(_model_provider, NebiusModelProvider) else None}


@app.get("/api/companies")
def companies():
    return {"companies": RECOMMENDED}


@app.post("/api/research", status_code=202)
def create_research(request: ResearchRequest):
    task_id = str(uuid4())
    inputs = ()
    with _lock:
        if request.report_ids:
            if request.as_of_date or request.companies or len(set(request.report_ids)) != len(request.report_ids) or len(request.report_ids) < 2:
                raise HTTPException(422, 'Select two to four distinct reports for current comparative research.')
            reports = [_reports.get(identifier) for identifier in request.report_ids]
            if any(not item or item['status'] not in {'completed', 'partial'}
                   or item['data_mode'] == 'mock' for item in reports):
                raise HTTPException(422, 'Select saved completed or partial reports with real evidence.')
            if (_model_mode != 'nebius' or _search_mode != 'tavily'
                    or not all(runtime_values('NEBIUS_API_KEY', 'TAVILY_API_KEY'))):
                raise HTTPException(409, 'Comparative research requires configured Nebius and Tavily Providers.')
            if _financial_mode != 'sec' and not os.getenv('SELDON_OFFICIAL_ISSUER_CATALOG'):
                raise HTTPException(409, 'Comparative research requires an official financial Provider.')
            inputs = tuple(_read_saved_report(item) for item in reports)
            names = list(dict.fromkeys((company.cik or company.company_name)
                                       for item in inputs for company in item.companies
                                       if company.resolution_status == 'resolved'))
            if not 1 <= len(names) <= 6:
                raise HTTPException(422, 'The selected reports need at least one resolved issuer, with a limit of six.')
            request = request.model_copy(update={'companies': names})
        _tasks[task_id] = {"task_id": task_id, "status": "pending", "progress": 0,
                           "current_step": "queued", "created_at": _now(), "updated_at": _now(),
                           "report_id": None, "error_code": None, "error_message": None}
        save_task(_tasks[task_id])
    if inputs:
        _executor.submit(_run, task_id, request, inputs)
    else:
        _executor.submit(_run, task_id, request)
    return {"task_id": task_id, "status": "pending"}


@app.get("/api/research/{task_id}", responses={200: {"model": ResearchTaskStatus}})
def get_research(task_id: str):
    with _lock:
        if task_id not in _tasks:
            raise HTTPException(404, "Task not found")
        return dict(_tasks[task_id])


@app.get("/api/reports/{report_id}", response_model=Report)
def get_report(report_id: str):
    if report_id not in _reports:
        raise HTTPException(404, "Report not found")
    raw = _restore_saved_investment_view(_reports[report_id])
    report = _read_saved_report(raw).model_dump(mode='json')
    if not report.get('providers'):
        if report['data_mode'] == 'live':
            report = dict(report, data_mode='unverified')
        if report.get('data_as_of') and report['data_mode'] != 'mock':
            cutoff = date.fromisoformat(report['data_as_of'])
            sources = [source for source in report['sources']
                       if source['source_type'] in {'sec_xbrl', 'esef_xbrl', 'sec_statement'}
                       and available_by(source.get('publication_date'), source.get('period_end'), cutoff)]
            source_ids = {source['source_id'] for source in sources}
            report = dict(report, sources=sources,
                          metrics=[item for item in report['metrics'] if set(item['evidence_ids']) <= source_ids],
                          claims=[item for item in report['claims'] if set(item['evidence_ids']) <= source_ids],
                          limitations=[*report['limitations'],
                                       'Legacy historical report: web leads excluded because their dates were not checked.'])
        legacy = Report.model_validate(report)
        names = {company.company_id: company.company_name for company in legacy.companies}
        claims = [qualify_claim(claim, company_name=names.get(claim.company_id)) for claim in legacy.claims]
        return legacy.model_copy(update={'claims': claims})
    return report


@app.exception_handler(RequestValidationError)
async def request_validation(request: Request, error: RequestValidationError):
    if request.method == 'PUT' and request.url.path.rstrip('/') == '/api/settings':
        return JSONResponse(status_code=422, content={'detail': [
            {'loc': ['body'], 'msg': 'Invalid settings request', 'type': 'value_error'}
        ]})
    return await request_validation_exception_handler(request, error)


@app.get("/api/settings", response_model=SettingsStatus, dependencies=[Depends(require_local_request)])
def get_settings():
    return settings_status()


@app.put("/api/settings", response_model=SettingsStatus, dependencies=[Depends(require_local_request)])
def update_settings(update: SettingsUpdate):
    return save_settings(update)


@app.post('/api/settings/connections', response_model=ConnectionDiagnostics, dependencies=[Depends(require_local_request)])
def test_connections():
    return connection_diagnostics()

# Serve the built client from the same origin as the API.
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_client_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _client_dist.joinpath("index.html").is_file():
    app.mount("/assets", StaticFiles(directory=_client_dist / "assets"), name="assets")

    @app.get("/{client_path:path}", include_in_schema=False)
    def client_entry(client_path: str):
        if client_path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        return FileResponse(_client_dist / "index.html")
