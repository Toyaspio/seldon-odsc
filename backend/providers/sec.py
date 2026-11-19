"""SEC EDGAR company directory and structured financial facts."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from threading import Lock
from typing import ClassVar
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from backend.agent.limits import record_provider_http, record_provider_detail, count_provider_filing
from backend.financial.capex import CAPEX_SCOPES, STANDARD_CAPEX, capex_duration, standard_capex_scope
from backend.providers.transport import read_bounded


class SecProviderError(RuntimeError):
    pass


class SecRequestTimedOut(SecProviderError):
    pass


# Convenience aliases. Issuer coverage comes from EDGAR's directory.
ALIASES = {'微软': 'MSFT', '谷歌': 'GOOGL', 'google': 'GOOGL', 'amazon': 'AMZN', '亚马逊': 'AMZN',
           '脸书': 'META', '英伟达': 'NVDA', '甲骨文': 'ORCL', '阿里巴巴': 'BABA',
           'jpmorgan chase': 'JPM', 'tsmc': 'TSM', 'taiwan semiconductor': 'TSM',
           'toyota': 'TM', 'shell': 'SHEL', 'spacex': 'SPCX'}
ALIASES.update({'google cloud': 'GOOGL', 'aws': 'AMZN'})
SEEDS = {'microsoft': ('Microsoft Corporation', 'MSFT', '789019'),
         'alphabet': ('Alphabet Inc.', 'GOOGL', '1652044'),
         'amazon': ('Amazon.com, Inc.', 'AMZN', '1018724'),
         'meta': ('Meta Platforms, Inc.', 'META', '1326801'),
         'nvidia': ('NVIDIA Corporation', 'NVDA', '1045810'),
         'oracle': ('Oracle Corporation', 'ORCL', '1341439'),
         'alibaba': ('Alibaba Group Holding Limited', 'BABA', '1577552'),
         'jpmorgan chase': ('JPMorgan Chase & Co.', 'JPM', '19617'),
         'tsmc': ('Taiwan Semiconductor Manufacturing Company Limited', 'TSM', '1046179'),
         'toyota': ('Toyota Motor Corporation', 'TM', '1094517'),
         'shell': ('Shell plc', 'SHEL', '1306965'),
         'spacex': ('Space Exploration Technologies Corp.', 'SPCX', '1181412')}
TAGS = {
    'us-gaap': {
        'cash_capex': tuple(tag for tax, tag in STANDARD_CAPEX if tax == 'us-gaap'),
        'operating_cash_flow': ('NetCashProvidedByUsedInOperatingActivities',),
        'revenue': ('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'),
        'noncash_lease_investment': ('FinanceLeaseRightOfUseAssetObtainedInExchangeForFinanceLeaseLiability',),
        'remaining_performance_obligations': ('RevenueRemainingPerformanceObligation',),
    },
    'ifrs-full': {
        'cash_capex': tuple(tag for tax, tag in STANDARD_CAPEX if tax == 'ifrs-full'),
        'operating_cash_flow': ('CashFlowsFromUsedInOperatingActivities',),
        'revenue': ('Revenue',),
    },
}


VALUATION_TAGS = {
    'us-gaap': {
        'net_income_common': ('NetIncomeLossAvailableToCommonStockholdersBasic',),
        'diluted_eps': ('EarningsPerShareDiluted',),
        'common_equity': ('StockholdersEquity',),
        'cash_and_equivalents': ('CashAndCashEquivalentsAtCarryingValue',),
        'total_debt_and_finance_leases': ('DebtAndFinanceLeaseObligations',),
        'diluted_weighted_shares': ('WeightedAverageNumberOfDilutedSharesOutstanding',),
    },
    'dei': {'common_shares_outstanding': ('EntityCommonStockSharesOutstanding',)},
    'ifrs-full': {
        'net_income_common': ('ProfitLossAttributableToOwnersOfParent',),
        'diluted_eps': ('DilutedEarningsLossPerShare',),
        'common_equity': ('EquityAttributableToOwnersOfParent',),
        'cash_and_equivalents': ('CashAndCashEquivalents',),
    },
}


def normalized(name: str) -> str:
    value = re.sub(r'[^\w]+', ' ', name.casefold()).strip()
    return re.sub(r'\s+(?:(?:inc|corporation|corp|plc|ltd|limited|co|company)\s*)+$', '', value)


def available_by(filed: object, period_end: object, cutoff: date) -> bool:
    """A filing must be public by the cutoff, independently of its observation end."""
    try:
        return date.fromisoformat(filed) <= cutoff and date.fromisoformat(period_end) <= cutoff
    except (TypeError, ValueError):
        return False


class SecCompanyResolver:
    def matches(self, name: str, directory: list[dict] | None = None) -> list[dict]:
        name = ALIASES.get(name.strip().casefold(), name.strip())
        key = normalized(name)
        cik = re.fullmatch(r'(?:cik[\s:]*)?(\d{1,10})', name, re.I)
        records = directory if directory is not None else [
            {'name': title, 'ticker': ticker, 'cik': number.zfill(10)}
            for title, ticker, number in SEEDS.values()]
        matches = {}
        for record in records:
            qualified = f"{record.get('exchange') or ''} {record['ticker']}".strip()
            if ((cik and record['cik'] == cik[1].zfill(10))
                    or key in {normalized(record['name']), normalized(record['ticker']), normalized(qualified)}):
                matches.setdefault(record['cik'], record)
        if not matches and directory is not None and not cik and len(key) >= 4:
            prefix = key + ' '
            for record in records:
                if normalized(record['name']).startswith(prefix):
                    matches.setdefault(record['cik'], record)
        return list(matches.values())

    def resolve(self, name: str) -> dict:
        matches = self.matches(name)
        if len(matches) != 1:
            raise SecProviderError('No unique issuer match')
        record = matches[0]
        return {**record, 'company_id': record['ticker'].lower(), 'resolution_status': 'resolved'}


@dataclass
class SecFinancialDataProvider:
    user_agent: str | None = None
    timeout_seconds: float = 45
    opener: object = urlopen

    _request_lock: ClassVar[Lock] = Lock()
    _last_request_at: ClassVar[float] = 0
    _catalog: ClassVar[tuple[float, list[dict]] | None] = None
    _facts_cache: ClassVar[dict[str, tuple[float, dict]]] = {}

    def _get_json(self, url: str, timeout_seconds: float | None = None) -> dict:
        agent = self.user_agent or os.getenv('SEC_USER_AGENT')
        if not agent or '@' not in agent:
            raise SecProviderError('Set SEC_USER_AGENT to an application name and identifying contact email')
        timeout = min(timeout_seconds or self.timeout_seconds, self.timeout_seconds)
        deadline = time.monotonic() + timeout
        cls = type(self)
        if self.opener is urlopen:
            cached = cls._facts_cache.get(url)
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1]
        if not cls._request_lock.acquire(timeout=timeout):
            raise SecProviderError('SEC request timed out in queue')
        try:
            request = Request(url, headers={'User-Agent': agent, 'Accept': 'application/json',
                                            'Accept-Encoding': 'identity'})
            for attempt in range(3):
                delay = max(0, 0.2 - (time.monotonic() - cls._last_request_at))
                if time.monotonic() + delay >= deadline:
                    raise SecRequestTimedOut('SEC request deadline reached')
                time.sleep(delay)
                cls._last_request_at = time.monotonic()
                try:
                    record_provider_http('sec')
                    with self.opener(request, timeout=deadline - time.monotonic()) as response:
                        record_provider_http('sec', response=True)
                        payload = read_bounded(response, 32 * 1024 * 1024, deadline)
                    break
                except HTTPError as exc:
                    record_provider_http('sec', response=True)
                    if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                        raise
                except OSError:
                    if attempt == 2:
                        raise
                backoff = min(2 ** attempt, max(0, deadline - time.monotonic()))
                if backoff <= 0:
                    raise SecRequestTimedOut('SEC request deadline reached')
                time.sleep(backoff)
            if time.monotonic() >= deadline:
                raise SecRequestTimedOut('SEC request deadline reached')
            result = json.loads(payload)
            if not isinstance(result, dict):
                raise ValueError('Invalid SEC response')
            if self.opener is urlopen:
                if len(cls._facts_cache) >= 8:
                    cls._facts_cache.pop(next(iter(cls._facts_cache)))
                cls._facts_cache[url] = (time.monotonic(), result)
            return result
        except (OSError, ValueError) as exc:
            record_provider_detail('sec', error_code=(f'HTTP {exc.code}' if isinstance(exc, HTTPError)
                                                      else type(exc).__name__))
            error = SecRequestTimedOut if isinstance(exc, TimeoutError) else SecProviderError
            raise error(f'SEC request failed ({type(exc).__name__})') from None
        finally:
            cls._request_lock.release()

    def _get_filing(self, url: str, timeout: float) -> bytes:
        from backend.providers.live import open_provider_request

        agent = self.user_agent or os.getenv('SEC_USER_AGENT')
        if not agent or '@' not in agent:
            raise SecProviderError('SEC contact is not configured')
        deadline = time.monotonic() + timeout
        cls = type(self)
        if not cls._request_lock.acquire(timeout=timeout):
            raise SecProviderError('SEC filing timed out in queue')
        try:
            delay = max(0, 0.2 - (time.monotonic() - cls._last_request_at))
            if time.monotonic() + delay >= deadline:
                raise SecProviderError('SEC filing deadline reached')
            time.sleep(delay)
            cls._last_request_at = time.monotonic()
            request = Request(url, headers={'User-Agent': agent, 'Accept-Encoding': 'identity'})
            record_provider_http('sec')
            with open_provider_request(request, deadline - time.monotonic()) as response:
                record_provider_http('sec', response=True)
                content = read_bounded(response, 16 * 1024 * 1024, deadline)
                count_provider_filing('sec')
                return content
        except (OSError, ValueError) as exc:
            if isinstance(exc, HTTPError):
                record_provider_http('sec', response=True)
            raise SecProviderError('SEC filing could not be read') from None
        finally:
            cls._request_lock.release()

    def directory(self, timeout_seconds: float) -> list[dict]:
        cached = type(self)._catalog if self.opener is urlopen else None
        if cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
        result = self._get_json('https://www.sec.gov/files/company_tickers_exchange.json', timeout_seconds)
        try:
            fields = result['fields']
            indices = [fields.index(key) for key in ('cik', 'name', 'ticker', 'exchange')]
            records = []
            for row in result['data']:
                cik, name, ticker, exchange = [row[index] for index in indices]
                records.append({'cik': str(cik).zfill(10), 'name': name, 'ticker': ticker,
                                'exchange': exchange})
        except (KeyError, TypeError, ValueError, IndexError):
            raise SecProviderError('Invalid SEC company directory') from None
        if self.opener is urlopen:
            type(self)._catalog = (time.monotonic(), records)
        return records

    def issuer_labels(self, cik: str, timeout_seconds: float) -> list[str]:
        if not re.fullmatch(r'[0-9]{10}', cik):
            return []
        payload = self._get_json(f'https://data.sec.gov/submissions/CIK{cik}.json', timeout_seconds)
        if str(payload.get('cik', '')).zfill(10) != cik:
            return []
        former = payload.get('formerNames')
        former_names = [row.get('name') for row in former if isinstance(row, dict)] if isinstance(former, list) else []
        return [label for label in [payload.get('name'), *former_names] if isinstance(label, str)]

    def facts(self, company_id: str, as_of_date: str | None = None, *,
              timeout_seconds: float | None = None, include_valuation: bool = False) -> list[dict]:
        cik = (company_id.zfill(10) if company_id.isascii() and company_id.isdigit() else
               SecCompanyResolver().resolve(company_id)['cik'])
        if not re.fullmatch(r'[0-9]{10}', cik):
            raise SecProviderError('Invalid CIK')
        url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json'
        deadline = time.monotonic() + min(timeout_seconds or self.timeout_seconds, self.timeout_seconds)
        record_provider_detail('sec', parsed=False, filings_retrieved=0,
                               filings_parsed=0, filing_parse_failures=0)
        payload = self._get_json(url, timeout_seconds)
        if not isinstance(payload.get('facts'), dict):
            raise SecProviderError('Invalid SEC Company Facts document')
        record_provider_detail('sec', parsed=True)
        cutoff = date.fromisoformat(as_of_date or time.strftime('%Y-%m-%d', time.gmtime()))
        output = []
        taxonomies = TAGS | {'dei': {}} if include_valuation else TAGS
        for taxonomy, standard in taxonomies.items():
            metrics = standard | VALUATION_TAGS.get(taxonomy, {}) if include_valuation else standard
            concepts = payload['facts'].get(taxonomy, {})
            if not isinstance(concepts, dict):
                continue
            for metric_name, tags in metrics.items():
                for tag in tags:
                    concept = concepts.get(tag, {})
                    units = concept.get('units') if isinstance(concept, dict) else None
                    if not isinstance(units, dict):
                        continue
                    for unit, entries in units.items():
                        if not isinstance(entries, list):
                            continue
                        # Preserve tag differences and multiple filing versions so disagreements remain auditable.
                        selected = [row for row in entries
                                    if isinstance(row, dict) and row.get('form') in {'10-Q', '10-K', '20-F', '40-F', '6-K',
                                                           '10-Q/A', '10-K/A', '20-F/A', '40-F/A'}
                                    and 'val' in row and available_by(row.get('filed'), row.get('end'), cutoff)]
                        selected.sort(key=lambda row: (row['end'], str(row.get('start') or ''), row['filed']))
                        for row in selected[-180:]:
                            details = {}
                            if metric_name == 'cash_capex':
                                duration = capex_duration(row.get('start'), row['end'], row['form'])
                                if not duration or type(row['val']) not in {int, float} or row['val'] < 0:
                                    continue
                                scope = standard_capex_scope(cik, taxonomy, tag, row.get('accn'))
                                details = {'capex_scope': scope, 'duration_kind': duration,
                                           'tag_definition': concept.get('description') or CAPEX_SCOPES[scope]}
                            if row.get('start') and row['form'] in {'10-Q', '10-Q/A'}:
                                try:
                                    duration = (date.fromisoformat(row['end']) - date.fromisoformat(row['start'])).days + 1
                                except ValueError:
                                    continue
                                # A twelve-month figure in a quarterly filing can be trailing coverage.
                                # Use annual filings for these observations in the fiscal-year series.
                                if 360 <= duration <= 371:
                                    continue
                            accession = row.get('accn')
                            filing_url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                                          f"{accession.replace('-', '')}/{accession}-index.htm"
                                          if isinstance(accession, str) and re.fullmatch(r'[0-9]{10}-[0-9]{2}-[0-9]{6}', accession)
                                          else url)
                            output.append({
                                'cik': cik, 'metric_name': metric_name, 'taxonomy': taxonomy,
                                'tag': tag, 'value': row['val'], 'unit': unit,
                                'period_start': row.get('start'), 'period_end': row['end'],
                                'filed_at': row['filed'], 'form': row['form'], 'fiscal_year': row.get('fy'),
                                'fiscal_period': row.get('fp'), 'accession_number': accession,
                                'frame': row.get('frame'), 'source_url': filing_url, **details,
                            })
        from backend.providers.sec_capex_filings import reviewed_rows

        rows = output + reviewed_rows(self, cik, cutoff, deadline, output)
        record_provider_detail('sec', filing_types=sorted({row['form'] for row in rows if row.get('form')}))
        return rows
