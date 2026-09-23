"""Eight contracts for generated HackAlem artifacts; no pipeline imports."""
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
ROLES = {"consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"}


@pytest.fixture(scope="module")
def artifacts():
    out = ROOT / "out"
    if not out.is_dir():
        pytest.skip("out/ отсутствует: сначала запустите python pipeline.py")
    # An existing but incomplete output directory is an error, not a skip.
    return {
        "nodes": pd.read_csv(out / "nodes_roles.csv", dtype={"gid": "int64"}),
        "clusters": pd.read_csv(out / "clusters.csv"),
        "top": pd.read_csv(out / "top_nodes.csv", dtype={"gid": "int64"}),
        "source": pd.read_parquet(ROOT / "data" / "nodes.parquet"),
    }


def assert_nonempty(series):
    assert series.notna().all(), f"{series.name}: есть пропуски"
    assert series.astype(str).str.strip().ne("").all(), f"{series.name}: есть пустые строки"


def test_node_identifiers(artifacts):
    nodes, source = artifacts["nodes"], artifacts["source"]
    assert len(nodes) == 2248
    assert nodes.gid.nunique() == 2248
    assert nodes.gid.dtype == "int64"
    assert set(nodes.gid) == set(source.gid)


def test_role_vocabulary(artifacts):
    assert artifacts["nodes"].role.isin(ROLES).all()


def test_required_values_and_evidence(artifacts):
    nodes = artifacts["nodes"]
    for column in ("role", "role_score", "cluster_id", "priority_score", "evidence"):
        assert column in nodes.columns
        assert_nonempty(nodes[column])
    evidence = nodes.evidence.astype(str)
    assert evidence.str.len().le(200).all()
    missing_digits = nodes.loc[~evidence.str.contains(r"\d", regex=True), "gid"].tolist()
    assert not missing_digits, f"evidence без цифр у gid: {missing_digits[:20]}"


def test_score_ranges(artifacts):
    for column in ("role_score", "priority_score"):
        assert artifacts["nodes"][column].between(0, 1).all(), column


def test_cluster_contract(artifacts):
    nodes, clusters = artifacts["nodes"], artifacts["clusters"]
    required = {"cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"}
    assert required <= set(clusters.columns)
    assert clusters.cluster_id.notna().all()
    assert clusters.cluster_id.is_unique
    assert set(nodes.cluster_id) == set(clusters.cluster_id)
    assert clusters.n_nodes.notna().all()
    assert clusters.n_nodes.sum() == 2248
    actual_counts = nodes.groupby("cluster_id").size().sort_index()
    reported_counts = clusters.set_index("cluster_id").n_nodes.sort_index()
    assert actual_counts.to_dict() == reported_counts.to_dict()


def test_top_nodes_contract(artifacts):
    top = artifacts["top"]
    assert {"rank", "gid", "role", "priority_score", "why"} <= set(top.columns)
    assert len(top) >= 20
    assert top.gid.dtype == "int64"
    assert top.gid.is_unique
    assert set(top.gid) <= set(artifacts["nodes"].gid)
    assert top.role.isin(ROLES).all()
    assert top.priority_score.between(0, 1).all()
    assert top.priority_score.is_monotonic_decreasing
    assert_nonempty(top.why)


def test_depth_four_is_not_observed_sink(artifacts):
    nodes, source = artifacts["nodes"], artifacts["source"]
    assert "sub_role" in nodes.columns
    boundary_ids = source.loc[source.depth.eq(4), "gid"]
    assert not nodes.loc[nodes.gid.isin(boundary_ids), "sub_role"].eq("observed_sink").any()


def test_seeds_are_not_transit(artifacts):
    nodes, source = artifacts["nodes"], artifacts["source"]
    seed_ids = source.loc[source.is_seed.eq(True), "gid"]
    assert not nodes.loc[nodes.gid.isin(seed_ids), "role"].eq("transit").any()
