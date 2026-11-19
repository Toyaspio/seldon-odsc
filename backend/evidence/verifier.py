from decimal import Decimal

from backend.agent.models import Claim, Metric, Source


def fact_text(name: str, metric: str, value: float, unit: str, start: str | None, end: str) -> str:
    period = f'from {start} to {end}' if start else f'as of {end}'
    return f'{name} disclosed {metric.replace("_", " ")} of {Decimal(str(value)):f} {unit} {period}.'


def qualify_claim(claim: Claim, *, company_name: str | None = None) -> Claim:
    if claim.verification_status == 'supported':
        return claim
    conflict = claim.verification_status == 'contradicted' or any(
        note.startswith('Conflicting values') for note in claim.limitations)
    prefix = 'Evidence conflict' if conflict else 'Unverified'
    if (claim.claim_type == 'evidence_gap' and claim.numeric_value is None
            and claim.text.startswith(f'{prefix} — ')
            and claim.verification_status == ('contradicted' if conflict else 'insufficient')):
        return claim
    if conflict or claim.claim_type == 'disclosed_fact' or claim.numeric_value is not None:
        subject = (claim.metric_name or 'the proposed figure').replace('_', ' ')
        period = (f' from {claim.period_start} to {claim.period_end}' if claim.period_start and claim.period_end
                  else f' as of {claim.period_end}' if claim.period_end else '')
        issuer = f' for {company_name}' if company_name else ''
        explanation = ('The cited evidence does not establish a single value. Review the source records.'
                       if conflict else 'Available evidence does not establish a value.')
        text = f'{prefix} — {subject}{issuer}{period}. {explanation}'
        return claim.model_copy(update={'text': text, 'numeric_value': None, 'actual_or_guidance': None,
            'claim_type': 'evidence_gap', 'verification_status': 'contradicted' if conflict else 'insufficient'})
    if not claim.text.startswith('Unverified — '):
        return claim.model_copy(update={'text': f'Unverified — {claim.text}'})
    return claim


def verify_claim(claim: Claim, sources: list[Source]) -> Claim:
    by_id = {source.source_id: source for source in sources}
    identity = ('company_id', 'metric_name', 'unit', 'period_end')
    if (not claim.evidence_ids or any(getattr(claim, field) is None for field in identity)
            or claim.numeric_value is None or any(eid not in by_id for eid in claim.evidence_ids)):
        return qualify_claim(claim.model_copy(update={'verification_status': 'insufficient'}))

    def grounded(source: Source) -> bool:
        if not source.publication_date or not source.url.startswith('https://') or source.numeric_value is None:
            return False
        if any(getattr(claim, field) != getattr(source, field) for field in identity):
            return False
        if source.source_type in {'sec_xbrl', 'esef_xbrl', 'sec_statement'}:
            return (bool(source.structured_locator) and source.period_start == claim.period_start
                    and source.accounting_basis == claim.accounting_basis
                    and source.capex_scope == claim.capex_scope
                    and source.actual_or_guidance == claim.actual_or_guidance
                    and claim.claim_type == 'disclosed_fact')
        # Older structured filing evidence may only support an exact quotation.
        return source.source_type == 'filing' and source.quoted_text == claim.text

    cited = [by_id[eid] for eid in claim.evidence_ids]
    if not all(grounded(source) for source in cited):
        return qualify_claim(claim.model_copy(update={'verification_status': 'insufficient'}))
    value = Decimal(str(claim.numeric_value))
    if any(Decimal(str(source.numeric_value)) != value for source in cited):
        return qualify_claim(claim.model_copy(update={'verification_status': 'contradicted'}))
    if any(source.source_type in {'sec_xbrl', 'esef_xbrl', 'sec_statement'} and
           (not source.issuer_name or claim.text != fact_text(source.issuer_name, claim.metric_name,
                claim.numeric_value, claim.unit, claim.period_start, claim.period_end)) for source in cited):
        return qualify_claim(claim.model_copy(update={'verification_status': 'insufficient'}))
    conflicts = [source.source_id for source in sources if grounded(source)
                 and Decimal(str(source.numeric_value)) != value]
    if conflicts:
        return qualify_claim(claim.model_copy(update={'verification_status': 'contradicted',
            'limitations': [*claim.limitations, 'Conflicting values for the same measure and period.'],
            'evidence_ids': list(dict.fromkeys(claim.evidence_ids + conflicts))}),
            company_name=next((source.issuer_name for source in cited if source.issuer_name), None))
    return claim.model_copy(update={'verification_status': 'supported'})


class EvidenceVerifier:
    verify = staticmethod(verify_claim)

    def audit_facts(self, metrics: list[Metric], sources: list[Source],
                    names: dict[str, str]) -> tuple[list[Metric], list[Claim]]:
        audited, claims = [], []
        for metric in metrics:
            text = fact_text(names[metric.company_id], metric.metric_name, metric.value, metric.unit,
                             metric.period_start, metric.period_end)
            claim = Claim(claim_id=metric.metric_id, text=text, verification_status='insufficient',
                          evidence_ids=metric.evidence_ids, company_id=metric.company_id,
                          metric_name=metric.metric_name, numeric_value=metric.value, unit=metric.unit,
                          period_start=metric.period_start, period_end=metric.period_end,
                          actual_or_guidance=metric.actual_or_guidance,
                          accounting_basis=metric.accounting_basis, capex_scope=metric.capex_scope,
                          claim_type='disclosed_fact')
            checked = self.verify(claim, sources)
            claims.append(checked)
            if checked.verification_status == 'supported':
                audited.append(metric)
        return audited, claims
