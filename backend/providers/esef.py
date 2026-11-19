"""Read configured issuer-hosted ESEF Inline XBRL annual reports."""
import io
import json
import os
import re
from time import monotonic
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree as ET

from backend.agent.limits import record_provider_http
from backend.financial.capex import CAPEX_SCOPES, capex_duration
from backend.providers.sec import SecProviderError, TAGS
from backend.providers.transport import read_bounded

IX = '{http://www.xbrl.org/2013/inlineXBRL}'
INSTANCE = '{http://www.xbrl.org/2003/instance}'
ISO4217 = 'http://www.xbrl.org/2003/iso4217'


def esef_facts(document: bytes, issuer: dict, filing: dict, cutoff: str | None) -> list[dict]:
    try:
        filed = date.fromisoformat(filing['publication_date'])
        if filed > (date.fromisoformat(cutoff) if cutoff else date.today()):
            return []
        # No DOCTYPE or entities from external documents. ESEF XHTML is ordinarily UTF-8.
        text = document.decode('utf-8-sig')
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I):
            raise ValueError
        root = ET.fromstring(text)
    except (ValueError, KeyError, ET.ParseError):
        raise SecProviderError('Invalid official Inline XBRL document') from None
    namespaces = {}
    for _, (prefix, uri) in ET.iterparse(io.StringIO(text), events=('start-ns',)):
        if prefix and prefix in namespaces and namespaces[prefix] != uri:
            namespaces[prefix] = None
        else:
            namespaces[prefix] = uri
    inverse = {tag: metric for metric, tags in TAGS['ifrs-full'].items() for tag in tags}
    contexts = {}
    for context in root.iter(INSTANCE + 'context'):
        entity = context.find(f'{INSTANCE}entity/{INSTANCE}identifier')
        if (entity is None or entity.text != issuer['lei']
                or (entity.get('scheme') or '').rstrip('/') not in
                {'http://standards.iso.org/iso/17442', 'https://standards.iso.org/iso/17442'}
                or context.find(f'.//{INSTANCE}segment') is not None
                or context.find(f'.//{INSTANCE}scenario') is not None):
            continue
        period = context.find(INSTANCE + 'period')
        if period is None:
            continue
        start, end = (period.findtext(INSTANCE + key) for key in ('startDate', 'endDate'))
        if start and end:
            contexts[context.get('id')] = (start, end)
    units = {}
    for node in root.iter(INSTANCE + 'unit'):
        measures = node.findall(INSTANCE + 'measure')
        if len(measures) != 1 or measures[0].text is None:
            continue
        prefix, _, currency = measures[0].text.partition(':')
        if namespaces.get(prefix) == ISO4217 and re.fullmatch('[A-Z]{3}', currency):
            units[node.get('id')] = currency
    rows = []
    for node in root.iter(IX + 'nonFraction'):
        prefix, _, name = node.get('name', '').partition(':')
        taxonomy = namespaces.get(prefix) or ''
        context, unit = contexts.get(node.get('contextRef')), units.get(node.get('unitRef'))
        children = list(node)
        if (not re.fullmatch(r'https?://xbrl\.ifrs\.org/taxonomy/\d{4}-\d{2}-\d{2}/ifrs-full', taxonomy)
                or name not in inverse or not context or not unit or children):
            continue
        rendered = (node.text or '').strip()
        transform = node.get('format', '').split(':')[-1]
        try:
            if transform in {'numdotdecimal', 'num-dot-decimal'} and re.fullmatch(r'[0-9.,\s]+', rendered):
                rendered = re.sub(r'[,\s]', '', rendered)
            elif transform in {'numcommadecimal', 'num-comma-decimal'} and re.fullmatch(r'[0-9.,\s]+', rendered):
                rendered = re.sub(r'[.\s]', '', rendered).replace(',', '.')
            elif transform or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', rendered):
                continue
            scale = int(node.get('scale', '0'))
            if abs(scale) > 12 or node.get('sign') not in {None, '-'}:
                continue
            value = Decimal(rendered).scaleb(scale) * (-1 if node.get('sign') == '-' else 1)
        except (ValueError, InvalidOperation):
            continue
        start, end = context
        details = {}
        if inverse[name] == 'cash_capex':
            duration = capex_duration(start, end, 'ESEF annual report')
            if not duration or value < 0:
                continue
            details = {'capex_scope': 'ppe', 'duration_kind': duration, 'tag_definition': CAPEX_SCOPES['ppe']}
        rows.append(dict(cik=None, lei=issuer['lei'], taxonomy='ifrs-full', tag=name,
                         metric_name=inverse[name], value=value, unit=unit,
                         period_start=start, period_end=end, filed_at=filed.isoformat(),
                         form='ESEF annual report', source_url=filing['url'],
                         accession_number=node.get('id') or node.get('contextRef'),
                         original_text=(node.text or '').strip(), source_type='esef_xbrl',
                         publisher=issuer['name'], **details))
    return rows


def official_catalog() -> list[dict]:
    path = os.getenv('SELDON_OFFICIAL_ISSUER_CATALOG')
    if not path:
        return []
    try:
        issuers = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(issuers, list) or len(issuers) > 500:
            raise ValueError
        identifiers = set()
        for issuer in issuers:
            for key in ('name', 'ticker', 'exchange', 'country', 'official_host'):
                if not isinstance(issuer[key], str) or not issuer[key].strip():
                    raise ValueError
            if (not re.fullmatch('[A-Z0-9]{20}', issuer['lei']) or issuer['lei'] in identifiers
                    or not isinstance(issuer['aliases'], list)
                    or not all(isinstance(alias, str) and alias.strip() for alias in issuer['aliases'])
                    or not isinstance(issuer['filings'], list)):
                raise ValueError
            identifiers.add(issuer['lei'])
            for filing in issuer['filings']:
                address = urlsplit(filing['url'])
                if (address.scheme != 'https' or address.hostname != issuer['official_host']
                        or address.username or address.password or address.port not in {None, 443}):
                    raise ValueError
                date.fromisoformat(filing['publication_date'])
        return issuers
    except (OSError, ValueError, KeyError, TypeError):
        raise SecProviderError('Invalid official issuer catalog') from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_filing(issuer: dict, filing: dict, cutoff: str | None, timeout: float) -> list[dict]:
    request = Request(filing['url'], headers={'User-Agent': 'SeldonSystem research',
                                             'Accept': 'application/xhtml+xml,text/html'})
    size_limit = 16 * 1024 * 1024
    deadline = monotonic() + timeout
    try:
        record_provider_http('esef')
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            record_provider_http('esef', response=True)
            data = read_bounded(response, size_limit, deadline)
    except (OSError, ValueError) as exc:
        if isinstance(exc, HTTPError):
            record_provider_http('esef', response=True)
        raise SecProviderError('Official issuer filing unavailable') from None
    return esef_facts(data, issuer, filing, cutoff)
