from fastapi.testclient import TestClient

from chatbot_api.main import app as chatbot_app
from ingestion_api.main import app as ingestion_app


def test_chatbot_api_healthz() -> None:
    response = TestClient(chatbot_app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ingestion_api_healthz() -> None:
    response = TestClient(ingestion_app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
