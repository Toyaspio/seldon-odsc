"""Approved cash investing concepts. A scope is part of a metric's identity."""
from datetime import date

CANONICAL_CAPEX = 'Cash Capital Expenditures'
CAPEX_SCOPES = {
    'ppe': 'Cash purchases of property, plant and equipment, including capital improvements where reported.',
    'productive_assets': 'Cash purchases of PP&E, software and other intangible assets.',
    'ppe_excluding_campus_land': 'Cash purchases of property and equipment excluding land-use rights and campus construction.',
    'ppe_and_land_use': 'Cash purchases of land-use rights, property and equipment.',
    'ppe_excluding_leased_equipment': 'Cash additions to fixed assets excluding equipment leased to others.',
    'other_long_term_investing_assets': 'Shell consolidated investing cash-flow capital expenditure.',
}
STANDARD_CAPEX = {
    ('us-gaap', 'PaymentsToAcquirePropertyPlantAndEquipment'): 'ppe',
    ('us-gaap', 'PaymentsToAcquireProductiveAssets'): 'productive_assets',
    ('ifrs-full', 'PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'): 'ppe',
}

# Amazon's cash-flow statement calls this broader standard tag “Purchases of
# property and equipment”. Prefer that narrower perimeter only for reviewed filings.
AMAZON_PPE_FILINGS = {'0001018724-26-000004', '0001018724-26-000040'}

# None of these partial and noncash concepts alone establishes a consolidated total.
UNSUPPORTED_CAPEX_TAGS = {
    'PaymentsToAcquireOtherPropertyPlantAndEquipment': 'Other PP&E component; coverage of total PP&E is unproven.',
    'PaymentsToAcquireOtherProductiveAssets': 'Other productive assets may be a component of the reported total.',
    'PaymentsForCapitalImprovements': 'Investing or in-use property purchases and improvements; not proof of total consolidated cash CapEx.',
    'CapitalExpendituresIncurredButNotYetPaid': 'Unpaid amount; not an investing cash outflow.',
    'PaymentsToAcquireIntangibleAssets': 'Intangible component alone does not establish total cash CapEx.',
}


def standard_capex_scope(cik: str, taxonomy: str, tag: str, accession: str | None) -> str:
    scope = STANDARD_CAPEX[taxonomy, tag]
    if cik == '0001018724' and accession in AMAZON_PPE_FILINGS and scope == 'productive_assets':
        return 'ppe'
    return scope


def capex_duration(start, end, form: str) -> str | None:
    try:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    except (TypeError, ValueError):
        return None
    if 360 <= days <= 371 and form in {'10-K', '10-K/A', '20-F', '20-F/A', '40-F', '40-F/A', 'ESEF annual report'}:
        return 'year'
    if 80 <= days <= 100:
        return 'quarter'
    if 170 <= days <= 195 or 255 <= days <= 292:
        return 'ytd'
    return None
