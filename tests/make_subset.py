#!/usr/bin/env python3
"""
«Другой датасет» той же схемы для проверки обобщения: из data/ берётся подграф и сохраняется
в edges/nodes/transactions.parquet (те же колонки и типы).

  python tests/make_subset.py --out tmp/subset_seeds --mode seeds   # 40 первых seed и всё, что от них достижимо
  python tests/make_subset.py --out tmp/subset_half  --mode half    # только транзакции первой половины периода
"""
from __future__ import annotations

import argparse
from pathlib import Path

import networkx as nx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def make_subset(out: Path, mode: str = "seeds", data: Path = ROOT / "data", n_seeds: int = 40) -> dict:
    edges = pd.read_parquet(data / "edges.parquet")
    nodes = pd.read_parquet(data / "nodes.parquet")
    tx = pd.read_parquet(data / "transactions.parquet")
    if mode == "seeds":
        G = nx.DiGraph()
        G.add_nodes_from(nodes.gid)
        G.add_edges_from(zip(edges.src, edges.dst))
        seeds = sorted(nodes.loc[nodes.is_seed, "gid"])[:n_seeds]
        keep = set(seeds).union(*(nx.descendants(G, s) for s in seeds))
        nodes = nodes[nodes.gid.isin(keep)]
        edges = edges[edges.src.isin(keep) & edges.dst.isin(keep)]
    elif mode == "half":
        d = pd.to_datetime(tx.date)
        mid = d.min() + (d.max() - d.min()) / 2
        tx = tx[d <= mid]
        # рёбра пересчитываются из оставшихся транзакций; колено ребра сохраняется
        agg = tx.groupby(["src", "dst"]).agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size")).reset_index()
        edges = agg.merge(edges[["src", "dst", "depth"]], on=["src", "dst"])[list(edges.columns)].astype(edges.dtypes)
    else:
        raise ValueError(mode)
    pairs = set(zip(edges.src, edges.dst))
    tx = tx[[p in pairs for p in zip(tx.src, tx.dst)]]
    out.mkdir(parents=True, exist_ok=True)
    edges.reset_index(drop=True).to_parquet(out / "edges.parquet", index=False)
    nodes.reset_index(drop=True).to_parquet(out / "nodes.parquet", index=False)
    tx.reset_index(drop=True).to_parquet(out / "transactions.parquet", index=False)
    return {"nodes": len(nodes), "edges": len(edges), "transactions": len(tx), "seeds": int(nodes.is_seed.sum())}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["seeds", "half"], default="seeds")
    a = ap.parse_args()
    print(make_subset(Path(a.out), a.mode))
