"""
Инструменты запросов к графу. Одни и те же функции используются:
  * UI (app.py),
  * MCP-сервером (mcp_server.py) — для любого MCP-клиента (Claude Desktop, Cursor, внутренний бот),
  * AI-аналитиком (agent.py) — через function calling.
Все функции возвращают JSON-совместимые dict/list, gid — int.
"""
from __future__ import annotations

import pickle
from functools import lru_cache
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

OUT = Path(__file__).parent / "out"
ROLE_RU = {"coordinator": "координатор", "consolidator": "точка консолидации",
           "distributor": "распределитель", "transit": "транзит",
           "terminal": "конечный получатель", "peripheral": "периферия"}


@lru_cache(maxsize=1)
def state():
    with open(OUT / "graph.pkl", "rb") as f:
        return pickle.load(f)


def G() -> nx.DiGraph:
    return state()["G"]


def DF() -> pd.DataFrame:
    return state()["df"]


def _data_requests() -> pd.DataFrame:
    return pd.read_csv(OUT / "data_requests.csv", dtype={"gid": "int64"})


def data_gaps(n: int = 15) -> list:
    """Какие данные запросить следующими: топ запросов по приоритету узла (gid может повторяться)."""
    return _data_requests().sort_values("rank").head(max(0, int(n))).to_dict("records")


def _gid(x) -> int:
    return int(str(x).strip())


def _short(g: int) -> dict:
    r = DF().loc[g]
    return {"gid": int(g), "role": r.role, "role_ru": ROLE_RU[r.role], "priority_score": float(r.priority_score),
            "cluster_id": int(r.cluster_id), "is_seed": bool(r.is_seed)}


# ------------------------------------------------------------------ базовые
def node_info(gid) -> dict:
    """Роль, скор, кластер, ключевые метрики и evidence по одному узлу."""
    g = _gid(gid)
    if g not in DF().index:
        return {"error": f"gid {g} не найден в графе"}
    r = DF().loc[g]
    keys = ["role", "role_score", "sub_role", "cluster_id", "priority_score", "evidence", "depth", "is_seed",
            "in_deg", "out_deg", "in_kzt", "out_kzt", "in_tx", "out_tx", "pass_through", "seed_payers",
            "near_seeds", "seed_reach", "p_forward", "fast_transit_share", "max_payers_same_day", "cycles",
            "anomaly_score", "anomaly_flag", "anomaly_reason", "structuring_flag", "small_tx_share"]
    out = {"gid": g}
    for k in keys:
        v = r[k]
        if isinstance(v, (np.floating, float)):
            v = None if np.isnan(v) else round(float(v), 4)
        elif isinstance(v, (np.integer,)):
            v = int(v)
        elif isinstance(v, (np.bool_,)):
            v = bool(v)
        out[k] = v
    out["role_ru"] = ROLE_RU[r.role]
    rank = int((DF().priority_score > r.priority_score).sum()) + 1
    out["priority_rank"] = rank
    requests = _data_requests()
    out["data_requests"] = requests.loc[requests.gid == g, ["request", "reason"]].to_dict("records")
    return out


def neighbors(gid, direction: str = "both", limit: int = 15) -> dict:
    """Контрагенты узла: direction = in (кто платил), out (кому платил), both. Отсортировано по сумме."""
    g = _gid(gid)
    Gr = G()
    if g not in Gr:
        return {"error": f"gid {g} не найден"}
    res = {}
    if direction in ("in", "both"):
        e = sorted(Gr.in_edges(g, data=True), key=lambda x: -x[2]["sum_kzt"])[:limit]
        res["payers"] = [{**_short(u), "sum_kzt": d["sum_kzt"], "n_tx": d["n_tx"]} for u, _, d in e]
    if direction in ("out", "both"):
        e = sorted(Gr.out_edges(g, data=True), key=lambda x: -x[2]["sum_kzt"])[:limit]
        res["receivers"] = [{**_short(v), "sum_kzt": d["sum_kzt"], "n_tx": d["n_tx"]} for _, v, d in e]
    return res


def trace_money(gid, max_depth: int = 4, branch: int = 3) -> dict:
    """Куда уходят деньги узла: вниз по цепочке до max_depth переводов, на каждом шаге — branch крупнейших потоков."""
    g = _gid(gid)
    Gr = G()
    if g not in Gr:
        return {"error": f"gid {g} не найден"}
    paths, frontier = [], [[g]]
    for _ in range(max_depth):
        nxt = []
        for p in frontier:
            outs = sorted(Gr.out_edges(p[-1], data=True), key=lambda x: -x[2]["sum_kzt"])[:branch]
            outs = [o for o in outs if o[1] not in p]
            if not outs:
                paths.append(p)
            for _, v, _ in outs:
                nxt.append(p + [v])
        frontier = nxt
    paths += frontier
    out = []
    for p in paths[:25]:
        hops = [{"from": int(a), "to": int(b), "sum_kzt": Gr[a][b]["sum_kzt"]} for a, b in zip(p, p[1:])]
        out.append({"path": [int(x) for x in p], "roles": [DF().at[x, "role"] for x in p], "hops": hops})
    return {"gid": g, "paths": out}


def common_receivers(gids: list, max_depth: int = 3, top: int = 10) -> dict:
    """Кто собирает деньги с указанных клиентов: узлы, куда за <= max_depth переводов доходят деньги
    от нескольких из них. Отсортировано по числу источников и приоритету."""
    Gr = G()
    src = [_gid(x) for x in gids]
    missing = [s for s in src if s not in Gr]
    reach: dict[int, set] = {}
    for s in src:
        if s in Gr:
            for v, dist in nx.single_source_shortest_path_length(Gr, s, cutoff=max_depth).items():
                if dist >= 1:
                    reach.setdefault(v, set()).add(s)
    cand = [(v, s) for v, s in reach.items() if len(s) >= 2]
    cand.sort(key=lambda x: (-len(x[1]), -DF().at[x[0], "priority_score"]))
    res = [{**_short(v), "from_n_of_given": len(s), "from_gids": sorted(int(x) for x in s),
            "evidence": DF().at[v, "evidence"]} for v, s in cand[:top]]
    return {"given": len(src), "not_found": missing, "collectors": res}


def top_nodes(role: str | None = None, n: int = 10, exclude_seeds: bool = False) -> list:
    """Топ узлов по приоритету проверки, опционально с фильтром по роли."""
    d = DF()
    if role:
        d = d[d.role == role]
    if exclude_seeds:
        d = d[~d.is_seed]
    d = d.sort_values("priority_score", ascending=False).head(n)
    return [{**_short(g), "evidence": r.evidence} for g, r in d.iterrows()]


def cluster_info(cluster_id: int) -> dict:
    """Состав кластера: размер, seed, оборот, гипотеза, ключевые узлы."""
    ct = state()["clusters"]
    row = ct[ct.cluster_id == int(cluster_id)]
    if row.empty:
        return {"error": f"кластер {cluster_id} не найден"}
    r = row.iloc[0].to_dict()
    members = DF()[DF().cluster_id == int(cluster_id)]
    r["key_nodes"] = top_nodes_in(members, 8)
    return {k: (int(v) if isinstance(v, (np.integer,)) else v) for k, v in r.items()}


def top_nodes_in(members: pd.DataFrame, n: int) -> list:
    m = members.sort_values("priority_score", ascending=False).head(n)
    return [{**_short(g), "evidence": r.evidence} for g, r in m.iterrows()]


def simulate_removal(gids: list | None = None, top_n: int = 0) -> dict:
    """Что станет с сетью, если заблокировать узлы (список gid или топ-N по приоритету без seed).
    Возвращает размер крупнейшей компоненты, оборот, число достижимых пар seed→узел до и после."""
    Gr = G()
    d = DF()
    if not gids:
        gids = d[~d.is_seed].sort_values("priority_score", ascending=False).head(int(top_n)).index.tolist()
    rm = [_gid(x) for x in gids if _gid(x) in Gr]
    seeds = d.index[d.is_seed]

    def stats(H):
        comps = sorted((len(c) for c in nx.weakly_connected_components(H)), reverse=True)
        return {"largest_component": comps[0] if comps else 0, "n_components": len(comps),
                "flow_kzt": float(sum(x["sum_kzt"] for *_, x in H.edges(data=True))),
                "seed_reach_pairs": int(sum(len(nx.descendants(H, s)) for s in seeds if s in H))}

    before = stats(Gr)
    H = Gr.copy()
    H.remove_nodes_from(rm)
    after = stats(H)
    pct = {k: round(100 * (1 - after[k] / before[k]), 1) if before[k] else 0
           for k in ("largest_component", "flow_kzt", "seed_reach_pairs")}
    return {"removed": [int(x) for x in rm], "before": before, "after": after, "drop_pct": pct}


def node_card(gid) -> str:
    """Справка по узлу для аналитика (markdown): роль, потоки, связи, на что обратить внимание."""
    i = node_info(gid)
    if "error" in i:
        return i["error"]
    nb = neighbors(gid, "both", 5)
    lines = [f"### Узел {i['gid']} — {i['role_ru']} (уверенность {i['role_score']:.2f})",
             f"**Приоритет:** {i['priority_score']:.3f} (место {i['priority_rank']} из {len(DF())}) · "
             f"**кластер** {i['cluster_id']} · **колено** {i['depth']}{' · seed' if i['is_seed'] else ''}",
             f"**Обоснование:** {i['evidence']}",
             f"**Потоки:** вход {i['in_kzt']:,.0f} ₸ от {int(i['in_deg'])} плательщиков ({int(i['in_tx'])} перев.), "
             f"выход {i['out_kzt']:,.0f} ₸ на {int(i['out_deg'])} получателей ({int(i['out_tx'])} перев.)"]
    if nb.get("payers"):
        lines.append("**Крупнейшие плательщики:** " + ", ".join(
            f"{p['gid']} ({p['role_ru']}, {p['sum_kzt']:,.0f} ₸)" for p in nb["payers"][:3]))
    if nb.get("receivers"):
        lines.append("**Крупнейшие получатели:** " + ", ".join(
            f"{p['gid']} ({p['role_ru']}, {p['sum_kzt']:,.0f} ₸)" for p in nb["receivers"][:3]))
    flags = []
    if i["structuring_flag"]:
        flags.append(f"признаки дробления у порога: {i['small_tx_share']:.0%} входящих переводов от 5 до 10 тыс ₸")
    if i["anomaly_flag"]:
        flags.append(f"аномальный профиль: {i['anomaly_reason']}")
    if i["fast_transit_share"] and i["fast_transit_share"] >= 0.5:
        flags.append(f"{i['fast_transit_share'] * 100:.0f}% исходящих ушло в течение 2 дней после поступления")
    if i["max_payers_same_day"] and i["max_payers_same_day"] >= 3:
        flags.append(f"синхронные поступления: до {int(i['max_payers_same_day'])} плательщиков в один день")
    if i["cycles"]:
        flags.append(f"участвует в {int(i['cycles'])} циклах возврата средств (≤6 шагов)")
    if i["depth"] == 4:
        flags.append(f"4-е колено: исходящие не выгружены, модель оценивает P(пересылает дальше) = {i['p_forward']:.2f} — "
                     f"запросить выписку исходящих")
    if i["is_seed"]:
        flags.append("seed: входящие извне выборки не видны, соотношение отдал/получил некорректно")
    if flags:
        lines.append("**На что обратить внимание:**\n" + "\n".join(f"- {f}" for f in flags))
    for request in i["data_requests"]:
        lines.append(f"**Рекомендуемый запрос данных:** {request['request']}. Основание: {request['reason']}")
    lines.append("_Гипотеза для углублённой проверки, не вывод о виновности._")
    return "\n\n".join(lines)


TOOLS = {f.__name__: f for f in
         [node_info, neighbors, trace_money, common_receivers, top_nodes, cluster_info, simulate_removal, node_card, data_gaps]}
