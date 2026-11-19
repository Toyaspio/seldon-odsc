"""SEC filing fallbacks for reviewed consolidated statement disclosures.

Entries are exact SEC archive documents. A new taxonomy or untagged exhibit
layout must be reviewed before registration. Never fetch a URL from a model.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html import unescape
import logging
import re
from time import monotonic

from backend.financial.capex import CAPEX_SCOPES, capex_duration


logger = logging.getLogger(__name__)
SEC_ARCHIVE = 'https://www.sec.gov/Archives/edgar/data/'
TSM = '0001046179'
NVDA = '0001045810'
SHELL = '0001306965'
TOYOTA = '0001094517'
ALIBABA = '0001577552'
JPMORGAN = '0000019617'


@dataclass(frozen=True)
class ReviewedFiling:
    cik: str
    accession: str
    document: str
    filed: str
    end: str
    form: str
    taxonomy: str
    layout: str
    historical: bool = False
    # Explicit issuer labels keyed by observation end, including comparative columns.
    fiscal_quarters: tuple[tuple[str, str], ...] = ()

    @property
    def url(self):
        return f'{SEC_ARCHIVE}{int(self.cik)}/{self.accession.replace("-", "")}/{self.document}'


FILINGS = (
    ReviewedFiling(NVDA, '0001045810-26-000088', 'q3fy27pr.htm', '2026-11-17', '2026-10-25', '8-K', 'us-gaap', 'nvda',
                   fiscal_quarters=(('2026-10-25', 'FY2027 Q3'), ('2025-10-26', 'FY2026 Q3'))),
    ReviewedFiling(NVDA, '0001045810-21-000010', 'nvda-20210131.htm', '2021-02-26', '2021-01-31',
                   '10-K', 'us-gaap', 'inline', historical=True),
    ReviewedFiling(TSM, '0001046179-26-000801', 'a2026q3consolidatedreport-.htm', '2026-11-13', '2026-09-30', '6-K', 'ifrs-full', 'tsm'),
    ReviewedFiling(TSM, '0001046179-26-000541', 'a2026q2consolidatedreport-.htm', '2026-08-14', '2026-06-30', '6-K', 'ifrs-full', 'tsm_half'),
    ReviewedFiling(TSM, '0001628280-26-025362', 'tsm-20251231.htm', '2026-04-16', '2025-12-31', '20-F', 'ifrs-full', 'inline'),
    ReviewedFiling(TOYOTA, '0001193125-26-448368', 'd87505dex992.htm', '2026-11-05', '2026-09-30', '6-K', 'ifrs-full', 'toyota'),
    ReviewedFiling(TOYOTA, '0001193125-26-264811', 'd101983d20f.htm', '2026-06-10', '2026-03-31', '20-F', 'ifrs-full', 'inline'),
    ReviewedFiling(ALIBABA, '0001104659-26-099220', 'tm2623667d1_ex99-1.htm', '2026-08-20', '2026-06-30', '6-K', 'us-gaap', 'alibaba'),
    ReviewedFiling(ALIBABA, '0001193125-26-231755', 'baba-20260331.htm', '2026-05-20', '2026-03-31', '20-F', 'us-gaap', 'inline'),
    ReviewedFiling(SHELL, '0001628280-26-068044', 'quarterlyexhibit992-q32026.htm', '2026-10-29', '2026-09-30', '6-K', 'ifrs-full', 'shell'),
    ReviewedFiling(SHELL, '0001628280-26-017024', 'shel-20251231.htm', '2026-03-12', '2025-12-31', '20-F', 'ifrs-full', 'inline'),
)

INLINE_CAPEX = {
    NVDA: ('nvda:PurchasesOfPropertyAndEquipmentAndIntangibleAssets', 'productive_assets'),
    TSM: ('ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities', 'ppe'),
    TOYOTA: ('tm:PurchaseOfPropertyPlantAndEquipmentExcludingEquipmentLeasedToOthersClassifiedAsInvestingActivities',
             'ppe_excluding_leased_equipment'),
    ALIBABA: ('baba:PaymentsToAcquireLandUseRightsPropertyAndEquipment', 'ppe_and_land_use'),
    SHELL: ('ifrs-full:PurchaseOfOtherLongtermAssetsClassifiedAsInvestingActivities', 'other_long_term_investing_assets'),
}
INLINE_NAMESPACE = {
    'nvda': 'http://www.nvidia.com/20210131',
    'tm': 'http://www.toyota-global.com/20260331',
    'baba': 'http://www.alibabagroup.com/20260331',
    'ifrs-full': 'https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full',
}
ATTRS = re.compile(r"""([\w:-]+)\s*=\s*(["'])(.*?)\2""", re.S)
TABLE = re.compile(r'<table\b[^>]*>(.*?)</table>', re.I | re.S)
ROW = re.compile(r'<tr\b[^>]*>(.*?)</tr>', re.I | re.S)
CELL = re.compile(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', re.I | re.S)
NUMBER = re.compile(r'[+-]?\d[\d,]*(?:\.\d+)?')


def attributes(text):
    return {key.lower(): unescape(value) for key, _, value in ATTRS.findall(text)}


def row_record(filing, metric, name, value, raw, unit, start, end, duration, scope=None, tagged=False):
    quarter = dict(filing.fiscal_quarters).get(end)
    fiscal_label = quarter if duration == 'quarter' else f'{quarter} YTD' if quarter and duration == 'ytd' else None
    return {'fiscal_label': fiscal_label, 'cik': filing.cik, 'taxonomy': filing.taxonomy, 'tag': name.split(':', 1)[-1] if tagged else name,
            'xbrl_tag': name if tagged else None, 'value': value, 'raw_value': raw, 'unit': unit,
            'period_start': start, 'period_end': end, 'filed_at': filing.filed, 'form': filing.form,
            'duration_kind': duration, 'metric_name': metric, 'source_url': filing.url, 'capex_scope': scope,
            'tag_definition': CAPEX_SCOPES.get(scope), 'accession_number': filing.accession,
            'source_type': 'sec_xbrl' if tagged else 'sec_statement'}


def inline_rows(text: str, filing: ReviewedFiling) -> list[dict]:
    cash_tag, scope = INLINE_CAPEX[filing.cik]
    operating_tag = ('us-gaap:NetCashProvidedByUsedInOperatingActivities' if filing.taxonomy == 'us-gaap'
                     else 'ifrs-full:CashFlowsFromUsedInOperatingActivities')
    html = re.search(r'<html\b([^>]*)>', text, re.I)
    namespace = attributes(html[1]) if html else {}
    if (namespace.get('xmlns:xbrli') != 'http://www.xbrl.org/2003/instance'
            or namespace.get('xmlns:iso4217') != 'http://www.xbrl.org/2003/iso4217'
            or namespace.get('xmlns:'+cash_tag.split(':')[0]) != INLINE_NAMESPACE[cash_tag.split(':')[0]]):
        return []
    contexts = {}
    for attrs, content in re.findall(r'<xbrli:context\b([^>]*)>(.*?)</xbrli:context>', text, re.I | re.S):
        if ('<xbrli:segment' in content.lower() or '<xbrli:scenario' in content.lower()
                or not re.search(r'<xbrli:identifier\s+scheme="https?://www.sec.gov/CIK">\s*'
                                 + filing.cik + r'\s*</xbrli:identifier>', content, re.I)):
            continue
        start = re.search(r'<xbrli:startDate>(\d{4}-\d{2}-\d{2})</xbrli:startDate>', content, re.I)
        end = re.search(r'<xbrli:endDate>(\d{4}-\d{2}-\d{2})</xbrli:endDate>', content, re.I)
        if start and end and end[1] <= filing.end and capex_duration(start[1], end[1], filing.form) == 'year':
            contexts[attributes(attrs).get('id')] = (start[1], end[1])
    units = {}
    for attrs, content in re.findall(r'<xbrli:unit\b([^>]*)>(.*?)</xbrli:unit>', text, re.I | re.S):
        measure = re.fullmatch(r'\s*<xbrli:measure>iso4217:([A-Z]{3})</xbrli:measure>\s*', content)
        if measure:
            units[attributes(attrs).get('id')] = measure[1]
    result = []
    for attrs, body in re.findall(r'<ix:nonfraction\b([^>]*)>([^<]*)</ix:nonfraction>', text, re.I):
        attrs = attributes(attrs)
        name = attrs.get('name')
        period, unit = contexts.get(attrs.get('contextref')), units.get(attrs.get('unitref'))
        body = unescape(body).strip()
        if (name not in {cash_tag, operating_tag} or not period or not unit
                or not NUMBER.fullmatch(body) or attrs.get('format') not in {'ixt:num-dot-decimal', 'ixt:numdotdecimal'}
                or attrs.get('sign') not in {None, '-'}):
            continue
        scale = attrs.get('scale', '0')
        if not re.fullmatch(r'-?\d{1,2}', scale) or abs(int(scale)) > 12:
            continue
        value = Decimal(body.replace(',', '')).scaleb(int(scale)) * (-1 if attrs.get('sign') == '-' else 1)
        capex = name == cash_tag
        if capex and value < 0:
            continue
        metric = 'cash_capex' if capex else 'operating_cash_flow'
        result.append(row_record(filing, metric, name, value, value, unit, *period, 'year',
                                 scope if capex else None, True))
    return result


def cells(row):
    return [' '.join(unescape(re.sub(r'<[^>]*>', ' ', cell)).split()) for cell in CELL.findall(row)]


def numbers(row):
    values = []
    for text in row:
        value = text.strip('$ ').replace(' ', '')
        if NUMBER.fullmatch(value.strip('()')):
            values.append(Decimal(value.strip('()').replace(',', '')) * (-1 if value.startswith('(') else 1))
    return tuple(values)


def extract_line(tables, label, columns, outflow=False):
    candidates = set()
    for table in tables:
        for row in ROW.findall(table):
            parsed = cells(row)
            if label not in parsed:
                continue
            values = numbers(parsed)
            if len(values) == columns and (not outflow or all(value <= 0 for value in values)):
                candidates.add(values)
    if len(candidates) != 1:
        return ()
    return candidates.pop()


def q(start, end):
    return (start, end, 'quarter')


def ytd(start, end):
    return (start, end, 'ytd')


# Column periods and labels were reviewed against each exact accession. Keeping
# the scoped row names prevents selecting summary tables, sale proceeds or totals.
TABLE_LAYOUTS = {
    'nvda': ('USD', 6, 'productive_assets', 'CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS',
             'Purchases related to property and equipment and intangible assets', 'Net cash provided by operating activities',
             (q('2026-07-27', '2026-10-25'), q('2025-07-28', '2025-10-26'),
              ytd('2026-01-26', '2026-10-25'), ytd('2025-01-27', '2025-10-26'))),
    'tsm': ('TWD', 3, 'ppe', 'Nine Months Ended September 30',
            'Property, plant and equipment', 'Net cash generated by operating activities',
            (ytd('2026-01-01', '2026-09-30'), ytd('2025-01-01', '2025-09-30'))),
    'tsm_half': ('TWD', 3, 'ppe', 'Six Months Ended June 30',
                 'Property, plant and equipment', 'Net cash generated by operating activities',
                 (ytd('2026-01-01', '2026-06-30'), ytd('2025-01-01', '2025-06-30'))),
    'toyota': ('JPY', 6, 'ppe_excluding_leased_equipment', 'For the first half ended September 30, 2026',
               'Additions to fixed assets excluding equipment leased to others', 'Net cash provided by (used in) operating activities',
               (ytd('2025-04-01', '2025-09-30'), ytd('2026-04-01', '2026-09-30'))),
    'alibaba': ('CNY', 6, 'ppe_excluding_campus_land', 'Three months ended June 30,',
                'Less: Purchase of property and equipment (excluding land use rights and construction in progress relating to office campuses)',
                'Net cash provided by operating activities',
                (q('2025-04-01', '2025-06-30'), q('2026-04-01', '2026-06-30'), None)),
    'shell': ('USD', 6, 'other_long_term_investing_assets', 'CONSOLIDATED STATEMENT OF CASH FLOWS',
              'Capital expenditure', 'Cash flow from operating activities',
              (q('2026-07-01', '2026-09-30'), q('2026-04-01', '2026-06-30'), q('2025-07-01', '2025-09-30'),
               ytd('2026-01-01', '2026-09-30'), ytd('2025-01-01', '2025-09-30'))),
}


# Income rows are scoped to exact reviewed exhibits and their consolidated
# statement headers. Columns with a translated currency have no period mapping.
INCOME_LAYOUTS = {
    'nvda': ('Revenue', ('Three Months Ended', 'Nine Months Ended', 'Cost of revenue', 'Operating income'),
             TABLE_LAYOUTS['nvda'][-1]),
    'toyota': ('Total sales revenues', ('Yen in millions', 'For the first half ended', 'Cost of products sold'),
               TABLE_LAYOUTS['toyota'][-1]),
    'alibaba': ('Revenue', ('Three months ended June 30,', 'in millions, except per share data',
                            'Cost of revenue', 'Income from operations'),
                TABLE_LAYOUTS['alibaba'][-1]),
    'shell': ('Revenue 1', ('CONSOLIDATED STATEMENT OF INCOME', 'Quarters', '$ million'),
              TABLE_LAYOUTS['shell'][-1]),
}


def statement_rows(text: str, filing: ReviewedFiling) -> list[dict]:
    unit, scale, scope, heading, capex_label, ocf_label, periods = TABLE_LAYOUTS[filing.layout]
    document_tables = [(table, ' '.join(' '.join(cells(table)).split()).casefold()) for table in TABLE.findall(text)]
    tables = [table for table, labels in document_tables if heading.casefold() in labels]
    result = []
    for metric, label, outflow in [('cash_capex', capex_label, True), ('operating_cash_flow', ocf_label, False)]:
        for value, period in zip(extract_line(tables, label, len(periods), outflow), periods):
            if period is None:
                continue
            raw = value.scaleb(scale)
            item = row_record(filing, metric, label, -raw if outflow else raw, raw, unit, *period,
                              scope if outflow else None)
            if filing.cik == TSM:
                item['accounting_basis'] = 'Taiwan-IFRS'
            result.append(item)
    if filing.layout in INCOME_LAYOUTS:
        label, markers, income_periods = INCOME_LAYOUTS[filing.layout]
        income_tables = [table for table, labels in document_tables
                         if all(marker.casefold() in labels for marker in markers)]
        for value, period in zip(extract_line(income_tables, label, len(income_periods)), income_periods):
            if period is not None:
                raw = value.scaleb(scale)
                result.append(row_record(filing, 'revenue', label, raw, raw, unit, *period))
    return result


def parse_reviewed_filing(data: bytes, filing: ReviewedFiling) -> list[dict]:
    text = data.decode('utf-8-sig', errors='replace')
    return inline_rows(text, filing) if filing.layout == 'inline' else statement_rows(text, filing)


def reviewed_rows(provider, cik, cutoff: date, deadline: float, company_facts: list[dict]) -> list[dict]:
    from backend.agent.limits import count_provider_filing
    from backend.providers.sec import SecProviderError

    result = []
    for filing in FILINGS:
        if filing.cik != cik or date.fromisoformat(filing.filed) > cutoff or date.fromisoformat(filing.end) > cutoff:
            continue
        need_capex = not any(row['metric_name'] == 'cash_capex'
                             and (row['period_end'] == filing.end if filing.historical else row['period_end'] >= filing.end)
                             and (filing.layout != 'inline' or row.get('duration_kind') == 'year')
                             for row in company_facts)
        need_revenue = False
        if filing.layout in INCOME_LAYOUTS:
            unit = TABLE_LAYOUTS[filing.layout][0]
            current = next(period for period in INCOME_LAYOUTS[filing.layout][-1]
                           if period and period[1] == filing.end)
            need_revenue = not any(
                row['metric_name'] == 'revenue' and row['period_end'] >= filing.end
                and row['taxonomy'] == filing.taxonomy and row['unit'] == unit
                and capex_duration(row.get('period_start'), row['period_end'], row['form']) == current[2]
                for row in company_facts)
        if not (need_capex or need_revenue):
            continue
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        try:
            data = provider._get_filing(filing.url, min(remaining, 12))
        except SecProviderError:
            logger.warning('SEC reviewed filing unavailable accession=%s', filing.accession)
            continue
        try:
            extracted = parse_reviewed_filing(data, filing)
        except ValueError:
            extracted = []
        missing_expected = ((need_capex and not any(row['metric_name'] == 'cash_capex' for row in extracted))
                            or (need_revenue and not any(row['metric_name'] == 'revenue' for row in extracted)))
        if missing_expected:
            count_provider_filing('sec', 'filing_parse_failures')
            logger.warning('SEC reviewed filing missing an expected statement row accession=%s', filing.accession)
        if extracted:
            count_provider_filing('sec', 'filings_parsed')
        for row in extracted:
            if (row['metric_name'] == 'cash_capex' and not need_capex
                    or row['metric_name'] == 'revenue' and not need_revenue):
                continue
            if row['metric_name'] in {'operating_cash_flow', 'revenue'} and any(
                base['metric_name'] == row['metric_name'] and all(
                    base.get(key) == row.get(key) for key in ('taxonomy', 'accounting_basis', 'unit', 'period_start', 'period_end'))
                for base in company_facts
            ):
                continue
            result.append(row)
    return result
