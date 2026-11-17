from __future__ import annotations

from fastapi.testclient import TestClient

from codingagent.api.app import create_app


def _client(settings, tmp_path) -> TestClient:
    local = settings.model_copy(deep=True)
    local.eval.db_path = tmp_path / "api-runs.db"
    return TestClient(create_app(local))


def test_health(settings, tmp_path):
    response = _client(settings, tmp_path).get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dataset_problems"] == 40


def test_list_problems_hides_correct_code(settings, tmp_path):
    client = _client(settings, tmp_path)
    response = client.get("/problems")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 40
    assert "correct_code" not in payload[0]


def test_problem_detail(settings, tmp_path):
    client = _client(settings, tmp_path)
    response = client.get("/problems/bitcount")
    assert response.status_code == 200
    body = response.json()
    assert body["buggy_code"]
    assert "correct_code" not in body


def test_missing_problem_returns_404(settings, tmp_path):
    client = _client(settings, tmp_path)
    assert client.get("/problems/nope").status_code == 404


def test_empty_metrics(settings, tmp_path):
    client = _client(settings, tmp_path)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.json() == {}
