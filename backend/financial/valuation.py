"""Market evidence is reopened at the publisher before any calculation."""
import json
import os
import re
import subprocess
from pathlib import Path
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlsplit
from urllib.request import Request
from urllib.error import HTTPError

from backend.agent.limits import ResourceLimitReached
from backend.agent.models import IssuerValuation, ProviderEvent, Source, ValuationMeasure
from backend.financial.validator import period_kind
from backend.providers.live import open_live_request
from backend.providers.transport import read_bounded
from time import monotonic


# Per-share earnings and aggregate common shares are only combined with quotes
# for the supported ordinary-share instruments, not ADRs or multiple classes.
ORDINARY_SHARE = {'MSFT', 'AMZN', 'NVDA', 'JPM'}


def valuation_is_core(query):
    return bool(re.search(r'\b(valuation|overvalued|undervalued|cheap|expensive|stock price|share price|market cap(?:italization)?|fair value|dcf|'
                          r'forward p/e|p/e|p/s|p/b|ev/fcf|ev/ebitda|fcf yield|price target)\b'
                          r'|估值|市值|市盈率|市净率|市销率|目标价|当前股价|最新股价', query, re.I))


def valuation_objectives(query):
    patterns = {
        'P/E': r'(?<!forward )\bp/e\b|(?<!远期)市盈率',
        'P/S': r'\bp/s\b|市销率', 'P/B': r'\bp/b\b|市净率',
        'EV/FCF': r'\bev/fcf\b', 'EV/EBITDA': r'\bev/ebitda\b',
        'FCF yield': r'\bfcf yield\b', 'Forward P/E': r'\bforward p/e\b|远期市盈率',
        'DCF': r'\bdcf\b|目标价|fair value|price target',
        'Market capitalization': r'\bmarket cap(?:italization)?\b|市值',
        'Closing price': r'\b(?:stock|share) price\b|当前股价|最新股价',
    }
    return {name for name, pattern in patterns.items() if re.search(pattern, query, re.I)}


def available_valuation(item, query):
    measures = {measure.name for measure in item.measures} if item else set()
    if item and item.price is not None:
        measures.add('Closing price')
    if 'P/E (last fiscal year)' in measures and not re.search(r'\bttm\b|滚动', query, re.I):
        measures.add('P/E')
    return measures


def market_queries(companies):
    return {f'{company.ticker} Nasdaq historical closing stock price market cap analyst EPS revenue EBITDA consensus': company.company_id
            for company in companies if company.resolution_status == 'resolved' and company.ticker
            and re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}', company.ticker)}


def _nasdaq_lead(url, ticker):
    try:
        address = urlsplit(url)
        return (address.scheme == 'https' and address.netloc.lower() == 'www.nasdaq.com'
                and address.path.lower().rstrip('/') in {
                    f'/market-activity/stocks/{ticker.lower()}',
                    f'/market-activity/stocks/{ticker.lower()}/historical'})
    except (ValueError, AttributeError):
        return False


def _read_market_document(request, timeout, on_response, opener=None):
    # Use the system HTTPS client on Windows. In this environment the public
    # quote endpoint rejects the Python TLS client; curl uses the OS TLS stack.
    system_root = os.environ.get('SystemRoot')
    curl = Path(system_root, 'System32', 'curl.exe') if os.name == 'nt' and system_root else None
    if opener is None and curl is not None and curl.is_file():
        try:
            response = subprocess.run(
                [str(curl), '--disable', '--silent', '--max-time', str(timeout), '--max-filesize', '524288',
                 '--proto', '=https', '--user-agent', 'Seldon Research Agent/0.1',
                 '--header', 'Accept: application/json', '--write-out', '\n%{http_code}', request.full_url],
                capture_output=True, timeout=timeout + .5, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError('Public market quote timed out') from None
        if response.returncode != 0:
            raise OSError('Public market response could not be read')
        payload, separator, code = response.stdout.rpartition(b'\n')
        if not separator or not code.isdigit() or len(payload) > 512 * 1024:
            raise ValueError('Invalid public market response')
        status = int(code)
        on_response(status)
        if status != 200:
            raise ValueError('Public market source returned an unsuccessful response')
        return json.loads(payload)

    deadline = monotonic() + timeout
    try:
        with (opener or open_live_request)(request, timeout) as response:
            on_response(getattr(response, 'status', None))
            return json.loads(read_bounded(response, 512 * 1024, deadline))
    except HTTPError as error:
        on_response(error.code)
        raise


def fetch_close(ticker, today, timeout, opener=None, on_response=lambda status: None):
    if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,9}', ticker):
        raise ValueError('Invalid market symbol')
    query = urlencode({'assetclass': 'stocks', 'fromdate': (today - timedelta(days=10)).isoformat(),
                       'todate': today.isoformat(), 'limit': 10})
    endpoint = f'https://api.nasdaq.com/api/quote/{ticker}/historical?{query}'
    request = Request(endpoint, headers={'User-Agent': 'Seldon Research Agent/0.1', 'Accept': 'application/json'})
    payload = _read_market_document(request, timeout, on_response, opener)
    data = payload['data']
    if data['symbol'] != ticker:
        raise ValueError('Market response symbol mismatch')
    closes = {}
    for row in data['tradesTable']['rows']:
        trading_day = datetime.strptime(row['date'], '%m/%d/%Y').date()
        value = row['close']
        if not re.fullmatch(r'\$[0-9,]+(?:\.\d+)?', value):
            raise ValueError('Expected a USD closing quote')
        price = Decimal(value[1:].replace(',', ''))
        if trading_day in closes and closes[trading_day] != price:
            raise ValueError('Conflicting closing quotes')
        if 0 < price < 10_000_000 and 0 <= (today - trading_day).days <= 7 and trading_day.weekday() < 5:
            closes[trading_day] = price
    if not closes:
        raise ValueError('No recent closing quote')
    day = max(closes)
    return closes[day], day, endpoint


def _valid_actuals(company_id, metrics, sources, quote_day):
    return [row for row in metrics if row.company_id == company_id and row.actual_or_guidance == 'actual'
            and row.evidence_ids and all((identifier in sources and sources[identifier].publication_date
                                         and sources[identifier].publication_date <= quote_day
                                         and sources[identifier].source_type in {'sec_xbrl', 'sec_statement', 'esef_xbrl'})
                                        for identifier in row.evidence_ids)]


def _trailing(rows, name, anchor, unit='USD'):
    eligible = [row for row in rows if row.metric_name == name and row.currency == 'USD'
                and row.unit == unit and row.period_start and row.period_end <= anchor]
    if name == 'free_cash_flow':
        eligible = [row for row in eligible if row.capex_scope in {'ppe', 'productive_assets'}]
    annual = [row for row in eligible if row.period_end == anchor and period_kind(row) == 'year']
    if len(annual) == 1:
        return Decimal(str(annual[0].value)), annual
    quarters = []
    end = anchor
    for _ in range(4):
        matches = [row for row in eligible if row.period_end == end and period_kind(row) == 'quarter']
        if len(matches) != 1:
            return None
        current = matches[0]
        quarters.append(current)
        end = (date.fromisoformat(current.period_start) - timedelta(days=1)).isoformat()
    if len({(row.accounting_basis, row.capex_scope) for row in quarters}) == 1:
        span = (date.fromisoformat(anchor) - date.fromisoformat(quarters[-1].period_start)).days + 1
        if 350 <= span <= 380:
            return sum(Decimal(str(row.value)) for row in quarters), quarters
    return None


def calculate_valuation(company, metrics, sources, trade_day, price, price_id):
    """Only audited actuals and independently observed price enter the formulas."""
    quote_day = trade_day.isoformat()
    rows = _valid_actuals(company.company_id, metrics, sources, quote_day)
    anchors = [row.period_end for row in rows if row.metric_name in {'revenue', 'net_income_common'}
               and period_kind(row) in {'year', 'quarter'} and row.period_end <= quote_day]
    anchor = max(anchors, default=None)
    measures = []
    missing = ['Verified dated analyst EPS, revenue and EBITDA consensus',
               'Comparable historical and peer multiples; evidenced DCF assumptions']

    def add(name, result, formula, inputs, evidence, kind='trailing_actual', end=None, unit='x'):
        measures.append(ValuationMeasure(name=name, value=float(result), unit=unit, formula=formula,
                        inputs={key: float(value) for key, value in inputs.items()},
                        evidence_ids=list(dict.fromkeys([price_id, *(i for r in evidence for i in r.evidence_ids)])),
                        basis=kind, period_end=end or anchor or quote_day))

    def latest_instant(name, unit='USD', end=None):
        values = [row for row in rows if row.metric_name == name and row.unit == unit
                  and row.period_start is None and (end is None or row.period_end == end)
                  and row.period_end <= quote_day]
        if not values:
            return None
        latest = max(row.period_end for row in values)
        matched = [row for row in values if row.period_end == latest]
        return matched[0] if len(matched) == 1 else None

    native = company.ticker in ORDINARY_SHARE
    shares = latest_instant('common_shares_outstanding', unit='shares', end=quote_day) if native else None
    market_cap = price * Decimal(str(shares.value)) if shares and shares.value > 0 else None
    if market_cap is not None:
        add('Market capitalization', market_cap, 'Closing price × common shares outstanding',
            {'close': price, 'shares': shares.value}, [shares], 'instant_actual', quote_day, 'USD')
    else:
        missing.append('Dated issuer market capitalization or same-day total shares for a verified ordinary share class')

    fiscal_eps = [row for row in rows if native and row.metric_name == 'diluted_eps'
                  and row.unit == 'USD/shares' and period_kind(row) == 'year'
                  and 0 <= (trade_day - date.fromisoformat(row.period_end)).days <= 380]
    eps_end = max((row.period_end for row in fiscal_eps), default=None)
    eps_at_end = [row for row in fiscal_eps if row.period_end == eps_end]
    if len(eps_at_end) == 1 and eps_at_end[0].value > 0:
        eps = eps_at_end[0]
        add('P/E (last fiscal year)', price / Decimal(str(eps.value)),
            'Closing price ÷ last reported fiscal-year diluted EPS',
            {'close': price, 'fiscal_year_diluted_eps': eps.value}, [eps], 'fiscal_year_actual', eps.period_end)
    if anchor is None or not 0 <= (trade_day - date.fromisoformat(anchor)).days <= 125:
        missing.append('Recent audited financial period for trailing multiples')
        return measures, missing

    earnings = _trailing(rows, 'net_income_common', anchor)
    revenue = _trailing(rows, 'revenue', anchor)
    fcf = _trailing(rows, 'free_cash_flow', anchor)
    if market_cap and earnings and earnings[0] > 0:
        add('P/E', market_cap / earnings[0], 'Market cap ÷ TTM income available to common shareholders',
            {'market_cap': market_cap, 'ttm_common_income': earnings[0]}, [shares, *earnings[1]])
    else:
        missing.append('Positive trailing earnings and a verified issuer market capitalization for TTM P/E')

    if market_cap and revenue and revenue[0] > 0:
        add('P/S', market_cap / revenue[0], 'Market cap ÷ TTM revenue',
            {'market_cap': market_cap, 'ttm_revenue': revenue[0]}, [shares, *revenue[1]])
    equity = latest_instant('common_equity', end=anchor)
    if market_cap and equity and equity.value > 0:
        add('P/B', market_cap / Decimal(str(equity.value)), 'Market cap ÷ reported stockholders’ equity',
            {'market_cap': market_cap, 'equity': equity.value}, [shares, equity], 'instant_actual')
    elif company.ticker == 'JPM':
        missing.append('Comparable reported equity and issuer market cap for bank P/B')
    if company.ticker != 'JPM' and market_cap and fcf:
        add('FCF yield', fcf[0] / market_cap * 100,
            f'TTM operating cash flow less cash CapEx ({fcf[1][0].capex_scope}) ÷ market cap × 100',
            {'market_cap': market_cap, 'ttm_fcf': fcf[0]}, [shares, *fcf[1]], unit='%')
        debt = latest_instant('total_debt_and_finance_leases', end=anchor)
        cash = latest_instant('cash_and_equivalents', end=anchor)
        if (debt and cash and debt.value >= 0 and cash.value >= 0 and fcf[0] > 0
                and len({debt.accounting_basis, cash.accounting_basis, fcf[1][0].accounting_basis}) == 1):
            ev = market_cap + Decimal(str(debt.value)) - Decimal(str(cash.value))
            if ev > 0:
                add('EV/FCF', ev / fcf[0], '(Market cap + reported debt and finance leases − cash) ÷ reported TTM FCF (simplified EV)',
                    {'market_cap': market_cap, 'debt_and_finance_leases': debt.value, 'cash': cash.value,
                     'ttm_fcf': fcf[0]}, [shares, debt, cash, *fcf[1]])
        else:
            missing.append('Comparable same-period debt and cash for EV/FCF')
    elif company.ticker != 'JPM' and not fcf:
        missing.append('Comparable trailing cash flow and CapEx with a single verified perimeter')
    if company.ticker != 'JPM':
        missing.append('Comparable EBITDA and peer definitions for EV/EBITDA')
    return measures, missing


def _qualitative_outlook(company, metrics):
    if company.ticker == 'JPM':
        return ('For a bank, compare sustainable return on common equity and credit costs against book valuation. '
                'Industrial EV/FCF is not included.'), []
    fcf = [row for row in metrics if row.company_id == company.company_id
           and row.actual_or_guidance == 'actual' and row.metric_name == 'free_cash_flow'
           and period_kind(row) == 'quarter' and row.capex_scope and row.evidence_ids]
    latest = max((row.period_end for row in fcf), default=None)
    recent = [row for row in fcf if row.period_end == latest]
    if len(recent) == 1:
        row = recent[0]
        direction = ('cash generation exceeded cash CapEx' if row.value > 0 else
                     'cash CapEx exceeded cash generation' if row.value < 0 else 'cash generation equaled cash CapEx')
        return (f'For the verified quarter ended {latest}, {direction} under the reported {row.capex_scope} perimeter. '
                'To assess the valuation, monitor whether that cash conversion persists as investment changes.',
                row.evidence_ids)
    return ('To assess valuation, compare normalized earnings and cash conversion with the growth implied by price. '
            'Growth accompanied by faster cash investment calls for a closer review of realized returns.'), []


def run_valuation(companies, metrics, sources, leads, searched, limits, today, historical=False, query=""):
    records, market_sources = [], []
    source_index = {source.source_id: source for source in sources}
    for company in companies:
        qualitative, qualitative_ids = _qualitative_outlook(company, metrics)
        reason = None
        if historical:
            reason = 'Point-in-time market quotes and estimates are unavailable for this historical research task.'
        elif company.company_id not in searched:
            reason = 'No live market search was dispatched for this issuer.'
        elif not company.ticker or not any(_nasdaq_lead(url, company.ticker) for url in leads):
            reason = 'Search did not identify a supported exchange quote page for this symbol.'
        if reason:
            records.append(IssuerValuation(company_id=company.company_id,
                           status='not_executed' if historical or company.company_id not in searched else 'data_insufficient',
                           missing=['Verified dated closing price and issuer market capitalization',
                                    'Forward earnings and revenue consensus'], note=reason, qualitative=qualitative, qualitative_evidence_ids=qualitative_ids))
            continue
        started = monotonic()
        event = ProviderEvent(provider='market_source', stage='valuation_quote_verification',
                              company_id=company.company_id)

        def received(status):
            event.http_responses = 1
            event.http_status = status

        try:
            timeout = min(8, limits.remaining())
            event.http_attempts = 1
            price, trade_day, url = fetch_close(company.ticker, today, timeout, on_response=received)
        except (OSError, AttributeError, ValueError, TypeError, KeyError, InvalidOperation, ResourceLimitReached) as error:
            event.outcome = 'failed'
            event.error_code = type(error).__name__
            records.append(IssuerValuation(company_id=company.company_id, status='data_insufficient',
                           missing=['Verified dated closing price', 'Dated issuer market capitalization',
                                    'Forward earnings and revenue consensus'],
                           note='The exchange did not return a verifiable recent closing quote.',
                           qualitative=qualitative, qualitative_evidence_ids=qualitative_ids))
            continue
        finally:
            event.elapsed_ms = round((monotonic() - started) * 1000)
            limits.events.append(event)
        price_id = f'market:nasdaq:{company.ticker}:{trade_day.isoformat()}'
        market_sources.append(Source(source_id=price_id, title=f'{company.ticker} daily closing quote',
                              publisher='Nasdaq', url=url, source_type='market_quote', company_id=company.company_id,
                              numeric_value=float(price), unit='USD', period_end=trade_day.isoformat(),
                              publication_date=trade_day.isoformat(), retrieved_at=datetime.now().astimezone(),
                              structured_locator=f'data.tradesTable.rows: {trade_day.isoformat()} close'))
        measures, missing = calculate_valuation(company, metrics, source_index, trade_day, price, price_id)
        record = IssuerValuation(company_id=company.company_id, status='partial', price=float(price), currency='USD',
                                 trade_date=trade_day.isoformat(), price_source_id=price_id, measures=measures,
                                 missing=missing, qualitative=qualitative, qualitative_evidence_ids=qualitative_ids,
                                 note='USD closing price for the listed security; intraday timestamp not supplied.')
        requested = valuation_objectives(query)
        if requested and requested <= available_valuation(record, query):
            record.status = 'completed'
        records.append(record)
    return records, market_sources
