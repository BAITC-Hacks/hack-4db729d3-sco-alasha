"""Контракты дополнительных сигналов, запросов данных и доступности через API/tools."""
import asyncio
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import THRESHOLDS, anomaly_features, data_requests

EXPECTED_ROLES = {"coordinator": 11, "consolidator": 60, "distributor": 63,
                  "transit": 69, "terminal": 431, "peripheral": 1614}
EXTRA_COLUMNS = {"anomaly_score", "anomaly_flag", "anomaly_reason", "structuring_flag", "small_tx_share"}


@pytest.fixture(scope="module")
def nodes():
    return pd.read_csv(ROOT / "out/nodes_roles.csv", dtype={"gid": "int64"})


@pytest.fixture(scope="module")
def requests():
    path = ROOT / "out/data_requests.csv"
    assert path.is_file(), "Сначала запустите python pipeline.py"
    return pd.read_csv(path, dtype={"gid": "int64"})


def test_request_contract(nodes, requests):
    assert list(requests.columns) == ["rank", "gid", "request", "reason", "priority_score"]
    assert not requests.empty
    assert set(requests.gid) <= set(nodes.gid)
    assert requests["rank"].tolist() == list(range(1, len(requests) + 1))
    assert requests.priority_score.is_monotonic_decreasing
    assert not requests.duplicated(["gid", "request"]).any()
    for column in ("request", "reason"):
        assert requests[column].notna().all()
        assert requests[column].str.strip().ne("").all()
    assert requests.priority_score.eq(requests.gid.map(nodes.set_index("gid").priority_score)).all()


def test_anomaly_contract(nodes):
    assert EXTRA_COLUMNS <= set(nodes.columns)
    active = (nodes.in_deg + nodes.out_deg) > 0
    assert 0.02 <= nodes.loc[active, "anomaly_flag"].mean() <= 0.04
    assert nodes.anomaly_score.between(0, 1).all()
    assert not nodes.loc[~active, "anomaly_flag"].any()
    assert nodes.loc[~active, "anomaly_score"].eq(0).all()
    assert nodes.loc[nodes.anomaly_flag, "anomaly_reason"].fillna("").str.contains("своего колена").all()
    assert nodes.loc[~nodes.anomaly_flag, "anomaly_reason"].fillna("").eq("").all()
    assert nodes.loc[nodes.anomaly_flag, "anomaly_score"].min() >= nodes.loc[~nodes.anomaly_flag, "anomaly_score"].max()


def test_structuring_matches_transactions(nodes):
    tx = pd.read_parquet(ROOT / "data/transactions.parquet")
    expected = tx.sum_kzt.ge(5_000).mul(tx.sum_kzt.lt(10_000)).groupby(tx.dst).mean()
    np.testing.assert_allclose(nodes.small_tx_share, nodes.gid.map(expected).fillna(0))
    assert nodes.loc[nodes.structuring_flag, "in_tx"].ge(5).all()
    assert nodes.structuring_flag.eq(nodes.in_tx.ge(5) & nodes.small_tx_share.ge(0.7)).all()


def test_roles_unchanged(nodes):
    assert nodes.role.value_counts().to_dict() == EXPECTED_ROLES


def test_extras_metadata(nodes, requests):
    extras = json.loads((ROOT / "out/run_meta.json").read_text(encoding="utf-8"))["extras"]
    assert extras["anomaly_nodes"] == int(nodes.anomaly_flag.sum())
    assert extras["structuring_nodes"] == int(nodes.structuring_flag.sum())
    assert extras["active_nodes"] == int((nodes.in_deg + nodes.out_deg).gt(0).sum())
    assert extras["data_requests"] == len(requests)
    assert extras["data_request_nodes"] == requests.gid.nunique()


def test_request_rule_boundaries():
    base = {"depth": 2, "in_deg": 1, "p_forward": 0.0, "in_kzt": 100_000, "out_kzt": 0,
            "is_seed": False, "structuring_flag": False, "small_tx_share": 0.0, "in_tx": 1,
            "role": "peripheral", "pass_through": 0.5, "priority_score": 0.5}
    cases = [
        {"depth": 4, "p_forward": 0.35},
        {"depth": 4, "p_forward": 0.34, "in_kzt": 500_000},
        {"depth": 4, "p_forward": 0.34, "in_kzt": 499_999},
        {"is_seed": True, "out_kzt": 500_000},
        {"is_seed": True, "out_kzt": 499_999},
        {"structuring_flag": True, "small_tx_share": 0.7, "in_tx": 10},
        {"role": "consolidator", "pass_through": 0.29},
        {"role": "coordinator", "pass_through": np.nan},
        {"role": "consolidator", "pass_through": 0.3},
        {"role": "consolidator", "pass_through": 0.0, "is_seed": True},
        {"role": "coordinator", "pass_through": 0.0, "depth": 4},
        {"depth": 4, "p_forward": 1, "in_deg": 0},
        {"depth": 4, "p_forward": 0.5, "structuring_flag": True, "in_tx": 10, "small_tx_share": 0.8},
    ]
    df = pd.DataFrame([{**base, **case} for case in cases])
    result = data_requests(df, THRESHOLDS)
    assert result.groupby("gid").size().to_dict() == {0: 1, 1: 1, 3: 1, 5: 1, 6: 1, 7: 1, 12: 2}
    assert "неизвестна" in result.loc[result.gid == 7, "reason"].iloc[0]
    assert list(data_requests(df.iloc[0:0], THRESHOLDS).columns) == list(result.columns)


def test_structuring_thresholds_and_constant_depths():
    df = pd.DataFrame({"depth": [1, 2, 3, 4], "in_tx": [10, 4, 10, 0], "in_deg": [1, 1, 1, 0],
                       "out_deg": [0, 0, 0, 0], "in_kzt": [1, 1, 1, 0], "out_kzt": 0,
                       "avg_tx_in": 1, "fast_transit_share": 0, "max_payers_same_day": 1, "cycles": 0})
    tx = pd.DataFrame({"dst": [0] * 10 + [1] * 4 + [2] * 10,
                       "sum_kzt": [5_000] * 6 + [9_999, 10_000, 4_999, 20_000] + [5_000] * 4 + [5_000] * 6 + [10_000] * 4})
    result = anomaly_features(df.copy(), tx, THRESHOLDS)
    assert result.small_tx_share.tolist() == [0.7, 1.0, 0.6, 0.0]
    assert result.structuring_flag.tolist() == [True, False, False, False]
    assert np.isfinite(result.anomaly_score).all()
    pd.testing.assert_frame_equal(result[df.columns], df)
    isolated = df.iloc[[3]].copy()
    result = anomaly_features(isolated, tx.iloc[0:0], THRESHOLDS)
    assert not result.anomaly_flag.any()
    assert result.anomaly_score.eq(0).all()


def test_data_gaps_tools_and_cards(nodes, requests):
    import graph_tools as T
    import agent
    import mcp_server

    assert len(T.TOOLS) == 9
    assert set(T.TOOLS) == {s["name"] for s in agent.FALLBACK_SCHEMAS}
    assert set(T.TOOLS) == {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert T.data_gaps(0) == []
    assert T.data_gaps(2) == requests.head(2).to_dict("records")
    flagged = nodes[nodes.anomaly_flag | nodes.structuring_flag]
    for row in flagged.itertuples():
        info, card = T.node_info(row.gid), T.node_card(row.gid)
        assert EXTRA_COLUMNS <= set(info)
        if row.structuring_flag:
            assert "признаки дробления у порога" in card
        if row.anomaly_flag:
            assert row.anomaly_reason in card
    for gid in requests.gid.head(3):
        assert "Рекомендуемый запрос данных:" in T.node_card(gid)


def test_data_requests_api_preserves_gid(requests):
    from fastapi.testclient import TestClient
    from server import app

    with TestClient(app) as client:
        response = client.get("/api/data_requests?n=12")
        assert response.status_code == 200
        rows = response.json()
        assert [r["gid"] for r in rows] == requests.gid.head(12).astype(str).tolist()
        assert client.get("/api/data_requests?n=0").json() == []
        assert client.get("/api/data_requests?n=-1").status_code == 422
        assert client.get("/api/download/data_requests.csv").status_code == 200
