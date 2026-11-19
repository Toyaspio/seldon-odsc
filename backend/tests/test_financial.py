from datetime import date
from decimal import Decimal
import pytest
from backend.financial.validator import convert_unit, percentage, validate_period, comparable, free_cash_flow, FinancialValidationError


def test_financial_rules():
    assert convert_unit(Decimal('1.5'), 'billions', 'millions') == 1500
    assert percentage(Decimal(1), Decimal(4)) == 25
    assert free_cash_flow(Decimal(100), Decimal(30)) == 70
    validate_period(date(2025, 1, 1), date(2025, 3, 31), 'quarter')
    with pytest.raises(FinancialValidationError):
        validate_period(date(2025, 1, 1), date(2025, 3, 31), 'year')
    assert not comparable({'company_id': 'a'}, {'company_id': 'b'})
