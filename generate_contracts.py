import json
from pathlib import Path
from backend.app import app
from backend.agent.models import Report

root = Path(__file__).parent / 'contracts'
(root / 'examples').mkdir(parents=True, exist_ok=True)
(root / 'openapi.yaml').write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
(root / 'report.schema.json').write_text(json.dumps(Report.model_json_schema(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
example = {
    'report_id': 'mock-report-001', 'task_id': 'mock-task-001',
    'query': 'Compare Microsoft, Alphabet, Amazon and Meta AI investment',
    'generated_at': '2026-11-14T00:00:00Z', 'data_as_of': None,
    'data_mode': 'mock',
    'companies': [{'company_id': name.lower(), 'company_name': name,
                   'ticker': None, 'exchange': None, 'country': None, 'cik': None,
                   'resolution_status': 'unverified'}
                  for name in ('Microsoft', 'Alphabet', 'Amazon', 'Meta')],
    'metrics': [{'metric_id': f'mock-capex-{i}', 'company_id': name.lower(),
                 'metric_name': 'synthetic_cash_capex', 'value': float(100 + i * 20),
                 'currency': 'USD', 'unit': 'millions', 'period_start': '2025-01-01',
                 'period_end': '2025-03-31', 'fiscal_period': '2025-Q1',
                 'accounting_basis': 'synthetic mock', 'actual_or_guidance': 'model_inference',
                 'evidence_ids': []}
                for i, name in enumerate(('Microsoft', 'Alphabet', 'Amazon', 'Meta'))],
    'claims': [{'claim_id': 'mock-claim-001', 'text': 'Illustrative comparison only',
                'verification_status': 'insufficient', 'evidence_ids': [],
                'limitations': ['Synthetic values have no supporting financial source.']}],
    'sources': [{'source_id': 'mock-source-001', 'title': 'Synthetic mock marker',
                 'publisher': 'Development fixture', 'url': 'https://example.invalid/mock',
                 'publication_date': None, 'retrieved_at': None,
                 'source_type': 'mock', 'quoted_text': None}],
    'limitations': ['All figures are fictional test values, not company disclosures or investment research.',
                    'No financial values or external evidence were retrieved.'],
    'status': 'partial',
}
Report.model_validate(example)
(root / 'examples' / 'research-report.mock.json').write_text(json.dumps(example, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
