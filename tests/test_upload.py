"""Загрузка своей выгрузки: чужая схема отклоняется с 400, активная выгрузка не меняется."""
import io
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client():
    if not (ROOT / "out" / "graph.pkl").exists():
        pytest.skip("нет out/graph.pkl: сначала python pipeline.py")
    from fastapi.testclient import TestClient
    import server
    return TestClient(server.app)


def pq(df):
    b = io.BytesIO()
    df.to_parquet(b, index=False)
    return b.getvalue()


def test_rejects_extra_client_attributes(client):
    nodes = pd.read_parquet(ROOT / "data" / "nodes.parquet").assign(fio="x")
    files = [("files", ("nodes.parquet", pq(nodes))),
             ("files", ("edges.parquet", (ROOT / "data" / "edges.parquet").read_bytes())),
             ("files", ("transactions.parquet", (ROOT / "data" / "transactions.parquet").read_bytes()))]
    r = client.post("/api/upload", files=files)
    assert r.status_code == 400 and "колонки" in r.json()["detail"]
    assert client.get("/api/dataset").json()["uploaded"] is False


def test_rejects_missing_files(client):
    r = client.post("/api/upload", files=[("files", ("nodes.parquet", (ROOT / "data" / "nodes.parquet").read_bytes()))])
    assert r.status_code == 400 and "Не хватает" in r.json()["detail"]
    assert client.get("/api/summary").json()["nodes"] == len(pd.read_parquet(ROOT / "data" / "nodes.parquet"))
