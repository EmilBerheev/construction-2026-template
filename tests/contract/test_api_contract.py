"""Контрактный тест: Schemathesis генерирует запросы по OpenAPI и сверяет ответы со схемой.

Запуск только этих тестов: uv run pytest -m contract
"""

import pytest
import schemathesis
from fastapi import FastAPI
from hypothesis import HealthCheck, settings
from schemathesis.checks import allow_header_conformance, positive_data_acceptance
from schemathesis.specs.openapi.schemas import OpenApiSchema

# Две проверки Schemathesis отключены, остальные работают:
# - allow_header_conformance: Starlette на неподдерживаемый метод отвечает 405, но в заголовке
#   Allow перечисляет методы только одного маршрута (особенность маршрутизатора);
# - positive_data_acceptance: API вправе ответить 422 на данные, корректные по схеме, если они
#   нарушают бизнес-правило (например, бронь вне рабочего времени). В JSON Schema такое не выразить.
EXCLUDED_CHECKS = [allow_header_conformance, positive_data_acceptance]


@pytest.fixture
def api_schema(app: FastAPI) -> OpenApiSchema:
    return schemathesis.openapi.from_asgi("/openapi.json", app)


schema = schemathesis.pytest.from_fixture("api_schema")


@pytest.mark.contract
@schema.parametrize()
@settings(max_examples=25, deadline=None, suppress_health_check=list(HealthCheck))
def test_api_matches_openapi(case: schemathesis.Case) -> None:
    case.call_and_validate(excluded_checks=EXCLUDED_CHECKS)
