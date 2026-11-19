from abc import ABC, abstractmethod


class ModelProvider(ABC):
    @abstractmethod
    def plan(self, query: str) -> dict: ...

class SearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, timeout_seconds: float) -> list[dict]: ...


class FinancialDataProvider(ABC):
    @abstractmethod
    def facts(self, company_id: str, as_of_date: str | None = None) -> list[dict]: ...


class MockModelProvider(ModelProvider):
    def plan(self, query: str) -> dict:
        return {'query': query, 'mode': 'mock'}


class MockSearchProvider(SearchProvider):
    def search(self, query: str, timeout_seconds: float) -> list[dict]:
        return []


class MockFinancialDataProvider(FinancialDataProvider):
    def facts(self, company_id: str, as_of_date: str | None = None) -> list[dict]:
        return []


from backend.providers.sec import SecFinancialDataProvider
from backend.providers.live import NebiusModelProvider, TavilySearchProvider
