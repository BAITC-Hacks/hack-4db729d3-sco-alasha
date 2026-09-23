#!/usr/bin/env python3
"""
Граф денег — пайплайн: raw .parquet -> метрики -> роли -> кластеры -> приоритеты -> 3 CSV.

Запуск:  python pipeline.py --data data --out out
Время:   ~10-30 секунд на ноутбуке (лимит ТЗ — 5 минут).

Все пороги ролей вынесены в словарь THRESHOLDS и описаны в README.
Никаких захардкоженных gid: всё считается из данных.
"""
from __future__ import annotations

import argparse
import json
import pickle
import time
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

SEED = 42
ROLES = ["consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"]

# ---------------------------------------------------------------- пороги (документированы в README)
THRESHOLDS = {
    "consolidator_min_payers": 4,      # >= 4 разных плательщиков
    "distributor_min_receivers": 10,   # >= 10 разных получателей
    "transit_pt_low": 0.8,             # коэффициент пропуска out/in в [0.8; 1.2]
    "transit_pt_high": 1.2,
    "transit_fast_days": 2,            # "быстрый транзит": ушло в течение 2 дней после прихода
    "coordinator_min_near_seeds": 3,   # деньги >= 3 разных seed доходят до узла за <= 2 перевода
    "coordinator_min_role_payers": 2,  # >= 2 связей (вход+выход) с consolidator/transit/distributor, из них >= 1 на входе
    "coordinator_min_in_kzt_pct": 0.75,  # входящий оборот не ниже 75-го перцентиля узлов с входом и выходом
    "terminal_fwd_prob_max": 0.35,     # для узлов 4-го колена: P(пересылает дальше) < 0.35 -> terminal
    "terminal_min_kzt": 100_000,       # terminal: >= 100 тыс ₸ ИЛИ >= 2 плательщиков, иначе мелкий получатель -> peripheral
}

ROLE_WEIGHT = {"coordinator": 1.0, "consolidator": 0.9, "distributor": 0.75,
               "transit": 0.6, "terminal": 0.35, "peripheral": 0.05}

ROLE_RU = {"coordinator": "координатор", "consolidator": "точка консолидации",
           "distributor": "распределитель", "transit": "транзит",
           "terminal": "конечный получатель", "peripheral": "периферия"}


def fmt_kzt(x: float) -> str:
    if x >= 1e6:
        return f"{x / 1e6:.1f} млн"
    if x >= 1e3:
        return f"{x / 1e3:.0f} тыс"
    return f"{x:.0f}"


# ---------------------------------------------------------------- загрузка
def load(data_dir: Path):
    edges = pd.read_parquet(data_dir / "edges.parquet")
    nodes = pd.read_parquet(data_dir / "nodes.parquet")
    tx = pd.read_parquet(data_dir / "transactions.parquet")
    tx["date"] = pd.to_datetime(tx["date"])
    return edges, nodes, tx


def build_graph(edges: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_nodes_from(nodes.gid.tolist())          # включая 19 seed без рёбер
    for r in edges.itertuples(index=False):
        G.add_edge(int(r.src), int(r.dst), sum_kzt=float(r.sum_kzt), n_tx=int(r.n_tx), depth=int(r.depth))
    return G


# ---------------------------------------------------------------- метрики
def structural_features(G: nx.DiGraph, nodes: pd.DataFrame) -> pd.DataFrame:
    df = nodes[["gid", "depth", "is_seed"]].copy().set_index("gid")
    df["in_deg"] = pd.Series(dict(G.in_degree()))
    df["out_deg"] = pd.Series(dict(G.out_degree()))
    df["in_kzt"] = pd.Series(dict(G.in_degree(weight="sum_kzt")))
    df["out_kzt"] = pd.Series(dict(G.out_degree(weight="sum_kzt")))
    df["in_tx"] = pd.Series(dict(G.in_degree(weight="n_tx")))
    df["out_tx"] = pd.Series(dict(G.out_degree(weight="n_tx")))
    df = df.fillna(0)
    df["pass_through"] = np.where(df.in_kzt > 0, df.out_kzt / df.in_kzt.replace(0, np.nan), np.nan)

    # ЛОВУШКА 1: ребро всегда записано на колене (глубина плательщика + 1).
    # Значит исходящие узлов 0..3 колена собраны полностью, а у узлов 4-го колена не собраны вообще.
    df["out_observed"] = df.depth < 4
    df["truncated_by_depth"] = (df.depth == 4)

    df["pagerank"] = pd.Series(nx.pagerank(G, weight="sum_kzt"))
    hubs, auth = nx.hits(G, max_iter=500)
    df["hub"] = pd.Series(hubs)
    df["authority"] = pd.Series(auth)
    df["betweenness"] = pd.Series(nx.betweenness_centrality(G, seed=SEED))

    # доля крупнейшего плательщика во входящих (1.0 = один источник)
    top_share = {}
    for v in G.nodes:
        w = [d["sum_kzt"] for _, _, d in G.in_edges(v, data=True)]
        top_share[v] = max(w) / sum(w) if w else np.nan
    df["top_payer_share"] = pd.Series(top_share)

    # seed-связи
    seeds = set(nodes.loc[nodes.is_seed, "gid"])
    df["seed_payers"] = [sum(1 for u in G.predecessors(v) if u in seeds) for v in df.index]
    reach = {v: set() for v in G.nodes}
    for s in seeds:
        for v in nx.descendants(G, s):
            reach[v].add(s)
    df["seed_reach"] = pd.Series({v: len(s) for v, s in reach.items()})
    # "близкие" seed: деньги seed доходят до узла не более чем за 2 перевода (сильный сигнал, в отличие от
    # seed_reach, который раздувается циклами в крупной компоненте)
    near = {v: 0 for v in G.nodes}
    for s in seeds:
        for v, dist in nx.single_source_shortest_path_length(G, s, cutoff=2).items():
            if dist >= 1:
                near[v] += 1
    df["near_seeds"] = pd.Series(near)
    df["avg_tx_in"] = np.where(df.in_tx > 0, df.in_kzt / df.in_tx.replace(0, np.nan), 0)
    return df.fillna({"top_payer_share": 0}), reach


def temporal_features(df: pd.DataFrame, tx: pd.DataFrame, fast_days: int) -> pd.DataFrame:
    """Сквозной транзит и синхронные поступления."""
    inc = tx.rename(columns={"dst": "gid"})[["gid", "date", "sum_kzt", "src"]]
    out = tx.rename(columns={"src": "gid"})[["gid", "date", "sum_kzt"]]

    # доля исходящей суммы, которой предшествовал приход в пределах fast_days
    fast = {}
    inc_by = {g: grp["date"].values for g, grp in inc.groupby("gid")}
    for g, grp in out.groupby("gid"):
        dates_in = inc_by.get(g)
        if dates_in is None:
            continue
        tot = grp.sum_kzt.sum()
        ok = 0.0
        for d, s in zip(grp.date.values, grp.sum_kzt.values):
            delta = (d - dates_in) / np.timedelta64(1, "D")
            if ((delta >= 0) & (delta <= fast_days)).any():
                ok += s
        fast[g] = ok / tot if tot else 0
    df["fast_transit_share"] = pd.Series(fast)

    # максимальное число разных плательщиков в один день (синхронные поступления)
    sync = inc.groupby(["gid", "date"]).src.nunique().groupby("gid").max()
    df["max_payers_same_day"] = sync
    df["active_days_in"] = inc.groupby("gid").date.nunique()
    df["last_in_day"] = inc.groupby("gid").date.max().dt.day
    df["first_in_day"] = inc.groupby("gid").date.min().dt.day
    return df.fillna({"fast_transit_share": 0, "max_payers_same_day": 0, "active_days_in": 0,
                      "last_in_day": 0, "first_in_day": 0})


def cycle_features(G: nx.DiGraph, df: pd.DataFrame) -> pd.DataFrame:
    """Возвратные потоки: узел лежит на цикле длиной <= 6."""
    on_cycle = {}
    n_cycles = 0
    for cyc in nx.simple_cycles(G, length_bound=6):
        n_cycles += 1
        for v in cyc:
            on_cycle[v] = on_cycle.get(v, 0) + 1
        if n_cycles > 20000:
            break
    df["cycles"] = pd.Series(on_cycle)
    return df.fillna({"cycles": 0})


def forward_probability(df: pd.DataFrame) -> pd.DataFrame:
    """
    ЛОВУШКА 1 (решение). Для узлов 1..3 колена мы ЗНАЕМ, переслали ли они деньги дальше
    (их исходящие собраны). Обучаем логистическую регрессию только на ВХОДЯЩЕМ поведении
    и применяем к узлам 4-го колена, чьи исходящие не видны.
    """
    feats = ["log_in_kzt", "in_deg", "log_in_tx", "log_avg_tx", "active_days_in", "last_in_day"]
    X = pd.DataFrame({
        "log_in_kzt": np.log1p(df.in_kzt), "in_deg": df.in_deg, "log_in_tx": np.log1p(df.in_tx),
        "log_avg_tx": np.log1p(df.avg_tx_in), "active_days_in": df.active_days_in, "last_in_day": df.last_in_day,
    }, index=df.index)
    train = df.depth.between(1, 3) & ~df.is_seed & (df.in_deg > 0)
    y = (df.loc[train, "out_deg"] > 0).astype(int)
    mu, sd = X[train].mean(), X[train].std().replace(0, 1)
    clf = LogisticRegression(max_iter=1000, random_state=SEED).fit((X[train] - mu) / sd, y)
    df["p_forward"] = clf.predict_proba((X - mu) / sd)[:, 1]
    df.loc[df.out_observed, "p_forward"] = (df.loc[df.out_observed, "out_deg"] > 0).astype(float)
    model_info = {"features": feats, "coef": dict(zip(feats, clf.coef_[0].round(3).tolist())),
                  "train_size": int(train.sum()), "train_forward_rate": float(y.mean())}
    return df, model_info


# ---------------------------------------------------------------- роли
def clip01(x):
    return float(np.clip(x, 0, 1))


def assign_roles(G: nx.DiGraph, df: pd.DataFrame, T: dict) -> pd.DataFrame:
    """Каждой роли — формальное правило (условие) и скор уверенности. Роль = максимальный скор среди выполненных правил."""
    roles, scores, evid, sub = {}, {}, {}, {}

    # проход 1: базовые роли
    for g, r in df.iterrows():
        cand = {}
        pt = r.pass_through
        seed_note = " (seed: входящие извне выборки не видны)" if r.is_seed else ""

        if r.out_deg >= T["distributor_min_receivers"]:
            cand["distributor"] = (clip01(0.5 + 0.5 * np.log(r.out_deg / T["distributor_min_receivers"] + 1) / np.log(12)),
                                   f"веерная рассылка: {int(r.out_deg)} получателям, {int(r.out_tx)} переводов на {fmt_kzt(r.out_kzt)} ₸{seed_note}")

        if r.in_deg >= T["consolidator_min_payers"]:
            kept = 1 - min(pt, 1) if not np.isnan(pt) else (1 - r.p_forward)
            s = 0.45 + 0.35 * min(r.in_deg / 12, 1) + 0.2 * kept
            if r.is_seed:
                s *= 0.8
            fwd = (f"дальше {min(pt, 9.99) * 100:.0f}% полученного" if r.out_observed
                   else f"исходящие не видны (4 колено), P(пересылает)={r.p_forward:.2f}")
            cand["consolidator"] = (clip01(s), f"признаки консолидации: получает от {int(r.in_deg)} плательщиков "
                                               f"({int(r.seed_payers)} seed) {fmt_kzt(r.in_kzt)} ₸, {fwd}")

        if (r.in_deg >= 1 and r.out_deg >= 1 and not r.is_seed and not np.isnan(pt)
                and T["transit_pt_low"] <= pt <= T["transit_pt_high"]):
            s = 0.55 + 0.3 * r.fast_transit_share + 0.15 * (1 - abs(1 - pt) / 0.2)
            cand["transit"] = (clip01(s), f"транзит: получил {fmt_kzt(r.in_kzt)} ₸, отдал {pt * 100:.0f}%, "
                                          f"{r.fast_transit_share * 100:.0f}% ушло в течение {T['transit_fast_days']} дн. после прихода")

        big_enough = r.in_deg >= 2 or r.in_kzt >= T["terminal_min_kzt"]
        if r.in_deg >= 1 and r.out_deg == 0 and not big_enough:
            sub[g] = "small_receiver"
        if r.in_deg >= 1 and r.out_deg == 0 and big_enough:
            if r.out_observed:
                s = 0.6 + 0.2 * min(r.in_deg / 4, 1) + 0.2 * min(np.log1p(r.in_kzt) / np.log1p(1e6), 1)
                cand["terminal"] = (clip01(s), f"деньги осели: получил {fmt_kzt(r.in_kzt)} ₸ от {int(r.in_deg)} плательщ., "
                                               f"исходящих ≥5 тыс ₸ нет (колено {int(r.depth)}, исходящие собраны)")
                sub[g] = "observed_sink"
            elif r.p_forward < T["terminal_fwd_prob_max"]:
                s = 0.35 + 0.4 * (1 - r.p_forward)
                cand["terminal"] = (clip01(s), f"вероятный конечный получатель: {fmt_kzt(r.in_kzt)} ₸ от {int(r.in_deg)}; "
                                               f"4 колено, исходящие не собраны, P(пересылает)={r.p_forward:.2f}")
                sub[g] = "likely_sink_truncated"
            else:
                sub[g] = "truncated_unknown"

        if cand:
            best = max(cand, key=lambda k: cand[k][0])
            roles[g], scores[g], evid[g] = best, cand[best][0], cand[best][1]
        else:
            roles[g] = "peripheral"
            if r.depth == 4 and r.in_deg >= 1:
                scores[g] = clip01(0.5 + 0.3 * abs(r.p_forward - 0.5))
                evid[g] = (f"4 колено, обход оборван: исходящие не видны, P(пересылает)={r.p_forward:.2f} — "
                           f"роль не определить, получил {fmt_kzt(r.in_kzt)} ₸")
            elif sub.get(g) == "small_receiver" and r.out_observed:
                scores[g] = 0.85
                evid[g] = (f"мелкий получатель: разовое поступление {fmt_kzt(r.in_kzt)} ₸ от 1 плательщика, "
                           f"дальше не ушло — признаков роли нет")
            elif r.in_deg == 0 and r.out_deg == 0:
                scores[g] = 0.9
                evid[g] = "seed без переводов ≥5 тыс ₸ внутри банка за июль — связей в выгрузке нет"
            elif r.is_seed:
                scores[g] = 0.7
                evid[g] = (f"seed: отправил {fmt_kzt(r.out_kzt)} ₸ {int(r.out_deg)} получателям; "
                           f"для консолидации/рассылки мало связей")
            else:
                scores[g] = 0.7
                evid[g] = (f"слабые связи: {int(r.in_deg)} вход / {int(r.out_deg)} выход, "
                           f"{fmt_kzt(r.in_kzt)} ₸ → {fmt_kzt(r.out_kzt)} ₸, без устойчивого паттерна")

    # проход 2: координатор — узел МЕЖДУ сбором и распределением: получает от структурных посредников
    # (консолидаторы/транзит/распределители), близок к нескольким seed и держит крупный оборот
    structural = {g for g, ro in roles.items() if ro in ("consolidator", "transit", "distributor")}
    active = df[(df.in_deg > 0) & (df.out_deg > 0)]
    kzt_cut = active.in_kzt.quantile(T["coordinator_min_in_kzt_pct"])
    for g, r in df.iterrows():
        if r.is_seed:
            continue
        up = [u for u in G.predecessors(g) if u in structural]
        down = [v for v in G.successors(g) if v in structural]
        links = len(up) + len(down)
        if (r.near_seeds >= T["coordinator_min_near_seeds"] and len(up) >= 1
                and links >= T["coordinator_min_role_payers"] and r.in_kzt >= kzt_cut):
            s = 0.45 + 0.2 * min(r.near_seeds / 8, 1) + 0.2 * min(links / 6, 1) + 0.15 * min(r.in_deg / 10, 1)
            if s >= scores[g] - 0.05 or roles[g] in ("terminal", "peripheral", "transit"):
                roles[g], scores[g] = "coordinator", clip01(s)
                evid[g] = (f"кандидат в организаторы: деньги {int(r.near_seeds)} seed за ≤2 перевода, связан с "
                           f"{len(up)} посредн. на входе и {len(down)} на выходе, вход {fmt_kzt(r.in_kzt)} ₸")

    df["role"] = pd.Series(roles)
    df["role_score"] = pd.Series(scores).round(3)
    df["evidence"] = pd.Series(evid).str.slice(0, 200)
    df["sub_role"] = pd.Series(sub)
    df["sub_role"] = df["sub_role"].fillna("")
    return df


# ---------------------------------------------------------------- кластеры
def clusters(G: nx.DiGraph, df: pd.DataFrame):
    """Louvain на НЕОРИЕНТИРОВАННОЙ проекции (оговорено в README): направление для сообществ не нужно,
    вес ребра = log(1 + сумма). Детерминировано (seed=42)."""
    UG = nx.Graph()
    UG.add_nodes_from(G.nodes)
    for u, v, d in G.edges(data=True):
        w = np.log1p(d["sum_kzt"])
        if UG.has_edge(u, v):
            UG[u][v]["weight"] += w
        else:
            UG.add_edge(u, v, weight=w)
    comms = nx.community.louvain_communities(UG, weight="weight", resolution=1.0, seed=SEED)
    comms = sorted(comms, key=lambda c: (-len(c), min(c)))
    cid = {}
    for i, c in enumerate(comms):
        for v in c:
            cid[v] = i
    df["cluster_id"] = pd.Series(cid).astype(int)
    return df


def cluster_table(G: nx.DiGraph, df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for c, grp in df.groupby("cluster_id"):
        members = set(grp.index)
        internal = sum(d["sum_kzt"] for u, v, d in G.edges(data=True) if u in members and v in members)
        top = grp.sort_values("priority_score", ascending=False).head(5)
        rc = grp.role.value_counts()
        n_seed = int(grp.is_seed.sum())
        lead = top.iloc[0]
        if len(grp) == 1:
            hyp = "изолированный узел без связей в выгрузке"
        elif rc.get("coordinator", 0) > 0:
            g0 = grp[grp.role == "coordinator"].sort_values("priority_score").index[-1]
            hyp = (f"гипотеза: многоуровневая схема — {n_seed} seed → {rc.get('consolidator', 0)} консолид. / "
                   f"{rc.get('transit', 0)} транзит → координатор {g0}")
        elif rc.get("consolidator", 0) > 0 and n_seed >= 2:
            g0 = grp[grp.role == "consolidator"].sort_values("priority_score").index[-1]
            hyp = f"гипотеза: сбор средств от {n_seed} seed в точке консолидации {g0}"
        elif rc.get("distributor", 0) > 0:
            g0 = grp[grp.role == "distributor"].sort_values("priority_score").index[-1]
            hyp = f"гипотеза: веерная рассылка от {g0} на {int(df.at[g0, 'out_deg'])} получателей (выплаты/дробление)"
        elif rc.get("transit", 0) >= 2:
            hyp = f"гипотеза: транзитная цепочка из {rc.get('transit', 0)} счетов"
        elif n_seed == 0:
            hyp = "гипотеза: периферия дальних колен, связь с seed косвенная"
        else:
            hyp = f"гипотеза: локальные переводы вокруг {n_seed} seed без выраженной структуры"
        rows.append({"cluster_id": int(c), "n_nodes": len(grp), "n_seed": n_seed,
                     "sum_kzt_internal": round(internal, 2),
                     "top_gids": ";".join(str(int(g)) for g in top.index),
                     "hypothesis": hyp,
                     "roles": ", ".join(f"{k}:{v}" for k, v in rc.items() if k != "peripheral"),
                     "lead_gid": int(lead.name)})
    return pd.DataFrame(rows).sort_values(["n_seed", "sum_kzt_internal"], ascending=False)


# ---------------------------------------------------------------- приоритет
def rank01(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average")


def priority(df: pd.DataFrame) -> pd.DataFrame:
    """Приоритет проверки = взвешенная сумма интерпретируемых компонент (все в 0..1):
       0.30 вес роли × уверенность, 0.25 близость к seed (near_seeds), 0.20 входящий оборот, 0.15 PageRank, 0.10 betweenness.
       Известные seed умножаются на 0.6: они уже в деле, фокус — на тех, кто выше по цепочке."""
    comp = pd.DataFrame(index=df.index)
    comp["role"] = df.role.map(ROLE_WEIGHT) * df.role_score
    comp["reach"] = np.minimum(df.near_seeds / 6, 1)
    comp["money"] = rank01(np.log1p(df.in_kzt + df.out_kzt))
    comp["pagerank"] = rank01(df.pagerank)
    comp["betw"] = rank01(df.betweenness)
    p = 0.30 * comp.role + 0.25 * comp.reach + 0.20 * comp.money + 0.15 * comp.pagerank + 0.10 * comp.betw
    p = np.where(df.is_seed, p * 0.6, p)
    df["priority_score"] = np.round(p / np.max(p), 4)
    for c in comp.columns:
        df[f"prio_{c}"] = comp[c].round(3)
    return df


def why_text(g, r) -> str:
    parts = [f"{ROLE_RU[r.role]} (уверенность {r.role_score:.2f})"]
    if r.near_seeds:
        parts.append(f"деньги {int(r.near_seeds)} seed доходят за ≤2 перевода")
    parts.append(f"вход {fmt_kzt(r.in_kzt)} ₸ от {int(r.in_deg)}, выход {fmt_kzt(r.out_kzt)} ₸ на {int(r.out_deg)}")
    if r.fast_transit_share >= 0.5 and r.out_deg:
        parts.append(f"{r.fast_transit_share * 100:.0f}% ушло за ≤2 дня")
    if r.max_payers_same_day >= 3:
        parts.append(f"до {int(r.max_payers_same_day)} плательщиков в один день")
    if r.cycles:
        parts.append(f"на {int(r.cycles)} циклах возврата")
    return "; ".join(parts) + ". Гипотеза для проверки, не вывод о виновности."


# ---------------------------------------------------------------- устойчивость
def resilience(G: nx.DiGraph, df: pd.DataFrame, ns=(5, 10, 20)) -> dict:
    """Что будет с сетью при изъятии топ-N узлов по приоритету."""
    seeds = set(df.index[df.is_seed])

    def stats(H):
        wcc = max((len(c) for c in nx.weakly_connected_components(H)), default=0)
        flow = sum(d["sum_kzt"] for _, _, d in H.edges(data=True))
        reach = sum(len(nx.descendants(H, s)) for s in seeds if s in H)
        return {"largest_component": wcc, "edges": H.number_of_edges(), "flow_kzt": flow, "seed_reach_pairs": reach}

    base = stats(G)
    order = df.sort_values("priority_score", ascending=False)
    order = order[~order.is_seed].index.tolist()
    out = {"base": base}
    for n in ns:
        H = G.copy()
        H.remove_nodes_from(order[:n])
        out[f"top{n}"] = stats(H)
    return out


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    t0 = time.time()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    edges, nodes, tx = load(Path(a.data))
    G = build_graph(edges, nodes)
    print(f"[1/7] граф: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")

    df, reach = structural_features(G, nodes)
    df = temporal_features(df, tx, THRESHOLDS["transit_fast_days"])
    df = cycle_features(G, df)
    print(f"[2/7] метрики посчитаны ({time.time() - t0:.1f}s)")

    df, model_info = forward_probability(df)
    print(f"[3/7] модель P(пересылает дальше) для 4-го колена: обучена на {model_info['train_size']} узлах")

    df = assign_roles(G, df, THRESHOLDS)
    print(f"[4/7] роли: {df.role.value_counts().to_dict()}")

    df = clusters(G, df)
    df = priority(df)
    print(f"[5/7] кластеров: {df.cluster_id.nunique()}")

    # --- nodes_roles.csv (обязательные колонки + метрики для прозрачности)
    df.index.name = "gid"
    nr = df.reset_index()
    req = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]
    extra = ["sub_role", "depth", "is_seed", "in_deg", "out_deg", "in_kzt", "out_kzt", "in_tx", "out_tx",
             "pass_through", "seed_payers", "near_seeds", "seed_reach", "p_forward", "fast_transit_share",
             "max_payers_same_day", "cycles", "pagerank", "hub", "authority", "betweenness",
             "truncated_by_depth", "prio_role", "prio_reach", "prio_money", "prio_pagerank", "prio_betw"]
    nr = nr[req + extra].sort_values("priority_score", ascending=False)
    nr["gid"] = nr.gid.astype("int64")
    nr.to_csv(out / "nodes_roles.csv", index=False)

    # --- clusters.csv
    ct = cluster_table(G, df)
    ct.to_csv(out / "clusters.csv", index=False)

    # --- top_nodes.csv
    top = df[df.role != "peripheral"].sort_values("priority_score", ascending=False).head(30)
    tn = pd.DataFrame({"rank": range(1, len(top) + 1), "gid": top.index.astype("int64"), "role": top.role.values,
                       "priority_score": top.priority_score.values,
                       "why": [why_text(g, r) for g, r in top.iterrows()]})
    tn.to_csv(out / "top_nodes.csv", index=False)
    print(f"[6/7] выгрузки: nodes_roles={len(nr)}, clusters={len(ct)}, top_nodes={len(tn)}")

    # --- артефакты для UI / MCP
    res = resilience(G, df)
    pos = nx.spring_layout(G.to_undirected(), seed=SEED, k=0.08, iterations=60)
    with open(out / "graph.pkl", "wb") as f:
        pickle.dump({"G": G, "df": df, "tx": tx, "pos": pos, "clusters": ct}, f)
    meta = {"thresholds": THRESHOLDS, "role_weight": ROLE_WEIGHT, "forward_model": model_info,
            "resilience": res, "runtime_sec": round(time.time() - t0, 1)}
    (out / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"[7/7] готово за {time.time() - t0:.1f}s → {out}/")


if __name__ == "__main__":
    main()
