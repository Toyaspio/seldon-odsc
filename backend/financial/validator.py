from datetime import date, timedelta
from decimal import Decimal

from backend.agent.models import Claim, Metric


class FinancialValidationError(ValueError):
    pass


def convert_unit(value: Decimal, source: str, target: str) -> Decimal:
    factors = {'ones': Decimal(1), 'thousands': Decimal(1000),
               'millions': Decimal(1000000), 'billions': Decimal(1000000000)}
    if source not in factors or target not in factors:
        raise FinancialValidationError('Unsupported unit')
    return value * factors[source] / factors[target]


def percentage(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        raise FinancialValidationError('Zero denominator')
    return numerator / denominator * 100


def validate_period(start: date, end: date, fiscal_period: str) -> None:
    if start > end:
        raise FinancialValidationError('Period start after end')
    days = (end - start).days + 1
    if fiscal_period == 'quarter' and not 80 <= days <= 100:
        raise FinancialValidationError('Quarter period mismatch')
    if fiscal_period == 'year' and not 360 <= days <= 371:
        raise FinancialValidationError('Year period mismatch')


def comparable(left: dict, right: dict) -> bool:
    keys = ('company_id', 'unit', 'currency', 'period_start', 'period_end',
            'fiscal_period', 'accounting_basis', 'actual_or_guidance')
    return all(left.get(key) == right.get(key) for key in keys)


def free_cash_flow(operating_cash_flow: Decimal, cash_capex: Decimal) -> Decimal:
    return operating_cash_flow - cash_capex


def period_kind(metric: Metric) -> str:
    if metric.duration_kind:
        return metric.duration_kind
    if not metric.period_start:
        return 'instant'
    days = (date.fromisoformat(metric.period_end) - date.fromisoformat(metric.period_start)).days + 1
    if 80 <= days <= 100:
        return 'quarter'
    if 360 <= days <= 371:
        return 'year'
    if 160 <= days <= 300:
        return 'ytd'
    return 'other'


def comparable_periods(current: Metric, previous: Metric, comparison: str) -> bool:
    keys = ('company_id', 'metric_name', 'currency', 'unit', 'accounting_basis', 'actual_or_guidance')
    if any(getattr(current, k) != getattr(previous, k) for k in keys):
        return False
    if current.capex_scope != previous.capex_scope:
        return False
    if current.actual_or_guidance != 'actual' or not current.period_start or not previous.period_start:
        return False
    current_start, previous_start = map(date.fromisoformat, (current.period_start, previous.period_start))
    current_end, previous_end = map(date.fromisoformat, (current.period_end, previous.period_end))
    if comparison == 'qoq':
        return (period_kind(current) == period_kind(previous) == 'quarter'
                and current_start == previous_end + timedelta(days=1))
    if comparison in {'yoy', 'annual'}:
        expected = 'quarter' if comparison == 'yoy' else 'year'
        return (period_kind(current) == period_kind(previous) == expected
                and 350 <= (current_start - previous_start).days <= 380
                and 350 <= (current_end - previous_end).days <= 380)
    return False


def calculated(metric: Metric, name: str, value: Decimal, inputs: list[Metric],
               unit: str | None = None, start: str | None = None) -> Metric:
    result_unit = unit or metric.unit
    return metric.model_copy(update={
        'metric_id': name + ':' + ':'.join(item.metric_id for item in inputs),
        'metric_name': name, 'value': float(value), 'unit': result_unit,
        'currency': metric.currency if result_unit != '%' else None,
        'period_start': start or metric.period_start,
        'evidence_ids': list(dict.fromkeys(eid for item in inputs for eid in item.evidence_ids)),
    })


class FinancialValidator:
    def calculate(self, facts: list[Metric], comparison: str, *, claims: list[Claim] | None = None) -> list[Metric]:
        conflicted_capex = {
            (claim.company_id, claim.unit, claim.accounting_basis, claim.period_start, claim.period_end, claim.capex_scope)
            for claim in claims or []
            if claim.metric_name == 'cash_capex' and claim.verification_status == 'contradicted'
        }
        valid = [metric for metric in facts if metric.actual_or_guidance == 'actual']
        series = {}
        for metric in valid:
            key = (metric.company_id, metric.metric_name, metric.unit, metric.currency,
                   metric.accounting_basis, metric.period_start, metric.capex_scope)
            series.setdefault(key, []).append(metric)

        quarters = []
        for group in series.values():
            for current in group:
                if (current.metric_name not in {'cash_capex', 'operating_cash_flow', 'revenue'}
                        or period_kind(current) not in {'ytd', 'year'}):
                    continue
                candidates = [p for p in group if
                    80 <= (date.fromisoformat(current.period_end) - date.fromisoformat(p.period_end)).days <= 100
                    and (current.value >= p.value or current.metric_name == 'operating_cash_flow')]
                if len(candidates) != 1:
                    continue
                prior = candidates[0]
                if period_kind(prior) not in {'quarter', 'ytd'}:
                    continue
                start = (date.fromisoformat(prior.period_end) + timedelta(days=1)).isoformat()
                capex_period = (current.company_id, current.unit, current.accounting_basis,
                                start, current.period_end, current.capex_scope)
                if current.metric_name == 'cash_capex' and capex_period in conflicted_capex:
                    continue
                if any(item.company_id == current.company_id and item.metric_name == current.metric_name
                       and item.unit == current.unit and item.accounting_basis == current.accounting_basis
                       and (current.metric_name != 'cash_capex' or (item.currency == current.currency
                            and item.capex_scope == current.capex_scope))
                       and item.period_start == start and item.period_end == current.period_end for item in valid):
                    continue
                value = Decimal(str(current.value)) - Decimal(str(prior.value))
                quarter_label = current.fiscal_period.removesuffix(' YTD')
                if quarter_label == current.fiscal_period:
                    quarter_label = 'standalone quarter (derived)'
                metadata = {'fiscal_period': quarter_label, 'duration_kind': 'quarter'}
                if current.metric_name == 'cash_capex':
                    metadata['calculation_method'] = 'quarter_difference'
                quarters.append(calculated(current, current.metric_name, value, [current, prior], start=start)
                                .model_copy(update=metadata))

        inputs = valid + quarters
        derived = list(quarters)
        for cash_flow in inputs:
            if cash_flow.metric_name != 'operating_cash_flow' or period_kind(cash_flow) not in {'quarter', 'year', 'ytd'}:
                continue
            keys = ('company_id', 'unit', 'currency', 'period_start', 'period_end',
                    'accounting_basis', 'actual_or_guidance')
            capex = [metric for metric in inputs if metric.metric_name == 'cash_capex' and metric.value >= 0
                     and all(getattr(metric, key) == getattr(cash_flow, key) for key in keys)]
            scopes = {}
            for expenditure in capex:
                if period_kind(expenditure) == period_kind(cash_flow):
                    scopes.setdefault(expenditure.capex_scope, []).append(expenditure)
            for scope, matches in scopes.items():
                if len(matches) != 1:
                    continue
                expenditure, = matches
                value = free_cash_flow(Decimal(str(cash_flow.value)), Decimal(str(expenditure.value)))
                derived.append(calculated(cash_flow, 'free_cash_flow', value, [cash_flow, expenditure])
                               .model_copy(update={'capex_scope': scope, 'duration_kind': period_kind(cash_flow),
                                                   'calculation_method': 'operating_cash_flow_less_capex'}))

        if comparison in {'qoq', 'yoy', 'annual'}:
            comparable_inputs = valid + derived
            for current in comparable_inputs:
                candidates = [p for p in comparable_inputs if comparable_periods(current, p, comparison)]
                if len(candidates) != 1 or candidates[0].value <= 0:
                    continue
                previous = candidates[0]
                base = Decimal(str(previous.value))
                change = percentage(Decimal(str(current.value)) - base, base)
                growth = calculated(current, f'{current.metric_name}_{comparison}_growth',
                                    change, [current, previous], unit='%')
                if current.metric_name in {'cash_capex', 'free_cash_flow'}:
                    growth = growth.model_copy(update={'calculation_method': 'growth'})
                derived.append(growth)
        return derived
