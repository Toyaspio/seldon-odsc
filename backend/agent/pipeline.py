import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from uuid import uuid4
from urllib.parse import urlsplit

from backend.agent.investment import analyze_investment, investment_requested
from backend.agent.limits import ResourceLimitReached, ResearchLimits
from backend.agent.models import Claim, Company, Report, ResearchQuality
from backend.agent.planning import DEFAULT_METRICS, ResearchPlan, RetrievalTask, requested_metrics
from backend.evidence.extractor import EvidenceExtractor, HKEX_DISCLOSURE_HOSTS
from backend.evidence.registry import EvidenceRegistry
from backend.evidence.verifier import EvidenceVerifier, qualify_claim
from backend.financial.capex import CAPEX_SCOPES
from backend.agent.news import news_queries, research_news
from backend.financial.valuation import available_valuation, valuation_is_core, valuation_objectives
from backend.financial.validator import FinancialValidator, period_kind
from backend.providers.sec_capex_filings import JPMORGAN
from backend.providers.interfaces import (ModelProvider, SearchProvider, FinancialDataProvider,
    MockModelProvider, MockSearchProvider, MockFinancialDataProvider)
from backend.providers.live import (InvalidResearchPlan, NebiusModelProvider, ProviderError,
                                    TavilyQuotaExceeded, TavilySearchProvider)
from backend.providers.esef import official_catalog, fetch_filing
from backend.providers.historical import publication_day, source_publication
from backend.providers.sec import ALIASES, SEEDS, SecCompanyResolver, SecProviderError, SecRequestTimedOut, SecFinancialDataProvider, available_by, normalized


class CompanyUnresolved(ValueError):
    pass


@dataclass
class ResearchPlanner:
    model: ModelProvider

    def plan(self, query: str, names: list[str], limits: ResearchLimits,
             as_of_date: str | None = None) -> ResearchPlan:
        context = query[:2000] + ('\nUser-selected companies: ' + ', '.join(
            name[:100] for name in names[:6]) if names else '')
        if as_of_date:
            context += f'\nHistorical evidence cutoff: {as_of_date}. Plan only sources public by that date.'
        with limits.provider_call('nebius', mock=isinstance(self.model, MockModelProvider), stage='planning'):
            if isinstance(self.model, NebiusModelProvider):
                output = self.model.plan(context, limits=limits)
            else:
                limits.claim_model()
                output = self.model.plan(context)
        limits.remaining()
        if isinstance(output, dict) and output.get('mode') == 'mock':
            output = dict(companies=names, core_question=query[:300],
                          metrics=['cash_capex', 'operating_cash_flow', 'free_cash_flow', 'revenue'],
                          preferred_source_types=['filing', 'investor_relations'],
                          retrieval_tasks=[{'query': query[:300], 'purpose': 'Locate official financial disclosures'}],
                          verification_questions=['Which facts are disclosed for the requested periods?'],
                          comparison='latest')
        try:
            plan = ResearchPlan.from_model(output, query)
            return plan.model_copy(update={'metrics': list(dict.fromkeys(plan.metrics + requested_metrics(query)))})
        except ValueError:
            raise InvalidResearchPlan('Model returned an invalid research plan') from None


@dataclass(frozen=True)
class PublicListing:
    company_id: str
    name: str
    ticker: str
    exchange: str
    official_host: str
    aliases: tuple[str, ...]

    @property
    def labels(self):
        return (self.name, self.ticker, *self.aliases)


class CompanyResolver:
    # Exchange-verified identities for issuers outside the structured SEC route.
    public_listings = (
        PublicListing('hkex:0020', 'SenseTime Group Inc.', '0020.HK', 'HKEX', 'sensetime.com',
                      ('SenseTime', 'SenseTime-W', 'HKEX:0020', 'HK.00020', '商汤科技', '商湯科技',
                       '商汤集团', '商湯集團', '商汤集团股份有限公司', '商湯集團股份有限公司',
                       'SenseTime (0020.HK)', 'SenseTime Group Inc. (0020.HK)')),
    )
    # These domains rank discovery results only. They do not confer numeric verification.
    issuer_domains = {'orcl': 'oracle.com', 'msft': 'microsoft.com', 'googl': 'abc.xyz',
                      'amzn': 'amazon.com', 'meta': 'meta.com', 'nvda': 'nvidia.com',
                      'baba': 'alibabagroup.com', 'tm': 'global.toyota', 'shel': 'shell.com',
                      **{issuer.company_id: issuer.official_host for issuer in public_listings}}

    def public_listing(self, name: str) -> PublicListing | None:
        key = normalized(name)
        return next((issuer for issuer in self.public_listings
                     if any(normalized(label) == key for label in issuer.labels)), None)

    def mentioned(self, query: str) -> list[str]:
        found = [alias for alias in ALIASES if not alias.isascii() and alias in query]
        for name, (_, ticker, _) in SEEDS.items():
            if re.search(rf'(?<!\w){re.escape(name)}(?!\w)', query, re.I) or re.search(
                    rf'(?<!\w){ticker}(?!\w)', query):
                found.append(name.capitalize() if name not in {'nvidia'} else 'NVIDIA')
        found.extend(match.group() for match in re.finditer(r'\bCIK[\s:]*[0-9]{1,10}\b', query, re.I))
        key = normalized(query)
        for issuer in self.public_listings:
            if any(re.search(rf'(?<!\w){re.escape(normalized(label))}(?!\w)', key)
                   if label.isascii() else label in query for label in issuer.labels):
                found.append(issuer.name)
        return found

    def resolve(self, names: list[str], directory: list[dict] | None = None,
                *, mock: bool = False, official: list[dict] | None = None) -> list[Company]:
        resolver = SecCompanyResolver()
        companies = {}
        for name in names:
            listing = self.public_listing(name) if not mock else None
            if listing:
                companies.setdefault(listing.company_id, Company(
                    company_id=listing.company_id, company_name=listing.name, ticker=listing.ticker,
                    exchange=listing.exchange, resolution_status='resolved'))
                continue
            matches = [] if mock else resolver.matches(name, directory)
            foreign = [issuer for issuer in official or [] if normalized(name) in {
                normalized(label) for label in [issuer['name'], issuer['ticker'], issuer['lei'],
                     f"{issuer['exchange']}:{issuer['ticker']}", *issuer['aliases']]}]
            if len(foreign) == 1:
                issuer, = foreign
                company = Company(company_id='lei:' + issuer['lei'], company_name=issuer['name'],
                                  ticker=issuer['ticker'], exchange=issuer['exchange'],
                                  country=issuer['country'], resolution_status='resolved')
            elif len(matches) == 1 and not foreign:
                record, = matches
                ticker = record['ticker']
                company = Company(company_id=ticker.lower(), company_name=record['name'], ticker=ticker,
                                  exchange=record.get('exchange'), cik=record['cik'], resolution_status='resolved')
            else:
                label = ALIASES.get(name, name)
                if mock:
                    label = next((title.split()[0].rstrip('.,') for title, ticker, _ in SEEDS.values()
                                  if ticker == label), label)
                company = Company(company_id=normalized(label).replace(' ', '-') or label.casefold(),
                                  company_name=label, resolution_status='ambiguous' if matches or foreign else 'unverified')
            companies.setdefault(company.company_id, company)
        return list(companies.values())


@dataclass
class ToolRouter:
    search: SearchProvider
    financial: FinancialDataProvider

    def search_once(self, query: str, limits: ResearchLimits) -> list[dict]:
        return self.search_round([query], limits)

    def search_round(self, queries: list[str], limits: ResearchLimits, issues: list[str] | None = None,
                     *, as_of_date: str | None = None, stages: dict[str, str] | None = None, topics: dict[str, str] | None = None) -> list[dict]:
        limits.claim_search(len(queries))
        results = []
        for query in queries:
            try:
                timeout = limits.remaining()
                with limits.provider_call('tavily', mock=isinstance(self.search, MockSearchProvider),
                                          stage=(stages or {}).get(query, 'initial_research'), query=query,
                                          topic=(topics or {}).get(query)) as event:
                    is_news = (stages or {}).get(query, '').startswith('news_')
                    if is_news and isinstance(self.search, TavilySearchProvider):
                        batch = self.search.search(query, timeout, as_of_date=as_of_date, news=True)
                    elif as_of_date and isinstance(self.search, TavilySearchProvider):
                        batch = self.search.search(query, timeout, as_of_date=as_of_date)
                    else:
                        batch = self.search.search(query, timeout)
                    if event is not None and event.http_responses:
                        event.usable_results = len(batch)
                        if is_news:
                            limits.news_results.extend(batch)
                        limits.search_results.append((event, {row['url'] for row in batch if isinstance(row.get('url'), str)}))
                limits.remaining()
                results.extend(batch)
            except TavilyQuotaExceeded:
                if issues is None:
                    raise
                issues.append('Tavily usage limit reached. Search evidence may be incomplete.')
                break
            except (ProviderError, TimeoutError, OSError, ResourceLimitReached) as exc:
                if issues is None:
                    raise
                issues.append(f'Search unavailable ({type(exc).__name__}).')
                if isinstance(exc, ResourceLimitReached):
                    break
        return results

    def directory(self, provider, limits: ResearchLimits):
        lookup = getattr(provider, 'directory', None)
        if lookup is None:
            return None
        timeout = limits.remaining()
        with limits.provider_call('sec', stage='issuer_directory'):
            return lookup(timeout)

    def facts(self, provider, company: Company, cutoff: str | None, limits: ResearchLimits, *, valuation=False) -> list[dict]:
        def fetch(stage):
            timeout = limits.remaining()
            with limits.provider_call('sec', mock=isinstance(provider, MockFinancialDataProvider),
                                      stage=stage, company_id=company.company_id):
                if valuation and isinstance(provider, SecFinancialDataProvider):
                    return provider.facts(company.cik, cutoff, timeout_seconds=timeout,
                                          include_valuation=True)
                if hasattr(provider, 'directory'):
                    return provider.facts(company.cik, cutoff, timeout_seconds=timeout)
                return provider.facts(company.ticker, cutoff)

        try:
            rows = fetch('company_facts')
        except SecRequestTimedOut:
            rows = fetch('company_facts_retry')
        limits.remaining()
        return rows

    def official_facts(self, issuer: dict, cutoff: str | None, limits: ResearchLimits) -> list[dict]:
        upper = cutoff or datetime.now(timezone.utc).date().isoformat()
        filings = sorted((item for item in issuer['filings'] if item['publication_date'] <= upper),
                         key=lambda item: item['publication_date'], reverse=True)
        rows = []
        for filing in filings[:2]:
            timeout = limits.remaining()
            with limits.provider_call('esef'):
                rows.extend(fetch_filing(issuer, filing, upper, timeout))
            limits.remaining()
        return rows

    def follow_up_official(self, issuer: dict, cutoff: str | None, limits: ResearchLimits,
                           results: list[dict]) -> list[dict]:
        upper = cutoff or datetime.now(timezone.utc).date().isoformat()
        filings = sorted((item for item in issuer['filings'] if item['publication_date'] <= upper),
                         key=lambda item: item['publication_date'], reverse=True)
        discovered = {result.get('url') for result in results if isinstance(result.get('url'), str)}
        match = next((filing for filing in filings[2:] if filing['url'] in discovered), None)
        if match is None:
            return []
        timeout = limits.remaining()
        with limits.provider_call('esef'):
            rows = fetch_filing(issuer, match, upper, timeout)
        limits.remaining()
        return rows


def historical_leads(results: list[dict], cutoff: date, hosts: set[str],
                     limits: ResearchLimits, checked: dict, issues: list[str]) -> list[dict]:
    leads = []
    for item in results:
        index_date = publication_day(item.get('published_date'))
        url = item.get('url')
        if not index_date or index_date > cutoff or not isinstance(url, str) or len(url) > 2048:
            continue
        if url not in checked:
            try:
                timeout = limits.remaining()
            except ResourceLimitReached:
                break
            checked[url] = source_publication(url, hosts, cutoff, timeout)
        if checked[url] == index_date:
            # Today's search snippet/title can include later edits. Keep only dated links.
            leads.append({'url': url, 'published_date': index_date.isoformat()})
    if len(leads) != len(results):
        issues.append('Some historical web leads lacked matching, in-range publisher dates and were omitted.')
    return leads


def structured_as_of(rows: list[dict], cutoff: str | None) -> list[dict]:
    if not cutoff:
        return rows
    day = date.fromisoformat(cutoff)
    return [row for row in rows if available_by(row.get('filed_at'), row.get('period_end'), day)]


class ReportBuilder:
    def build(self, **fields) -> Report:
        fields['claims'] = [qualify_claim(claim) for claim in fields['claims']]
        return Report(report_id=str(uuid4()), task_id='', generated_at=datetime.now(timezone.utc), **fields)


def evidence_gaps(companies: list[Company], plan: ResearchPlan, metrics: list) -> list[tuple[Company, str]]:
    gaps = []
    expected = 'year' if plan.comparison == 'annual' else 'quarter'
    for company in companies:
        own = [record for record in metrics if record.company_id == company.company_id]
        reference_periods = [record.period_end for record in own
                             if record.metric_name in {'revenue', 'operating_cash_flow', 'cash_capex'}
                             and period_kind(record) == expected]
        for metric in plan.metrics:
            available = [record for record in own if record.metric_name == metric]
            if not available or (reference_periods and max(reference_periods) >
                                 max(item.period_end for item in available)):
                gaps.append((company, metric))
                continue
            if plan.comparison != 'latest' and metric in {'cash_capex', 'operating_cash_flow', 'free_cash_flow', 'revenue'}:
                growth = f'{metric}_{plan.comparison}_growth'
                periods = [record.period_end for record in available if period_kind(record) == expected]
                if not periods or not any(record.metric_name == growth and record.period_end == max(periods)
                                          for record in own):
                    gaps.append((company, growth))
    return gaps


def scoped_research_quality(query, companies, metrics, claims, gaps, valuations):
    explicit = set(requested_metrics(query))
    relevant = explicit or {'revenue', 'cash_capex', 'free_cash_flow', 'operating_cash_flow'}
    core, supplemental = set(), set()
    history = set()
    years = set(re.findall(r'(?<![0-9])(?:19|20)[0-9]{2}(?![0-9])', query))
    latest = {}
    for row in metrics:
        if row.actual_or_guidance == 'actual' and row.metric_name in relevant:
            key = (row.company_id, row.metric_name)
            latest[key] = max(latest.get(key, ''), row.period_end)
    for company in companies:
        if company.resolution_status != 'resolved':
            core.add(f'{company.company_name}: the issuer could not be resolved.')
        elif not any(row.company_id == company.company_id and row.actual_or_guidance == 'actual'
                     for row in metrics):
            core.add(f'{company.company_name}: no official financial observations passed verification.')
    for company, name in gaps:
        base = re.sub(r'_(?:annual|qoq|yoy)_growth$', '', name)
        output = core if base in explicit else supplemental
        output.add(f'{company.company_name}: {name.replace("_", " ")} lacks comparable verified data.')
    for claim in claims:
        if claim.verification_status != 'contradicted' or not claim.period_end:
            continue
        identity = (claim.company_id, claim.metric_name, claim.period_start, claim.period_end,
                    claim.unit, claim.accounting_basis, claim.capex_scope)
        current = claim.period_end >= latest.get((claim.company_id, claim.metric_name), '')
        material = claim.metric_name in relevant
        if material and (claim.period_end[:4] in years if years else current):
            name = next((company.company_name for company in companies if company.company_id == claim.company_id),
                        claim.company_id)
            core.add(f'{name}: conflicting {claim.metric_name.replace("_", " ")} for {claim.period_end}.')
        else:
            history.add(identity)
    if valuation_is_core(query):
        requested = valuation_objectives(query)
        by_issuer = {valuation.company_id: valuation for valuation in valuations}
        for company in companies:
            measured = available_valuation(by_issuer.get(company.company_id), query)
            missing = requested - measured
            if missing:
                core.add(f'{company.company_name}: unsupported requested valuation: {", ".join(sorted(missing))}.')
            elif not requested and not (measured - {'Market capitalization', 'Closing price'}):
                core.add(f'{company.company_name}: available data do not support a current valuation measure.')
    return ResearchQuality(core_gaps=sorted(core), supplemental_gaps=sorted(supplemental),
                           historical_conflicts=len(history))


def selected_web_claims(selections: list[dict], leads: dict) -> list[Claim]:
    claims = {}
    for item in selections:
        if not isinstance(item, dict):
            continue
        source_id, quote, topic = item.get('source_id'), item.get('quote'), item.get('topic')
        if not isinstance(source_id, str) or not isinstance(quote, str):
            continue
        if topic not in ('commercialization', 'risk') or not 25 <= len(quote) <= 380:
            continue
        source = leads.get(source_id)
        if source is None or quote not in (source.quoted_text or '')[:480]:
            continue
        claim_id = f'analysis:{topic}:{source_id}'
        claims[claim_id] = Claim(
            claim_id=claim_id, text=f'{topic.capitalize()} lead ({source.publisher}): “{quote}”',
            claim_type='analysis', verification_status='insufficient', evidence_ids=[source_id],
            company_id=source.company_id,
            limitations=['Model-selected excerpt from a search response. Verify the underlying publication '
                         'before treating it as a fact, company statement, or third-party opinion.'],
        )
    return list(claims.values())


@dataclass
class ResearchOrchestrator:
    planner: ResearchPlanner
    resolver: CompanyResolver
    router: ToolRouter
    extractor: EvidenceExtractor
    validator: FinancialValidator
    verifier: EvidenceVerifier
    builder: ReportBuilder

    @staticmethod
    def issuer_name_in_question(label, question):
        key = normalized(label)
        if not key:
            return False
        if re.search(rf'(?<!\w){re.escape(key)}(?!\w)', question):
            return True
        parts = key.split()
        if len(parts) < 3 or len(parts[0]) < 5:
            return False
        prefix, rest = parts[0], ' '.join(parts[1:])
        for candidate in re.findall(rf'\b(\w{{5,}}) {re.escape(rest)}\b', question):
            # Match one insertion, deletion or substitution in the distinctive first word.
            short, long = sorted((candidate, prefix), key=len)
            if len(short) == len(long):
                if sum(a != b for a, b in zip(short, long)) == 1:
                    return True
            elif len(long) == len(short) + 1:
                mismatch = next((i for i, (a, b) in enumerate(zip(short, long)) if a != b), len(short))
                if short == long[:mismatch] + long[mismatch + 1:]:
                    return True
        return False

    def issuer_from_search(self, query, results, directory, provider, limits, planned_companies=()):
        if not isinstance(provider, SecFinancialDataProvider):
            return None
        ciks = {}
        for row in results:
            try:
                url = urlsplit(row.get('url') or '')
            except (TypeError, ValueError):
                continue
            if url.scheme != 'https' or url.hostname not in {'www.sec.gov', 'sec.gov'} or url.username:
                continue
            accession = re.match(r'/Archives/edgar/data/(\d{1,10})/', url.path, re.I)
            if accession:
                ciks[accession[1].zfill(10)] = None
        planned_ciks = {hits[0]['cik'] for name in planned_companies
                        if len(hits := SecCompanyResolver().matches(name, directory)) == 1}
        matched = []
        normalized_query = normalized(query)
        # Search results only nominate CIKs. Official SEC metadata must ground the identity.
        candidate_order = sorted(ciks, key=lambda cik: cik not in planned_ciks)
        for cik in candidate_order[:2]:
            if not any(entry['cik'] == cik for entry in directory or []):
                continue
            try:
                with limits.provider_call('sec', stage='issuer_identity'):
                    labels = provider.issuer_labels(cik, min(8, limits.remaining()))
            except (SecProviderError, ResourceLimitReached):
                continue
            if labels and (cik in planned_ciks or any(
                    self.issuer_name_in_question(label, normalized_query) for label in labels)):
                matched.append(cik)
        if len(matched) != 1:
            return None
        company, = self.resolver.resolve(matched, directory)
        return company if company.resolution_status == 'resolved' else None

    def run_mock(self, query: str, companies: list[str]) -> dict:
        limits = ResearchLimits()
        names = companies or self.resolver.mentioned(query)
        if not names:
            raise CompanyUnresolved
        return self.builder.build(query=query, companies=self.resolver.resolve(names, mock=True),
                                  data_mode='mock', providers=limits.provider_snapshot(), sources=[], metrics=[], claims=[], status='partial',
                                  limitations=['Development mock only. No live data was retrieved.']).model_dump()

    def run_sec(self, query: str, company_names: list[str], as_of_date: str | None = None,
                data_provider=None) -> Report:
        provider = data_provider or self.router.financial
        limits = ResearchLimits()
        limits.providers['sec'].enabled = isinstance(provider, SecFinancialDataProvider)
        companies = self.resolver.resolve(company_names, self.router.directory(provider, limits))
        metrics, sources = [], []
        for company in companies:
            if company.cik:
                rows = self.router.facts(provider, company, as_of_date, limits)
                extracted, evidence = self.extractor.structured(company, structured_as_of(rows, as_of_date))
                metrics.extend(extracted)
                sources.extend(evidence)
        return self.builder.build(query=query, data_as_of=as_of_date,
                                  data_mode='mixed' if limits.providers['sec'].calls else 'unverified',
                                  providers=limits.provider_snapshot(), companies=companies, metrics=metrics, sources=sources, claims=[],
                                  limitations=['Structured disclosures only.'], status='partial')

    def run_agent(self, query: str, company_names: list[str], as_of_date: str | None = None,
                  data_provider=None, progress=lambda step: None,
                  limits: ResearchLimits | None = None, input_reports: tuple[Report, ...] = (), on_draft=None, on_news_checkpoint=None) -> Report:
        limits = limits or ResearchLimits()
        provider = data_provider or self.router.financial
        limits.providers['nebius'].enabled = isinstance(self.planner.model, NebiusModelProvider)
        limits.providers['tavily'].enabled = isinstance(self.router.search, TavilySearchProvider)
        limits.providers['sec'].enabled = isinstance(provider, SecFinancialDataProvider)
        limits.providers['esef'].enabled = bool(os.getenv('SELDON_OFFICIAL_ISSUER_CATALOG'))
        issues = []
        planning_unavailable = False
        progress('planning')
        try:
            plan = self.planner.plan(query, company_names, limits, as_of_date)
        except ProviderError as error:
            limits.remaining()
            identified = (company_names or self.resolver.mentioned(query))[:6]
            queries = [f'{name[:100]} financial results and earnings outlook' for name in identified]
            plan = ResearchPlan(
                companies=identified, core_question=query.strip()[:300] or 'Research the requested topic',
                metrics=requested_metrics(query) or list(DEFAULT_METRICS),
                retrieval_tasks=[RetrievalTask(query=text) for text in
                                 (queries or [query.strip()[:300] or 'Official company information'])],
            )
            planning_unavailable = True
            reason = ('Research planning was incomplete.' if isinstance(error, InvalidResearchPlan)
                      else error.public_message or 'Research planning was incomplete.')
            issues.append(f'{reason} Research continued with the original question and official sources.')
        progress('resolving_companies')
        names = company_names or plan.companies or self.resolver.mentioned(query)
        if not company_names:
            mentioned = self.resolver.mentioned(query)
            normalized_query = normalized(query)
            candidates = []
            for name in names:
                words = normalized(name).split()
                for end in range(len(words), 0, -1):
                    label = ' '.join(words[:end])
                    if not re.search(rf'(?<!\w){re.escape(label)}(?!\w)', normalized_query):
                        continue
                    if end == len(words):
                        candidates.append(name)
                    elif len(label) >= 4:
                        # Resolve what the user actually typed, rather than trusting the model's expansion.
                        candidates.append(label)
                    break
            names = list(dict.fromkeys([*mentioned, *candidates]))
        directory = []
        if not names or any(self.resolver.public_listing(name) is None for name in names):
            try:
                directory = self.router.directory(provider, limits)
            except (SecProviderError, ResourceLimitReached):
                issues.append('The SEC issuer directory was unavailable.')
        try:
            official = official_catalog()
        except SecProviderError:
            official = []
            issues.append('The configured official issuer catalog was invalid.')
        if not company_names:
            # A model may return only a ticker. Ground it using the official name,
            # then resolve the wording in the question so ambiguity is preserved.
            for planned_name in plan.companies:
                for record in SecCompanyResolver().matches(planned_name, directory):
                    words = normalized(record['name']).split()
                    for end in range(len(words), 0, -1):
                        label = ' '.join(words[:end])
                        if len(label) >= 4 and re.search(
                                rf'(?<!\w){re.escape(label)}(?!\w)', normalized_query):
                            names.append(label)
                            break
        if not names and directory:
            # The planner can omit the company. Try capitalized names from the
            # question against EDGAR before dropping to general web research.
            resolver = SecCompanyResolver()
            for token in re.finditer(r'\b[A-Z][A-Za-z0-9]{1,}\b', query):
                label = token.group()
                matches = resolver.matches(label, directory)
                if any((label.isupper() and record['ticker'].upper() == label)
                       or (len(label) >= 4 and normalized(record['name']).split()[0] == label.casefold())
                       for record in matches):
                    names.append(label)
        if not names:
            issues.append('The issuer could not be identified from the question. Research continued with public sources.')
            plan = plan.model_copy(update={
                'retrieval_tasks': [RetrievalTask(query=query[:300], purpose='Find sources for the original question')]
            })
        names = list(dict.fromkeys(names))
        if len(names) > 6:
            issues.append('Research is limited to six issuers per task. Additional selections were omitted.')
        companies = self.resolver.resolve(names[:6], directory, official=official)
        company_labels = {company.company_id: company.company_name for company in companies}
        official_by_id = {'lei:' + entry['lei']: entry for entry in official}
        domains = self.resolver.issuer_domains | {key: issuer['official_host']
                                                 for key, issuer in official_by_id.items()}
        hosts = {'sec.gov', 'www.sec.gov', 'data.sec.gov'}
        if any(company.exchange == 'HKEX' for company in companies):
            hosts.update(HKEX_DISCLOSURE_HOSTS)
        for key in company_labels.keys() & self.resolver.issuer_domains.keys():
            host = self.resolver.issuer_domains[key]
            hosts.update((host, 'www.' + host))
        publisher_hosts = hosts.copy()
        hosts.update(issuer['official_host'] for issuer in official_by_id.values())
        historical_day = date.fromisoformat(as_of_date) if as_of_date else None
        checked_dates = {}
        wants_investment = bool(input_reports) or plan.investment_intent or investment_requested(query)
        if 'nvda' in company_labels:
            publisher_hosts.update(('nvidianews.nvidia.com', 'investor.nvidia.com'))
        discovery = (news_queries(companies, query)
                     if isinstance(self.router.search, TavilySearchProvider) and not historical_day else {})

        progress('retrieving')
        raw_results = []
        # Keep one query and one round available for a concrete evidence gap.
        initial_cap = min(6, max(1, limits.max_search_queries - int(limits.max_search_rounds > 1)))
        preferred = ' official filing' if any(source in plan.preferred_source_types
                                               for source in ('filing', 'official_regulator')) else ' investor relations'
        query_topics = {task.query[:275] + preferred: task.purpose for task in plan.retrieval_tasks}
        queries = list(query_topics)[:initial_cap]
        needs_identity = (not company_names and isinstance(provider, SecFinancialDataProvider)
                          and not any(company.resolution_status == 'resolved' for company in companies))
        if needs_identity:
            # Keep this search independent of the model's suggested legal name.
            original_search = query.strip()[:275] + ' SEC issuer filing'
            queries = [original_search]
            query_topics[original_search] = 'Identify the official company named in the question'
        if input_reports and company_labels:
            issuer_names = list(company_labels.values())
            relationship = ' '.join(issuer_names[:4]) + ' competition partnerships markets'
            per_issuer = [name + ' latest results outlook competitive position' for name in issuer_names]
            comparison_cap = min(len(per_issuer) + 1, max(1, limits.max_search_queries - 1))
            queries = [relationship[:300]] + per_issuer[:comparison_cap - 1]
            query_topics = dict.fromkeys(per_issuer, 'Latest results and competitive position')
            query_topics[queries[0]] = 'Shared markets and competitive relationships'
        stages = {queries[0]: 'joint_research'} if input_reports else {}
        if discovery:
            first_news = list(discovery)[:max(1, initial_cap // 2)]
            primary = queries[:max(0, initial_cap - len(first_news))]
            queries = primary + first_news
            stages.update(dict.fromkeys(first_news, 'news_discovery'))
            query_topics.update(dict.fromkeys(first_news, 'Recent issuer and industry developments'))
        try:
            raw_results = self.router.search_round(queries, limits, issues, as_of_date=as_of_date, stages=stages, topics=query_topics)
            if historical_day:
                raw_results = historical_leads(raw_results, historical_day, hosts, limits, checked_dates, issues)
        except ResourceLimitReached:
            issues.append('Initial search skipped due to a research limit.')
        if needs_identity:
            recovered = self.issuer_from_search(query, raw_results, directory, provider, limits, plan.companies)
            if recovered:
                companies = [recovered]
                company_labels = {recovered.company_id: recovered.company_name}
        facts, sources = [], []
        for company in companies:
            if company.resolution_status != 'resolved':
                continue
            issuer = official_by_id.get(company.company_id)
            if not issuer and not company.cik:
                issues.append(f'{company.company_name}: no structured financial feed is configured for '
                              f'{company.exchange or "this exchange"}. The report uses available public sources.')
                continue
            try:
                rows = (self.router.official_facts(issuer, as_of_date, limits) if issuer else
                        self.router.facts(provider, company, as_of_date, limits, valuation=wants_investment))
            except (SecProviderError, ResourceLimitReached, TimeoutError, OSError):
                issues.append(f'Official structured data unavailable for {company.company_name}.')
                continue
            extracted, evidence = self.extractor.structured(company, structured_as_of(rows, as_of_date))
            facts.extend(extracted)
            sources.extend(evidence)

        progress('checking_evidence')
        audited, claims = self.verifier.audit_facts(facts, sources, company_labels)
        calculated = self.validator.calculate(audited, plan.comparison, claims=claims)
        metrics = audited + calculated
        gaps = evidence_gaps(companies, plan, metrics)
        model = self.planner.model
        investment_queries = []
        if (not historical_day and isinstance(model, NebiusModelProvider) and wants_investment
                and limits.rounds_used < limits.max_search_rounds
                and limits.queries_used < limits.max_search_queries):
            try:
                with limits.provider_call('nebius', stage='investment_follow_up_planning'):
                    investment_queries = model.investment_follow_up(plan, sorted(company_labels.values()), [
                        {'metric': metric.metric_name, 'issuer': company_labels[metric.company_id],
                         'value': metric.value, 'period_end': metric.period_end, 'unit': metric.unit}
                        for metric in sorted(metrics, key=lambda entry: entry.period_end, reverse=True)[:12]
                    ], limits)
            except (ProviderError, ResourceLimitReached):
                issues.append('Model-directed investment follow-up was unavailable.')
        remaining_news = [search for search in discovery if search not in queries]
        corroboration = []
        if limits.news_results and len(companies) == 1 and discovery:
            name = companies[0].company_name[:95]
            corroboration = [f'{name} recent company announcements independent reporting competitors risks outlook']
        if gaps or investment_queries or remaining_news or corroboration:
            company = gaps[0][0] if gaps else None
            cutoff = f' published by {as_of_date}' if as_of_date else ''
            initial_keys = {query.casefold() for query in queries}
            gap_queries = [
                f'{gap_company.company_name} {gap_metric.replace("_", " ")} official filing investor relations{cutoff}'
                for gap_company, gap_metric in gaps]
            explicit = set(requested_metrics(query))
            core_queries = [search for search, (_, metric) in zip(gap_queries, gaps)
                            if re.sub(r'_(?:annual|qoq|yoy)_growth$', '', metric) in explicit]
            follow_ups = list(dict.fromkeys(core_queries + remaining_news + investment_queries +
                                            corroboration + gap_queries))
            follow_ups = [query for query in follow_ups if query.casefold() not in initial_keys]
            follow_ups = follow_ups[:min(6, limits.max_search_queries - limits.queries_used)]
            if not follow_ups:
                issues.append('Targeted follow-up skipped because no search queries remain.')
            else:
                progress('follow_up')
            try:
                stages = {query: ('news_discovery' if query in discovery else
                                  'news_corroboration' if query in corroboration else
                                  'investment_counterevidence' if query in investment_queries else 'financial_gap')
                          for query in follow_ups}
                follow_up_results = (self.router.search_round(follow_ups, limits, issues, as_of_date=as_of_date,
                                                             stages=stages,
                                                             topics={q: ('Recent issuer and industry developments'
                                                                         if q in discovery else
                                                                         'Independent reporting and counterevidence' if q in corroboration else
                                                                         'Test the investment thesis' if q in investment_queries
                                                                         else 'Resolve a financial evidence gap')
                                                                     for q in follow_ups})
                                     if follow_ups else [])
                if historical_day:
                    follow_up_results = historical_leads(
                        follow_up_results, historical_day, hosts, limits, checked_dates, issues)
                raw_results.extend(follow_up_results)
                issuer = official_by_id.get(company.company_id) if company else None
                if issuer:
                    rows = self.router.follow_up_official(issuer, as_of_date, limits, follow_up_results)
                    if rows:
                        extracted, evidence = self.extractor.structured(company, structured_as_of(rows, as_of_date))
                        source_index = {source.source_id: source for source in sources + evidence}
                        fact_index = {fact.metric_id: fact for fact in facts}
                        for fact in extracted:
                            old = fact_index.get(fact.metric_id)
                            if old:
                                fact = fact.model_copy(update={'evidence_ids': list(dict.fromkeys(
                                    old.evidence_ids + fact.evidence_ids))})
                            fact_index[fact.metric_id] = fact
                        sources = list(source_index.values())
                        audited, claims = self.verifier.audit_facts(list(fact_index.values()), sources, company_labels)
                        calculated = self.validator.calculate(audited, plan.comparison, claims=claims)
                        metrics = audited + calculated
                        gaps = evidence_gaps(companies, plan, metrics)
            except ResourceLimitReached:
                issues.append('Targeted follow-up skipped due to a research limit.')
            except SecProviderError:
                issues.append('A targeted official follow-up document was unavailable.')

        web_sources = self.extractor.web(raw_results, companies, domains)
        news_sources = self.extractor.web(limits.news_results, companies, domains)
        if news_sources:
            by_url = {source.url: source for source in web_sources}
            by_url.update({source.url: source for source in news_sources})
            web_sources = list(by_url.values())
        model = self.planner.model
        if not historical_day and isinstance(model, NebiusModelProvider) and web_sources:
            leads = {source.source_id: source for source in web_sources if source.quoted_text}
            leads = dict(list(leads.items())[:12])
            if leads:
                model_context = [{'source_id': source.source_id, 'title': source.title[:120],
                                  'publisher': source.publisher, 'excerpt': source.quoted_text[:480]}
                                 for source in leads.values()]
                try:
                    with limits.provider_call('nebius', stage='evidence_selection'):
                        selections = model.select_evidence(query, model_context, limits)
                    claims.extend(selected_web_claims(selections, leads))
                except (ProviderError, ResourceLimitReached):
                    issues.append('Model-assisted selection of commercialization and risk evidence was unavailable.')
        for entry in calculated:
            period = f'{entry.period_start} to {entry.period_end}'
            claims.append(Claim(
                claim_id=entry.metric_id, text=(f'{company_labels[entry.company_id]}: '
                    f'{entry.metric_name.replace("_", " ")} = {entry.value:.12g} {entry.unit} ({period}).'),
                verification_status='supported', evidence_ids=entry.evidence_ids,
                company_id=entry.company_id, metric_name=entry.metric_name, numeric_value=entry.value,
                unit=entry.unit, period_start=entry.period_start, period_end=entry.period_end,
                accounting_basis=entry.accounting_basis, actual_or_guidance='actual', claim_type='calculation',
                capex_scope=entry.capex_scope,
                limitations=['Calculated in Python from the cited disclosed inputs.'] +
                            ([CAPEX_SCOPES[entry.capex_scope]] if entry.capex_scope else []),
            ))
        for company in companies:
            fcf = [metric for metric in calculated if metric.company_id == company.company_id
                   and metric.metric_name == 'free_cash_flow' and period_kind(metric) in {'quarter', 'year'}]
            if 'free_cash_flow' in plan.metrics and fcf:
                latest = max(fcf, key=lambda item: (item.period_end, item.period_start or ''))
                if latest.value < 0:
                    claims.append(Claim(claim_id=f'analysis:cash-pressure:{company.company_id}',
                        claim_type='analysis', company_id=company.company_id, verification_status='supported',
                        text=(f'{company.company_name}: cash capital expenditure exceeded operating cash flow '
                              f'from {latest.period_start} to {latest.period_end} under the stated FCF definition.'),
                        evidence_ids=latest.evidence_ids,
                        limitations=['This comparison does not attribute the spending to AI.']))
        if len(companies) == 2:
            left, right = companies
            keys = ('currency', 'unit', 'period_start', 'period_end', 'accounting_basis',
                    'actual_or_guidance', 'capex_scope')
            expected = {'annual': {'year'}, 'qoq': {'quarter'}, 'yoy': {'quarter'}}.get(
                plan.comparison, {'quarter', 'year'})
            for name in plan.metrics:
                left_latest = sorted((m for m in metrics if m.company_id == left.company_id and
                                      m.metric_name == name and period_kind(m) in expected),
                                     key=lambda m: (m.period_end, m.period_start), reverse=True)
                right_latest = sorted((m for m in metrics if m.company_id == right.company_id and
                                       m.metric_name == name and period_kind(m) in expected),
                                      key=lambda m: (m.period_end, m.period_start), reverse=True)
                if not (left_latest and right_latest):
                    continue
                first, second = left_latest[0], right_latest[0]
                context = period_kind(first)
                if (context not in {'quarter', 'year'} or
                        not all(getattr(first, key) == getattr(second, key) for key in keys)):
                    issues.append(f'{name.replace("_", " ")}: latest issuer periods or accounting measures are not directly comparable.')
                    continue
                relation = 'higher than' if first.value > second.value else 'lower than' if first.value < second.value else 'equal to'
                claims.append(Claim(claim_id=f'analysis:comparison:{name}', claim_type='analysis',
                    text=(f'{left.company_name} had {name.replace("_", " ")} {relation} {right.company_name} '
                          f'from {first.period_start} to {first.period_end}.'),
                    verification_status='supported',
                    evidence_ids=list(dict.fromkeys(first.evidence_ids + second.evidence_ids))))
        for company, metric in dict.fromkeys((company.company_id, metric) for company, metric in gaps):
            claims.append(Claim(
                claim_id=f'gap:{company}:{metric}',
                text=f'{company_labels[company]}: insufficient verified evidence for {metric.replace("_", " ")}.',
                verification_status='insufficient', evidence_ids=[], company_id=company,
                metric_name=metric, claim_type='evidence_gap'))
        if any(metric in {'ai_capex', 'ai_revenue', 'ai_roi'} for metric in plan.metrics):
            claims.append(Claim(claim_id='analysis:ai-attribution', claim_type='analysis',
                text='The verified company-wide financial figures do not establish how much was spent on AI, '
                     'nor the revenue or return attributable to that investment.',
                verification_status='insufficient', evidence_ids=[],
                limitations=['AI-specific attribution requires a separately disclosed measure.']))
        archived_sources = {source.source_id: source for report in input_reports for source in report.sources}
        archived_sources.update((source.source_id, source) for source in [*sources, *web_sources])
        registry = EvidenceRegistry(archived_sources.values(), metrics)
        def preserve_report(stage, snapshot):
            if on_news_checkpoint is None:
                return
            recovered = self.builder.build(
                query=query, data_as_of=as_of_date, data_mode='mixed', status='partial',
                companies=companies, metrics=metrics, claims=claims, sources=registry.sources(),
                providers=limits.provider_snapshot(), provider_events=limits.events,
                news_analysis=snapshot.get('analysis'),
                input_report_ids=[item.report_id for item in input_reports],
                limitations=['Research was interrupted. Completed findings are preserved.'],
            )
            on_news_checkpoint(stage, {**snapshot, 'report': recovered.model_dump(mode='json')})

        try:
            preserve_report('financial_completed', {})
        except (OSError, sqlite3.Error):
            issues.append('Completed financial evidence could not be checkpointed.')
        def investment_answer(news=None):
            return analyze_investment(query, plan, metrics, sources, web_sources,
                                      model, limits, progress, issues, historical_day, input_reports,
                                      company_labels, on_draft=on_draft, news=news, registry=registry)

        # In a comparison, the user's answer takes precedence over the news article.
        investment = investment_answer() if input_reports else None
        news = research_news(query, company_labels, news_sources, metrics, model, limits,
                             historical_day or datetime.now(timezone.utc).date(), publisher_hosts,
                             registry=registry,
                             reserve_model_calls=int(not input_reports and isinstance(model, NebiusModelProvider)),
                             checkpoint=preserve_report if on_news_checkpoint else None)
        if not input_reports:
            investment = investment_answer(news)
        try:
            limits.remaining()
        except ResourceLimitReached:
            issues.append('Research deadline reached; unfinished work was omitted.')
        if historical_day:
            issues.append('Historical web leads include dated links only. Search excerpts may have changed since publication.')
        for company in companies:
            own_capex = [entry for entry in metrics if entry.company_id == company.company_id
                         and entry.metric_name == 'cash_capex']
            scopes = {entry.capex_scope for entry in own_capex if entry.capex_scope}
            if len(scopes) > 1:
                issues.append(f'{company.company_name}: Cash CapEx includes distinct asset perimeters; '
                              'compare trends within the same perimeter.')
            if company.cik == JPMORGAN and not own_capex:
                issues.append(f'{company.company_name}: cash CapEx and industrial-style FCF are N/A in this report. '
                              'No comparable consolidated cash purchase total was verified.')
        limitations = list(dict.fromkeys(issues + [
            'Search snippets are discovery leads. They do not establish financial figures.',
            'Company-wide CapEx, cloud revenue and RPO do not establish AI-specific spending, revenue or returns.',
            'Growth rates require comparable periods and a positive prior value. Free cash flow is operating cash flow less cash CapEx.',
        ]))
        quality = scoped_research_quality(query, companies, metrics, claims, gaps, [])
        if planning_unavailable and not limits.providers['nebius'].succeeded:
            quality.core_gaps.append('The model remained unavailable. Verified financial evidence is available when retrieved.')
        if not raw_results and any(issue.startswith(('Search unavailable', 'Initial search skipped'))
                                   for issue in issues):
            quality.core_gaps.append('External evidence retrieval failed for this question.')
        if not companies:
            quality.core_gaps.append('The issuer is unresolved. Company-specific financial metrics are unavailable.')
        if wants_investment and investment and not investment.points and investment.missing_inputs and any(
                event.stage == 'investment_synthesis' and event.outcome == 'failed' for event in limits.events):
            quality.core_gaps.append('Investment generation was interrupted. Completed findings are available.')
        status = 'completed' if metrics and not quality.core_gaps else 'partial'
        progress('building_report')
        sec_ids = {source.source_id for source in sources if source.source_type in {'sec_xbrl', 'sec_statement'}}
        has_audited_sec = any(sec_ids.intersection(metric.evidence_ids) for metric in audited)
        core_succeeded = all(limits.providers[name].succeeded for name in ('nebius', 'tavily', 'sec'))
        if core_succeeded and web_sources and has_audited_sec:
            data_mode = 'live'
        elif any(item.calls or item.enabled for item in limits.providers.values()):
            data_mode = 'mixed'
        else:
            data_mode = 'unverified' if sources else 'mock'
        adopted = {item for point in investment.points for item in point.evidence_ids} if investment else set()
        adopted.update(item for claim in claims if claim.claim_id.startswith('analysis:')
                       for item in claim.evidence_ids)
        adopted.update(identifier for item in news.insights for identifier in item.evidence_ids)
        adopted_urls = {source.url: source.source_id for source in web_sources if source.source_id in adopted}
        limits.attribute_search_citations(adopted_urls)
        for event in limits.events:
            if event.stage in {'company_facts', 'company_facts_retry'} and event.parsed:
                event.extracted = sum(row.company_id == event.company_id for row in facts)
                event.verified = sum(row.company_id == event.company_id for row in audited)
        return self.builder.build(query=query, data_as_of=as_of_date, data_mode=data_mode,
                                  provider_events=limits.events,
                                  providers=limits.provider_snapshot(), companies=companies,
                                  metrics=metrics, claims=claims, sources=registry.sources(),
                                  news_analysis=news, research_quality=quality,
                                  limitations=limitations, status=status, investment_analysis=investment,
                                  input_report_ids=[item.report_id for item in input_reports])
