"""Пайплайн на другом датасете той же схемы (подграф data/): отрабатывает, self-check OK, 3 CSV на месте."""
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_subset import ROOT, make_subset  # noqa: E402


@pytest.mark.parametrize("mode", ["seeds", "half"])
def test_pipeline_on_subset(tmp_path, mode):
    if not (ROOT / "data" / "nodes.parquet").exists():
        pytest.skip("нет data/")
    data, out = tmp_path / "data", tmp_path / "out"
    info = make_subset(data, mode)
    source = pd.read_parquet(data / "nodes.parquet")
    assert info["nodes"] == len(source) < len(pd.read_parquet(ROOT / "data" / "nodes.parquet")) or mode == "half"

    r = subprocess.run([sys.executable, "pipeline.py", "--data", str(data), "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "самопроверка: OK" in r.stdout

    nr = pd.read_csv(out / "nodes_roles.csv", dtype={"gid": "int64"})
    ct = pd.read_csv(out / "clusters.csv")
    tn = pd.read_csv(out / "top_nodes.csv", dtype={"gid": "int64"})
    assert len(nr) == len(source) and set(nr.gid) == set(source.gid)
    assert set(nr.cluster_id) == set(ct.cluster_id)
    assert len(tn) >= 20 and set(tn.gid) <= set(source.gid)
