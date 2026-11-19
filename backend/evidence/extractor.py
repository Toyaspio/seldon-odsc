from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from urllib.parse import urlsplit

from backend.agent.models import Company, Metric, Source
from backend.financial.capex import CANONICAL_CAPEX, CAPEX_SCOPES

HKEX_DISCLOSURE_HOSTS = {'www.hkexnews.hk', 'www1.hkexnews.hk'}


def identifier(*parts) -> str:
    return sha256(repr(parts).encode()).hexdigest()[:24]


class EvidenceExtractor:
    def web(self, results: list[dict], issuers: list[Company], domains: dict[str, str]) -> list[Source]:
        sources = {}
        for result in results:
            url = result.get('url')
            if not isinstance(url, str) or len(url) > 2048:
                continue
            try:
                address = urlsplit(url)
            except ValueError:
                continue
            if address.scheme != 'https' or not address.hostname or address.username or address.password:
                continue
            host = address.hostname.lower()
            owner = next((c for c in issuers if c.company_id in domains and (
                host == domains[c.company_id] or host.endswith('.' + domains[c.company_id]))), None)
            is_regulator = host in HKEX_DISCLOSURE_HOSTS or host in {'sec.gov', 'www.sec.gov', 'data.sec.gov',
                                    'www.londonstockexchange.com',
                                    'www.sedarplus.ca', 'www.asx.com.au', 'www.jpx.co.jp',
                                    'www.handelsregister.de', 'www.amf-france.org'}
            classification = 'regulator_lead' if is_regulator else 'issuer_lead' if owner else 'web_search'
            published = result.get('published_date')
            sources[url] = Source(
                source_id='web:' + identifier(url), title=str(result.get('title') or host)[:300],
                publisher=host, url=url, publication_date=published[:40] if isinstance(published, str) else None,
                retrieved_at=datetime.now(timezone.utc), source_type=classification,
                company_id=owner.company_id if owner else None,
                quoted_text=str(result.get('content') or '')[:1500],
            )
        return sorted(sources.values(), key=lambda source: source.source_type == 'web_search')

    def structured(self, company: Company, rows: list[dict]) -> tuple[list[Metric], list[Source]]:
        facts, sources, fiscal_labels = {}, {}, {}
        for row in rows:
            try:
                metric_name = row['metric_name']
                taxonomy, tag = row['taxonomy'], row['tag']
                start, end = row.get('period_start'), row['period_end']
                filed, currency, url = row['filed_at'], row['unit'], row['source_url']
                end_date, filed_date = date.fromisoformat(end), date.fromisoformat(filed)
                if filed_date < end_date or (start and date.fromisoformat(start) > end_date):
                    continue
                mixed_unit = (metric_name == 'diluted_eps' and re.fullmatch('[A-Z]{3}/shares', currency)
                              or metric_name in {'diluted_weighted_shares', 'common_shares_outstanding'} and currency == 'shares')
                if not mixed_unit and not re.fullmatch('[A-Z]{3}', currency):
                    continue
                number = Decimal(str(row['value']))
                if not number.is_finite() or abs(number) > 2**53 or Decimal(str(float(number))) != number:
                    continue
                source_type = row.get('source_type', 'sec_xbrl')
                scope = row.get('capex_scope')
                if metric_name == 'cash_capex' and (not start or number < 0 or
                        (scope is not None and scope not in CAPEX_SCOPES)):
                    continue
                address = urlsplit(url)
                if address.scheme != 'https' or address.username or address.password:
                    continue
                if source_type in {'sec_xbrl', 'sec_statement'} and (not company.cik or row.get('cik') != company.cik
                        or address.hostname not in {'www.sec.gov', 'data.sec.gov'}):
                    continue
                if source_type == 'esef_xbrl' and company.company_id != f"lei:{row.get('lei')}":
                    continue
                if source_type not in {'sec_xbrl', 'esef_xbrl', 'sec_statement'}:
                    continue
                accession = row.get('accession_number')
                fiscal_label = row.get('fiscal_label') if source_type == 'sec_statement' else None
                basis = row.get('accounting_basis') or {'us-gaap': 'US GAAP', 'ifrs-full': 'IFRS', 'dei': 'Issuer reference data'}[taxonomy]
                tag_path = row.get('xbrl_tag') or (
                    f'cash-flow table: {tag}' if source_type == 'sec_statement' else f'{taxonomy}:{tag}')
                locator = f'{tag_path} {currency} {start or "instant"}..{end}; accession {accession or "unavailable"}'
                if fiscal_label:
                    locator += f'; issuer fiscal period {fiscal_label}'
                evidence_id = source_type + ':' + identifier(company.company_id, tag, start, end, currency, number, accession, filed, url)
                sources[evidence_id] = Source(
                    source_id=evidence_id, title=f'{row["form"]} · {tag}',
                    publisher=row.get('publisher', 'U.S. Securities and Exchange Commission'), url=url,
                    publication_date=filed, retrieved_at=datetime.now(timezone.utc),
                    source_type=source_type, structured_locator=locator,
                    quoted_text=row.get('original_text'),
                    company_id=company.company_id, issuer_name=company.company_name,
                    metric_name=metric_name, numeric_value=float(number),
                    unit=currency, period_start=start, period_end=end, accounting_basis=basis,
                    actual_or_guidance='actual', capex_scope=scope,
                    financial_concept=CANONICAL_CAPEX if metric_name == 'cash_capex' else None,
                    xbrl_tag=row.get('xbrl_tag', f'{taxonomy}:{tag}') if metric_name == 'cash_capex' else None,
                    tag_definition=row.get('tag_definition'),
                    original_numeric_value=str(row.get('raw_value', row['value'])) if metric_name == 'cash_capex' else None,
                )
                key = (metric_name, currency, start, end, basis, number)
                if scope is not None:
                    key += (scope,)
                if fiscal_label:
                    fiscal_labels.setdefault(key, set()).add(fiscal_label)
                if key not in facts:
                    facts[key] = Metric(
                        metric_id='fact:' + identifier(company.company_id, *key),
                        company_id=company.company_id, metric_name=metric_name, value=float(number),
                        currency=(currency.split('/')[0] if currency != 'shares' else None),
                        unit=currency, period_start=start, period_end=end,
                        fiscal_period=f'{start} to {end}' if start else f'as of {end}',
                        accounting_basis=basis, actual_or_guidance='actual', evidence_ids=[],
                        capex_scope=scope, duration_kind=row.get('duration_kind'),
                    )
                if evidence_id not in facts[key].evidence_ids:
                    facts[key].evidence_ids.append(evidence_id)
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue
        for key, labels in fiscal_labels.items():
            if len(labels) == 1:
                facts[key].fiscal_period = next(iter(labels))
        return list(facts.values()), list(sources.values())
