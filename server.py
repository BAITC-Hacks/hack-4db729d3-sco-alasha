"""
Backend «Граф денег» (FastAPI).  REST API для веб-интерфейса + раздача статики web/.
Документация API: http://localhost:8000/docs

Запуск: python run.py  (или: uvicorn server:app --port 8000)
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import graph_tools as T

ROOT = Path(__file__).parent
OUT = ROOT / "out"
COLORS = {"coordinator": "#ef4444", "consolidator": "#f97316", "distributor": "#a855f7",
          "transit": "#3b82f6", "terminal": "#22c55e", "peripheral": "#64748b"}

app = FastAPI(title="Граф денег — AML API",
              description="Роли, кластеры, приоритеты и AI-аналитик по графу переводов. Все выводы — гипотезы для проверки.",
              version="1.0")


# ------------------------------------------------------------------ JSON: gid > 2^53 -> строка (иначе JS теряет точность)
def js(obj):
    if isinstance(obj, dict):
        return {str(k): js(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [js(v) for v in obj]
    if isinstance(obj, (np.integer, int)) and not isinstance(obj, bool):
        v = int(obj)
        return str(v) if abs(v) > 2 ** 53 else v
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if np.isnan(v) or np.isinf(v) else round(v, 6)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.strftime("%Y-%m-%d")
    return obj


def J(obj):
    return JSONResponse(js(obj))


def G():
    return T.G()


def DF():
    return T.DF()


def gid_of(x) -> int:
    try:
        g = int(str(x).strip())
    except ValueError:
        raise HTTPException(400, "gid должен быть числом")
    if g not in DF().index:
        raise HTTPException(404, f"gid {g} не найден")
    return g


def node_payload(g: int, extra: dict | None = None) -> dict:
    r = DF().loc[g]
    d = {"id": str(g), "label": str(g)[-6:], "role": r.role, "role_ru": T.ROLE_RU[r.role], "color": COLORS[r.role],
         "priority": float(r.priority_score), "score": float(r.role_score), "seed": bool(r.is_seed),
         "depth": int(r.depth), "cluster": int(r.cluster_id), "evidence": r.evidence, "sub_role": r.sub_role}
    if extra:
        d.update(extra)
    return d


def graph_payload(nodes: set) -> dict:
    H = G().subgraph(nodes)
    edges = [{"from": str(u), "to": str(v), "sum": d["sum_kzt"], "n_tx": d["n_tx"]} for u, v, d in H.edges(data=True)]
    return {"nodes": [node_payload(g) for g in H.nodes], "edges": edges}


# ------------------------------------------------------------------ API
@app.get("/api/summary", tags=["обзор"])
def summary():
    df = DF()
    meta = json.loads((OUT / "run_meta.json").read_text(encoding="utf-8"))
    return J({"nodes": len(df), "edges": G().number_of_edges(), "seeds": int(df.is_seed.sum()),
              "turnover": float(df.out_kzt.sum()), "roles": df.role.value_counts().to_dict(),
              "clusters": int(df.cluster_id.nunique()), "runtime_sec": meta["runtime_sec"],
              "colors": COLORS, "role_ru": T.ROLE_RU, "model": meta["forward_model"],
              "thresholds": meta["thresholds"], "role_weight": meta["role_weight"],
              "depth4": {"total": int((df.depth == 4).sum()),
                         "terminal": int(((df.depth == 4) & (df.role == "terminal")).sum())}})


@app.get("/api/top", tags=["обзор"])
def top(n: int = 30):
    t = pd.read_csv(OUT / "top_nodes.csv")
    rows = []
    for _, r in t.head(n).iterrows():
        rows.append({**node_payload(int(r.gid)), "rank": int(r["rank"]), "why": r.why})
    return J(rows)


@app.get("/api/search", tags=["обзор"])
def search(q: str = Query(..., min_length=2)):
    q = q.strip()
    ids = [g for g in DF().index if q in str(g)]
    ids = sorted(ids, key=lambda g: -DF().at[g, "priority_score"])[:12]
    return J([node_payload(g) for g in ids])


@app.get("/api/node/{gid}", tags=["узел"])
def node(gid: str):
    g = gid_of(gid)
    info = T.node_info(g)
    r = DF().loc[g]
    prio = {k: float(r[f"prio_{k}"]) for k in ["role", "reach", "money", "pagerank", "betw"]}
    nb = T.neighbors(g, "both", 25)
    tx = T.state()["tx"]
    t = tx[(tx.src == g) | (tx.dst == g)].sort_values("date")
    txs = [{"date": d.strftime("%Y-%m-%d"), "dir": "in" if dst == g else "out",
            "peer": str(src if dst == g else dst), "sum": float(s)}
           for src, dst, d, s in zip(t.src, t.dst, t.date, t.sum_kzt)]
    return J({"node": node_payload(g), "info": info, "prio": prio, "neighbors": nb, "tx": txs,
              "card_md": T.node_card(g)})


@app.get("/api/graph", tags=["граф"])
def graph(mode: str = "top", gid: str | None = None, radius: int = 1, cluster: int | None = None,
          ids: str | None = None, n: int = 45):
    Gr, df = G(), DF()
    if mode == "ego" and gid:
        g = gid_of(gid)
        nodes, frontier = {g}, {g}
        for _ in range(max(1, min(radius, 3))):
            nxt = set()
            for x in frontier:
                nxt |= set(Gr.predecessors(x)) | set(Gr.successors(x))
            nodes |= nxt
            frontier = nxt
        if len(nodes) > 160:
            nodes = set(df.loc[list(nodes)].sort_values("priority_score", ascending=False).head(160).index) | {g}
    elif mode == "cluster" and cluster is not None:
        nodes = set(df.index[df.cluster_id == cluster])
        if len(nodes) > 220:
            nodes = set(df.loc[list(nodes)].sort_values("priority_score", ascending=False).head(220).index)
    elif mode == "ids" and ids:
        core = {gid_of(x) for x in ids.split(",") if x.strip()}
        nodes = set(core)
        for g in core:
            nodes |= set(Gr.predecessors(g)) | set(Gr.successors(g))
        if len(nodes) > 160:
            nodes = core | set(df.loc[list(nodes - core)].sort_values("priority_score", ascending=False).head(160 - len(core)).index)
    else:
        topn = set(df.sort_values("priority_score", ascending=False).head(n).index)
        seeds = {u for g in topn for u in Gr.predecessors(g) if df.at[u, "is_seed"]}
        nodes = topn | set(list(seeds)[:40])
    return J(graph_payload(nodes))


@app.get("/api/flow/{gid}", tags=["граф"])
def flow(gid: str, max_hops: int = 3):
    """Пути денег: от seed-клиентов к узлу (вверх по цепочке) и крупнейшие потоки от узла дальше."""
    g = gid_of(gid)
    Gr, df = G(), DF()
    R = Gr.reverse(copy=False)
    dist = nx.single_source_shortest_path_length(R, g, cutoff=max_hops)
    seeds = sorted([s for s in dist if df.at[s, "is_seed"] and s != g], key=lambda s: (dist[s], -df.at[s, "out_kzt"]))[:10]
    up = []
    for s in seeds:
        p = nx.shortest_path(Gr, s, g)
        up.append({"path": [str(x) for x in p],
                   "sums": [Gr[a][b]["sum_kzt"] for a, b in zip(p, p[1:])]})
    down = [{"path": [str(x) for x in p["path"]], "sums": [h["sum_kzt"] for h in p["hops"]]}
            for p in T.trace_money(g, 3, 2)["paths"][:6] if len(p["path"]) > 1]
    nodes = {g} | {int(x) for p in up + down for x in p["path"]}
    return J({"target": str(g), "upstream": up, "downstream": down, "graph": graph_payload(nodes)})


@app.get("/api/clusters", tags=["кластеры"])
def clusters():
    ct = T.state()["clusters"].copy()
    rows = ct.to_dict("records")
    for r in rows:
        r["top_gids"] = str(r["top_gids"]).split(";")
    return J(rows)


@lru_cache(maxsize=1)
def _curve():
    df = DF()
    pts = []
    for k in [0, 5, 10, 15, 20, 30, 40, 50]:
        a = T.simulate_removal(top_n=k)["drop_pct"]["seed_reach_pairs"] if k else 0
        # случайный бейзлайн: k случайных не-seed узлов (random_state=1, как в README)
        b = T.simulate_removal(gids=df[~df.is_seed].sample(k, random_state=1).index.tolist())["drop_pct"]["seed_reach_pairs"] if k else 0
        pts.append({"n": k, "priority": a, "random": b})
    return pts


@app.get("/api/data_requests", tags=["оценка полноты"])
def data_requests(n: int = Query(15, ge=0)):
    rows = T.data_gaps(n)
    return J([{**r, "gid": str(r["gid"])} for r in rows])


@app.get("/api/simulate", tags=["стресс-тест"])
def simulate(top_n: int = 10):
    r = T.simulate_removal(top_n=max(0, min(top_n, 100)))
    return J({**r, "removed": [str(x) for x in r["removed"]], "curve": _curve()})


class Ask(BaseModel):
    question: str


@app.post("/api/ask", tags=["AI-аналитик"])
async def ask(body: Ask):
    res = await agent.ask_async(body.question)
    steps = []
    for s in res["steps"]:
        preview = json.dumps(js(s["result"]), ensure_ascii=False)[:600]
        steps.append({"tool": s["tool"], "args": s["args"], "via": s["via"], "ms": s["ms"], "preview": preview})
    return J({"answer": res["answer"], "steps": steps, "mode": res["mode"], "transport": res["transport"],
              "highlight": [str(x) for x in res["highlight"]], "ms": res["ms"]})


@app.get("/api/examples", tags=["AI-аналитик"])
def examples():
    df, Gr = DF(), G()
    top1 = df[~df.is_seed].priority_score.idxmax()
    rev = nx.single_source_shortest_path_length(Gr.reverse(copy=False), top1, cutoff=2)
    s3 = [str(g) for g in rev if df.at[g, "is_seed"]][:3]
    d4 = df[(df.depth == 4) & (df.sub_role == "truncated_unknown")].in_kzt.idxmax()
    return J([f"Кто собирает деньги с {' '.join(s3)}?",
              "Кого из не-seed проверять первым и почему?",
              f"Объясни роль {df.sort_values('priority_score', ascending=False).index[1]}",
              "Что будет, если заблокировать топ-20?",
              f"Почему у {d4} роль не определена?"])


@app.get("/api/health", tags=["служебное"])
async def health():
    async with agent.ToolBridge() as tb:
        return {"api": "ok", "mcp": tb.via == "mcp", "mcp_url": agent.MCP_URL,
                "mcp_tools": [t.name for t in tb.mcp_tools],
                "llm": bool(agent.os.getenv("OPENAI_API_KEY") or agent.os.getenv("NVIDIA_API_KEY"))}


@app.get("/api/download/{name}", tags=["служебное"])
def download(name: str):
    if name not in {"nodes_roles.csv", "clusters.csv", "top_nodes.csv", "run_meta.json", "data_requests.csv"}:
        raise HTTPException(404)
    return FileResponse(OUT / name, filename=name)


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
