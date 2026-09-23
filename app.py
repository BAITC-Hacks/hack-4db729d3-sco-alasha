"""
Экран аналитика «Граф денег».  Запуск:  streamlit run app.py
Перед первым запуском: python pipeline.py  (создаёт out/)
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

import graph_tools as T

st.set_page_config(page_title="Граф денег — AML", page_icon="🕸", layout="wide")

OUT = Path(__file__).parent / "out"
if not (OUT / "graph.pkl").exists():
    st.error("Нет out/graph.pkl — сначала запустите:  python pipeline.py --data data --out out")
    st.stop()

COLORS = {"coordinator": "#d62728", "consolidator": "#ff7f0e", "distributor": "#9467bd",
          "transit": "#1f77b4", "terminal": "#2ca02c", "peripheral": "#b0b0b0"}
ROLE_RU = T.ROLE_RU
G, DF = T.G(), T.DF()
CT = T.state()["clusters"]
POS = T.state()["pos"]
META = json.loads((OUT / "run_meta.json").read_text(encoding="utf-8"))


def fmt(x):
    return f"{x:,.0f}".replace(",", " ")


# ------------------------------------------------------------------ визуализация (pyvis)
def draw(nodes: set, focus: set = frozenset(), height: int = 620):
    """Направленная схема: цвет = роль, звезда = seed, размер = приоритет, толщина стрелки = сумма.
    gid передаются СТРОКАМИ: 18-значные числа теряют точность в JavaScript."""
    H = G.subgraph(nodes)
    net = Network(height=f"{height}px", width="100%", directed=True, bgcolor="#ffffff", cdn_resources="in_line")
    net.barnes_hut(gravity=-9000, spring_length=140, overlap=0.5)
    net.set_options('{"physics":{"stabilization":{"iterations":250}},"edges":{"smooth":{"type":"continuous"}},'
                    '"interaction":{"hover":true,"navigationButtons":true}}')
    for g in H.nodes:
        r = DF.loc[g]
        net.add_node(str(g), label=str(g)[-6:], color=COLORS[r.role],
                     shape="star" if r.is_seed else "dot",
                     size=10 + 30 * float(r.priority_score) + (14 if g in focus else 0),
                     borderWidth=4 if g in focus else 1,
                     title=f"gid {g}\n{ROLE_RU[r.role]} ({r.role_score:.2f})\nприоритет {r.priority_score:.3f}\n{r.evidence}")
    mx = max((d["sum_kzt"] for *_, d in H.edges(data=True)), default=1)
    for u, v, d in H.edges(data=True):
        net.add_edge(str(u), str(v), width=0.5 + 5 * (np.log1p(d["sum_kzt"]) / np.log1p(mx)) ** 3,
                     title=f"{fmt(d['sum_kzt'])} ₸, {d['n_tx']} перев.", arrows="to",
                     color="#d62728" if (u in focus or v in focus) else "#9aa5b1")
    html = net.generate_html().replace(
        "network = new vis.Network(container, data, options);",
        "network = new vis.Network(container, data, options);"
        "network.once('stabilizationIterationsDone', function(){network.fit();});"
        "var _k=0; var _t=setInterval(function(){network.redraw(); network.fit(); if(++_k>10) clearInterval(_t);}, 800);"
        "window.addEventListener('resize', function(){network.fit();});")
    components.html(html, height=height + 20, scrolling=False)


def ego(g: int, radius: int, max_nodes: int = 120) -> set:
    nodes = {g}
    frontier = {g}
    for _ in range(radius):
        nxt = set()
        for x in frontier:
            nxt |= set(G.predecessors(x)) | set(G.successors(x))
        nodes |= nxt
        frontier = nxt
        if len(nodes) > max_nodes:
            break
    if len(nodes) > max_nodes:  # оставить самые приоритетные
        keep = DF.loc[list(nodes)].sort_values("priority_score", ascending=False).head(max_nodes).index
        nodes = set(keep) | {g}
    return nodes


def legend():
    st.markdown(" ".join(f"<span style='color:{c};font-size:20px'>●</span> {ROLE_RU[r]}" for r, c in COLORS.items())
                + " &nbsp; ★ seed", unsafe_allow_html=True)


# ------------------------------------------------------------------ шапка
st.title("🕸 Граф денег — кого проверять первым и почему")
st.caption("Восстановление структуры группы по транзакционной сети. Все выводы — гипотезы для проверки, не утверждение о виновности.")

rc = DF.role.value_counts()
c = st.columns(7)
c[0].metric("Узлов", fmt(len(DF)), f"{int(DF.is_seed.sum())} seed")
c[1].metric("Оборот, ₸", f"{DF.out_kzt.sum() / 1e6:.0f} млн")
for i, r in enumerate(["coordinator", "consolidator", "distributor", "transit", "terminal"]):
    c[i + 2].metric(ROLE_RU[r].capitalize(), int(rc.get(r, 0)))

with st.sidebar:
    st.header("🔎 Поиск по gid")
    q = st.text_input("gid", value=st.session_state.get("gid", str(int(DF.priority_score.idxmax()))))
    try:
        sel = int(q.strip())
        if sel not in DF.index:
            st.warning("gid не найден")
            sel = int(DF.priority_score.idxmax())
    except ValueError:
        sel = int(DF.priority_score.idxmax())
    st.session_state["gid"] = str(sel)
    r = DF.loc[sel]
    st.markdown(f"**{ROLE_RU[r.role]}** · приоритет **{r.priority_score:.3f}** · кластер {int(r.cluster_id)}")
    st.caption(r.evidence)
    st.divider()
    st.caption(f"Пайплайн: {META['runtime_sec']} с · пороги и модель — вкладка «Методика»")

tabs = st.tabs(["🎯 Топ-лист", "🕸 Схема сети", "🪪 Карточка узла", "🧩 Кластеры", "💥 Стресс-тест", "🤖 AI-аналитик", "📐 Методика"])

# ------------------------------------------------------------------ 1. топ-лист
with tabs[0]:
    top = pd.read_csv(OUT / "top_nodes.csv", dtype={"gid": str})
    st.subheader("Приоритет углублённой проверки")
    st.dataframe(top, width="stretch", hide_index=True,
                 column_config={"priority_score": st.column_config.ProgressColumn("priority", min_value=0, max_value=1),
                                "why": st.column_config.TextColumn("обоснование", width="large")})
    st.caption("Приоритет = 0.30·роль×уверенность + 0.25·близость к seed + 0.20·оборот + 0.15·PageRank + 0.10·посредничество; "
               "известные seed ×0.6 — фокус на тех, кто выше по цепочке.")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Карта всей сети")
        xs, ys, cs, tx_ = [], [], [], []
        for g, (x, y) in POS.items():
            rr = DF.loc[g]
            xs.append(x); ys.append(y); cs.append(COLORS[rr.role])
            tx_.append(f"{g}<br>{ROLE_RU[rr.role]} · {rr.priority_score:.2f}")
        fig = go.Figure(go.Scattergl(x=xs, y=ys, mode="markers", text=tx_, hoverinfo="text",
                                     marker=dict(color=cs, size=[4 + 14 * DF.at[g, 'priority_score'] for g in POS], opacity=0.8)))
        fig.update_layout(height=480, margin=dict(l=0, r=0, t=0, b=0), xaxis_visible=False, yaxis_visible=False,
                          plot_bgcolor="white")
        st.plotly_chart(fig, width="stretch")
    with col2:
        st.subheader("Роли по коленам")
        pv = DF.pivot_table(index="depth", columns="role", values="priority_score", aggfunc="size", fill_value=0)
        st.dataframe(pv, width="stretch")
        st.caption("4-е колено: исходящие не выгружены (обход оборван). Роль таких узлов определяется моделью "
                   "P(пересылает дальше), обученной на коленах 1–3, где исходящие известны.")

# ------------------------------------------------------------------ 2. схема сети
with tabs[1]:
    a, b, cc = st.columns([2, 1, 1])
    mode = a.radio("Показать", ["Окружение выбранного gid", "Кластер выбранного gid", "Топ-40 и их связи"], horizontal=True)
    radius = b.slider("Радиус (переводов)", 1, 3, 1)
    legend()
    if mode.startswith("Окружение"):
        nodes = ego(sel, radius)
    elif mode.startswith("Кластер"):
        nodes = set(DF.index[DF.cluster_id == DF.at[sel, "cluster_id"]])
    else:
        top40 = set(DF.sort_values("priority_score", ascending=False).head(40).index)
        nodes = set(top40)
        for u, v in G.edges:
            if u in top40 and v in top40:
                nodes |= {u, v}
    cc.metric("На схеме", f"{len(nodes)} узлов")
    draw(nodes, focus={sel})

# ------------------------------------------------------------------ 3. карточка
with tabs[2]:
    st.markdown(T.node_card(sel))
    nb = T.neighbors(sel, "both", 20)
    c1, c2 = st.columns(2)
    for col, key, title in [(c1, "payers", "⬅️ Кто платил"), (c2, "receivers", "➡️ Кому платил")]:
        with col:
            st.subheader(title)
            if nb.get(key):
                d = pd.DataFrame(nb[key])[["gid", "role_ru", "sum_kzt", "n_tx", "priority_score"]]
                d["gid"] = d.gid.astype(str)
                st.dataframe(d, hide_index=True, width="stretch")
            else:
                st.caption("нет" + (" (4-е колено: исходящие не выгружены)" if key == "receivers" and DF.at[sel, "depth"] == 4 else ""))
    st.subheader("Движение денег по датам")
    tx = T.state()["tx"]
    t = tx[(tx.src == sel) | (tx.dst == sel)].copy()
    t["направление"] = np.where(t.dst == sel, "вход", "выход")
    t["src"], t["dst"] = t.src.astype(str), t.dst.astype(str)
    st.dataframe(t.sort_values("date"), hide_index=True, width="stretch")
    st.subheader("Куда уходят деньги (крупнейшие потоки)")
    for p in T.trace_money(sel, 4, 2)["paths"][:6]:
        st.markdown(" → ".join(f"`{str(g)[-6:]}` {ROLE_RU[ro]}" for g, ro in zip(p["path"], p["roles"])))

# ------------------------------------------------------------------ 4. кластеры
with tabs[3]:
    view = CT.copy()
    view["top_gids"] = view.top_gids.astype(str)
    st.dataframe(view, hide_index=True, width="stretch",
                 column_config={"hypothesis": st.column_config.TextColumn(width="large")})
    cid = st.selectbox("Показать кластер", view.cluster_id.tolist())
    info = T.cluster_info(cid)
    st.info(info["hypothesis"])
    legend()
    draw(set(DF.index[DF.cluster_id == cid]), focus={int(x["gid"]) for x in info["key_nodes"][:3]}, height=560)

# ------------------------------------------------------------------ 5. стресс-тест
with tabs[4]:
    st.subheader("Что будет с сетью, если заблокировать ключевые узлы?")
    n = st.slider("Заблокировать топ-N узлов (без seed)", 1, 50, 10)
    r = T.simulate_removal(top_n=n)
    c1, c2, c3 = st.columns(3)
    c1.metric("Достижимость seed → узел", fmt(r["after"]["seed_reach_pairs"]), f"-{r['drop_pct']['seed_reach_pairs']}%", delta_color="inverse")
    c2.metric("Оборот в графе, ₸", f"{r['after']['flow_kzt'] / 1e6:.0f} млн", f"-{r['drop_pct']['flow_kzt']}%", delta_color="inverse")
    c3.metric("Крупнейшая компонента", r["after"]["largest_component"], f"-{r['drop_pct']['largest_component']}%", delta_color="inverse")
    curve = []
    for k in [0, 5, 10, 20, 30, 50]:
        rr = T.simulate_removal(top_n=k) if k else {"drop_pct": {"seed_reach_pairs": 0}}
        rnd = DF[~DF.is_seed].sample(k, random_state=1).index.tolist() if k else []
        rr2 = T.simulate_removal(gids=rnd) if k else {"drop_pct": {"seed_reach_pairs": 0}}
        curve.append((k, rr["drop_pct"]["seed_reach_pairs"], rr2["drop_pct"]["seed_reach_pairs"]))
    cdf = pd.DataFrame(curve, columns=["N", "по нашему приоритету", "случайные узлы"]).set_index("N")
    st.line_chart(cdf, y_label="падение достижимости, %")
    st.caption("Сравнение с блокировкой случайных узлов показывает, что приоритет выделяет действительно несущие узлы сети.")

# ------------------------------------------------------------------ 6. AI-аналитик
with tabs[5]:
    import agent
    st.subheader("Спросите граф на естественном языке")
    st.caption("Агент сам вызывает инструменты графа (те же, что в MCP-сервере). Без OPENAI_API_KEY — режим правил.")
    ex = st.columns(3)
    # пример: seed, чьи деньги за <= 2 перевода доходят до топ-узла — агент должен найти его сам
    top1 = DF[~DF.is_seed].priority_score.idxmax()
    rev = nx.single_source_shortest_path_length(G.reverse(copy=False), top1, cutoff=2)
    s3 = [g for g in rev if DF.at[g, "is_seed"]][:3] or DF[DF.is_seed].index[:3].tolist()
    examples = [f"Кто собирает деньги с {' '.join(map(str, s3))}?", "Кого из не-seed смотреть первым?",
                "Что будет, если заблокировать топ-10?"]
    for i, e in enumerate(examples):
        if ex[i].button(e[:60] + ("…" if len(e) > 60 else ""), key=f"ex{i}"):
            st.session_state["q"] = e
    q = st.text_area("Вопрос", value=st.session_state.get("q", examples[0]), height=80)
    if st.button("Спросить", type="primary"):
        with st.spinner("Агент работает с графом…"):
            res = agent.ask(q)
        st.session_state["ai"] = res
    res = st.session_state.get("ai")
    if res:
        st.markdown(res["answer"])
        with st.expander(f"Шаги агента ({len(res['steps'])}) · режим {res['mode']}"):
            for name, args, out in res["steps"]:
                st.markdown(f"**{name}**(`{json.dumps(args, ensure_ascii=False, default=str)}`)")
                st.json(json.loads(json.dumps(out, ensure_ascii=False, default=str)) if not isinstance(out, str) else {"text": out[:2000]}, expanded=False)
        if res["highlight"]:
            st.caption("Найденные узлы на схеме:")
            hl = set(res["highlight"])
            nodes = set(hl)
            for g in hl:
                nodes |= set(G.predecessors(g)) | set(G.successors(g))
            draw(nodes if len(nodes) < 250 else hl, focus=hl, height=520)

# ------------------------------------------------------------------ 7. методика
with tabs[6]:
    st.subheader("Правила ролей (пороги)")
    st.json(META["thresholds"])
    st.markdown("""
| Роль | Формальное правило |
|---|---|
| **distributor** | ≥ 10 разных получателей |
| **consolidator** | ≥ 4 разных плательщиков; скор растёт с числом плательщиков и долей удержанных средств |
| **transit** | есть вход и выход, коэффициент пропуска out/in ∈ [0.8; 1.2], не seed; бонус за быстрый (≤ 2 дн.) транзит |
| **terminal** | исходящих нет И (≥ 2 плательщиков или ≥ 100 тыс ₸); для 4-го колена — только если P(пересылает) < 0.35 |
| **coordinator** | не seed; деньги ≥ 3 seed доходят за ≤ 2 перевода; ≥ 1 структурный посредник на входе и ≥ 2 связей с посредниками; вход ≥ 75-го перцентиля |
| **peripheral** | ни одно правило не сработало (в т. ч. мелкие разовые получатели и 4-е колено с неопределённым поведением) |
""")
    st.subheader("Модель для обрезанного 4-го колена")
    st.json(META["forward_model"])
    st.subheader("Устойчивость сети (пайплайн)")
    st.json(META["resilience"])
