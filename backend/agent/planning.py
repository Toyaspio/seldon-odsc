import re
from typing import Annotated, Literal, get_args
from pydantic import BaseModel, Field, StringConstraints, field_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
MetricName = Literal['cash_capex', 'operating_cash_flow', 'free_cash_flow', 'revenue',
                     'noncash_lease_investment', 'remaining_performance_obligations',
                     'ai_capex', 'cloud_revenue', 'ai_revenue', 'ai_roi']
SourcePreference = Literal['filing', 'investor_relations', 'earnings_release', 'earnings_call', 'official_regulator']
PeriodComparison = Literal['latest', 'yoy', 'qoq', 'annual']
DEFAULT_METRICS = ('cash_capex', 'operating_cash_flow', 'free_cash_flow', 'revenue')


def _planning_hints(values, choices, fallback):
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return list(fallback)
    recognized = (item.strip().lower() for item in values if isinstance(item, str))
    return list(dict.fromkeys(item for item in recognized if item in choices)) or list(fallback)


class RetrievalTask(BaseModel):
    query: ShortText
    purpose: ShortText = 'Find relevant disclosures'


class ResearchPlan(BaseModel):
    companies: list[ShortText] = Field(default_factory=list, max_length=6)
    core_question: ShortText
    metrics: list[MetricName] = Field(default_factory=lambda: list(DEFAULT_METRICS), min_length=1, max_length=10)
    preferred_source_types: list[SourcePreference] = Field(default_factory=lambda: ['filing', 'investor_relations'],
                                                          min_length=1, max_length=5)
    retrieval_tasks: list[RetrievalTask] = Field(min_length=1, max_length=6)
    verification_questions: list[ShortText] = Field(default_factory=list, max_length=8)
    comparison: PeriodComparison = 'latest'
    investment_intent: bool = False

    @classmethod
    def from_model(cls, payload, question):
        """Keep independently usable planning hints and fill gaps from the question."""
        if not isinstance(payload, dict) or not payload:
            raise ValueError('No planning hints supplied')

        def text(value):
            return value.strip()[:300] if isinstance(value, str) else ''

        def entries(value):
            return value if isinstance(value, list) else [value]

        data = dict(payload)
        if not data.get('companies'):
            data['companies'] = data.get('issuers') or data.get('company') or data.get('issuer')
        for field, cap in (('companies', 6), ('verification_questions', 8)):
            if field in data:
                data[field] = [item for value in entries(data[field]) if (item := text(value))][:cap]
        fallback = text(question) or 'Research the requested topic'
        data['core_question'] = text(data.get('core_question')) or text(data.get('question')) or fallback
        tasks = []
        for entry in entries(data.get('retrieval_tasks') or data.get('search_queries')):
            query = text(entry.get('query')) if isinstance(entry, dict) else text(entry)
            if query:
                purpose = text(entry.get('purpose')) if isinstance(entry, dict) else ''
                tasks.append(RetrievalTask(query=query, **({'purpose': purpose} if purpose else {})))
            if len(tasks) == 6:
                break
        data['retrieval_tasks'] = tasks or [RetrievalTask(query=fallback)]
        return cls.model_validate(data)

    @field_validator('investment_intent', mode='before')
    @classmethod
    def normalize_investment_intent(cls, value):
        return value is True or (isinstance(value, str) and value.strip().lower() == 'true')

    @field_validator('metrics', mode='before')
    @classmethod
    def supported_metrics(cls, values):
        return _planning_hints(values, get_args(MetricName), DEFAULT_METRICS)

    @field_validator('preferred_source_types', mode='before')
    @classmethod
    def supported_source_types(cls, values):
        return _planning_hints(values, get_args(SourcePreference), ('filing', 'investor_relations'))

    @field_validator('comparison', mode='before')
    @classmethod
    def comparison_or_latest(cls, value):
        return _planning_hints(value, get_args(PeriodComparison), ('latest',))[0]


def requested_metrics(query: str) -> list[str]:
    query = query.casefold()
    terms = {
        'revenue': ('revenue', 'sales growth', '营收', '收入'),
        'cash_capex': ('capex', 'capital expenditure', '资本支出'),
        'operating_cash_flow': ('operating cash flow', '经营现金流'),
        'free_cash_flow': ('free cash flow', '自由现金流', 'fcf'),
        'ai_capex': ('ai infrastructure capex', 'ai infrastructure capital expenditure', 'ai capex',
                     'ai capital expenditure', 'ai 资本支出', 'ai 基础设施资本支出'),
        'cloud_revenue': ('cloud revenue', 'cloud business revenue', '云业务收入', '云收入'),
        'ai_revenue': ('ai revenue', 'ai 收入'),
        'ai_roi': ('ai roi', 'return on', 'pay off', '产生回报', 'ai 投资回报'),
        'remaining_performance_obligations': ('remaining performance obligation', 'rpo', '剩余履约义务'),
    }
    general_revenue = re.sub(r'\b(?:cloud(?: business)?|ai) revenue\b|云业务收入|云收入|ai 收入', '', query)
    return [name for name, triggers in terms.items() if any(
        re.search(rf'(?<![a-z]){re.escape(term)}(?![a-z])', general_revenue if name == 'revenue' else query) if term.isascii()
        else term in (general_revenue if name == 'revenue' else query) for term in triggers)]
