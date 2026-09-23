/* Граф денег — фронтенд. Все gid — строки (18-значные числа не помещаются в JS Number). */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const S = { summary: null, mode: "top", radius: 1, sel: null, net: null, nodes: null, edges: null, removed: new Set(), flowTimer: null };

const api = async (url, opts) => {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
};
const fmtKZT = (x) => x >= 1e6 ? (x / 1e6).toFixed(1).replace(".", ",") + " млн ₸" : x >= 1e3 ? Math.round(x / 1e3) + " тыс ₸" : Math.round(x) + " ₸";
const fmtN = (x) => Number(x).toLocaleString("ru-RU");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const short = (g) => "…" + String(g).slice(-6);
const roleBadge = (role) => {
  const c = S.summary.colors[role];
  return `<span class="badge" style="color:${c};border-color:${c}55;background:${c}18">● ${S.summary.role_ru[role]}</span>`;
};
const loader = (on) => $("#loader").classList.toggle("hidden", !on);

/* ---------------- markdown (минимальный) + кликабельные gid */
function md(text) {
  let h = esc(text);
  h = h.replace(/^### (.*)$/gm, "<h4>$1</h4>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(^|\s)_(.+?)_(?=\s|$|[.,])/g, "$1<i>$2</i>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  const lines = h.split("\n"); let out = "", list = null;
  for (const l of lines) {
    const ul = l.match(/^\s*[-•] (.*)/), ol = l.match(/^\s*\d+\. (.*)/);
    if (ul || ol) {
      const t = ul ? "ul" : "ol";
      if (list !== t) { if (list) out += `</${list}>`; out += `<${t}>`; list = t; }
      out += `<li>${(ul || ol)[1]}</li>`;
    } else {
      if (list) { out += `</${list}>`; list = null; }
      out += l.trim() ? `<p style="margin:4px 0">${l}</p>` : "";
    }
  }
  if (list) out += `</${list}>`;
  return out.replace(/\b(\d{15,20})\b/g, '<span class="gidlink" data-gid="$1">$1</span>');
}
document.addEventListener("click", (e) => {
  const g = e.target.closest("[data-gid]");
  if (g) { selectNode(g.dataset.gid, true); }
});

/* ---------------- граф */
function initGraph() {
  S.nodes = new vis.DataSet(); S.edges = new vis.DataSet();
  S.net = new vis.Network($("#graph"), { nodes: S.nodes, edges: S.edges }, {
    autoResize: true,
    nodes: { shape: "dot", font: { color: "#cbd5e1", size: 11, face: "system-ui", strokeWidth: 3, strokeColor: "#070b16" }, borderWidth: 1.5 },
    edges: { arrows: { to: { enabled: true, scaleFactor: 0.55 } }, smooth: { type: "continuous" }, color: { color: "#475569", opacity: 0.55, highlight: "#fbbf24", hover: "#94a3b8" }, selectionWidth: 2 },
    physics: { solver: "barnesHut", barnesHut: { gravitationalConstant: -9000, springLength: 130, springConstant: 0.03, damping: 0.25 }, stabilization: { iterations: 250 } },
    interaction: { hover: true, tooltipDelay: 120, navigationButtons: false, keyboard: false },
    layout: { improvedLayout: false },
  });
  S.net.on("click", (p) => { if (p.nodes.length) selectNode(p.nodes[0], false); });
  S.net.on("doubleClick", (p) => { if (p.nodes.length) { S.sel = p.nodes[0]; setMode("ego"); } });
  S.net.on("stabilizationIterationsDone", () => { S.net.setOptions({ physics: false }); S.net.fit({ animation: { duration: 500 } }); });
}

function toVisNode(n, opts = {}) {
  const removed = S.removed.has(n.id);
  const c = n.color;
  const size = 8 + 26 * n.priority + (opts.focus ? 10 : 0);
  return {
    id: n.id, label: n.label, shape: n.seed ? "star" : "dot", size,
    color: removed ? { background: "#1e293b", border: "#ef4444" } : { background: c, border: opts.focus ? "#fbbf24" : c + "cc", highlight: { background: c, border: "#fbbf24" }, hover: { background: c, border: "#fff" } },
    borderWidth: opts.focus ? 4 : removed ? 2 : 1.5,
    shapeProperties: removed ? { borderDashes: [4, 3] } : {},
    font: { color: removed ? "#475569" : "#cbd5e1" },
    title: `${n.id}\n${n.role_ru} · уверенность ${n.score.toFixed(2)}\nприоритет ${n.priority.toFixed(3)} · колено ${n.depth}${n.seed ? " · seed" : ""}\n\n${n.evidence}`,
    level: n.depth, _raw: n,
  };
}
function renderGraph(g, { focus = [], hierarchical = false } = {}) {
  clearFlow();
  const fs = new Set(focus.map(String));
  const maxSum = Math.max(1, ...g.edges.map((e) => e.sum));
  S.net.setOptions({
    layout: hierarchical ? { hierarchical: { enabled: true, direction: "LR", levelSeparation: 230, nodeSpacing: 70, sortMethod: "directed" } } : { hierarchical: { enabled: false }, improvedLayout: false },
    physics: hierarchical ? { enabled: false } : { enabled: true },
  });
  S.nodes.clear(); S.edges.clear();
  S.nodes.add(g.nodes.map((n) => toVisNode(n, { focus: fs.has(n.id) })));
  S.edges.add(g.edges.map((e, i) => {
    const w = 0.6 + 5 * Math.pow(Math.log1p(e.sum) / Math.log1p(maxSum), 3);
    const hot = fs.size <= 2 ? (fs.has(e.from) || fs.has(e.to)) : (fs.has(e.from) && fs.has(e.to));
    const dead = S.removed.has(e.from) || S.removed.has(e.to);
    return { id: "e" + i, from: e.from, to: e.to, width: w, _w: w, title: `${fmtKZT(e.sum)} · ${e.n_tx} перев.`,
      color: dead ? { color: "#1e293b", opacity: 0.4 } : hot ? { color: "#f59e0b", opacity: 0.85 } : undefined };
  }));
  $("#graph-info").textContent = `${g.nodes.length} узлов · ${g.edges.length} связей · ★ seed · размер = приоритет · толщина = сумма`;
  if (hierarchical) setTimeout(() => S.net.fit({ animation: { duration: 400 } }), 60);
  else { S.net.setOptions({ physics: { enabled: true } }); S.net.stabilize(260); }
}

async function loadGraph() {
  loader(true);
  try {
    let g, opts = {};
    if (S.mode === "ego" && S.sel) { g = await api(`/api/graph?mode=ego&gid=${S.sel}&radius=${S.radius}`); opts.focus = [S.sel]; }
    else if (S.mode === "cluster" && S.sel) { const c = S.nodeData?.node?.cluster ?? 0; g = await api(`/api/graph?mode=cluster&cluster=${c}`); opts.focus = [S.sel]; }
    else if (S.mode === "flow" && S.sel) { await playFlow(S.sel); return; }
    else { g = await api(`/api/graph?mode=top&n=${Math.max(45, S.removed.size + 20)}`); opts.focus = S.sel ? [S.sel] : []; }
    renderGraph(g, opts);
  } finally { loader(false); }
}
function setMode(m) {
  S.mode = m;
  $$("#modes button").forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
  loadGraph();
}

/* ---------------- анимация пути денег */
function clearFlow() { if (S.flowTimer) { clearTimeout(S.flowTimer); S.flowTimer = null; } $("#flow-banner").classList.add("hidden"); }
async function playFlow(gid) {
  const f = await api(`/api/flow/${gid}`);
  renderGraph(f.graph, { focus: [gid], hierarchical: true });
  // все рёбра приглушаем
  S.edges.update(S.edges.get().map((e) => ({ id: e.id, color: { color: "#334155", opacity: 0.35 }, width: Math.max(0.6, e._w * 0.6) })));
  const edgeId = {};
  S.edges.get().forEach((e) => (edgeId[e.from + ">" + e.to] = e.id));
  const seq = [];
  f.upstream.forEach((p) => p.path.slice(1).forEach((to, i) => seq.push({ from: p.path[i], to, sum: p.sums[i], up: true })));
  f.downstream.forEach((p) => p.path.slice(1).forEach((to, i) => seq.push({ from: p.path[i], to, sum: p.sums[i], up: false })));
  const b = $("#flow-banner");
  b.innerHTML = `▶ Путь денег к узлу <b>${short(gid)}</b>: ${f.upstream.length} цепочек от seed-клиентов (слева) → узел → куда ушло дальше (справа). Колонки = колена обхода.`;
  b.classList.remove("hidden");
  let i = 0;
  const step = () => {
    if (i >= seq.length) { S.flowTimer = null; return; }
    const s = seq[i++], id = edgeId[s.from + ">" + s.to];
    if (id) S.edges.update({ id, color: { color: s.up ? "#fbbf24" : "#38bdf8", opacity: 1 }, width: 5, label: fmtKZT(s.sum), font: { color: "#fde68a", size: 10, strokeWidth: 3, strokeColor: "#070b16", align: "top" } });
    S.nodes.update({ id: s.to, borderWidth: 5 });
    S.flowTimer = setTimeout(step, 320);
  };
  S.flowTimer = setTimeout(step, 500);
}

/* ---------------- выбор узла + карточка */
async function selectNode(gid, focusGraph) {
  gid = String(gid);
  S.sel = gid;
  $$(".item").forEach((it) => it.classList.toggle("sel", it.dataset.id === gid));
  switchTab("node");
  const d = await api(`/api/node/${gid}`);
  S.nodeData = d;
  renderNode(d);
  if (focusGraph) { if (S.mode === "top") setMode("ego"); else loadGraph(); }
  else if (S.nodes.get(gid)) S.net.selectNodes([gid]);
}

function renderNode(d) {
  const n = d.node, i = d.info, c = S.summary.colors[n.role];
  const pt = i.pass_through == null ? "—" : Math.round(i.pass_through * 100) + "%";
  const flags = [];
  if (i.fast_transit_share >= 0.5) flags.push(`${Math.round(i.fast_transit_share * 100)}% исходящих ушло в течение 2 дней после поступления`);
  if (i.max_payers_same_day >= 3) flags.push(`синхронные поступления: до ${i.max_payers_same_day} плательщиков в один день`);
  if (i.cycles) flags.push(`участвует в ${i.cycles} циклах возврата средств (≤6 шагов)`);
  if (n.depth === 4) flags.push(`4-е колено: исходящие не выгружены. P(пересылает дальше) = ${i.p_forward.toFixed(2)} → запросить выписку`);
  if (n.seed) flags.push("seed: входящие извне выборки не видны, отдал/получил некорректно");
  const prioNames = { role: "роль × уверенность", reach: "близость к seed", money: "оборот", pagerank: "PageRank", betw: "посредничество" };
  const prioW = { role: 0.30, reach: 0.25, money: 0.20, pagerank: 0.15, betw: 0.10 };
  const peers = (arr, dir) => (arr || []).slice(0, 8).map((p) => `
    <div class="peer" data-gid="${p.gid}"><span><span class="g">${short(p.gid)}</span> ${roleBadge(p.role)}</span><span>${dir} ${fmtKZT(p.sum_kzt)}</span></div>`).join("") || '<div class="muted small">нет' + (dir === "→" && n.depth === 4 ? " (исходящие не выгружены)" : "") + "</div>";
  $("#tab-node").innerHTML = `
    <div class="node-head">
      <div><div class="muted small">gid · колено ${n.depth} · кластер ${n.cluster}${n.seed ? " · <b style='color:var(--accent)'>★ seed</b>" : ""}</div>
      <div class="node-gid">${n.id}</div></div>
      <div style="text-align:right"><div class="muted small">приоритет</div><div style="font-size:22px;font-weight:800;color:var(--accent)">${n.priority.toFixed(2)}</div><div class="muted small">место ${i.priority_rank} из ${fmtN(S.summary.nodes)}</div></div>
    </div>
    <div class="node-role" style="color:${c}">${S.summary.role_ru[n.role]}</div>
    <div class="conf">уверенность <div class="bar"><div style="width:${n.score * 100}%;background:${c}"></div></div> ${n.score.toFixed(2)}</div>
    <div class="evidence">${esc(n.evidence)}</div>
    <div class="grid">
      <div class="metric"><b>${fmtKZT(i.in_kzt)}</b><span>получил от ${i.in_deg} плательщ. (${i.in_tx} перев.)</span></div>
      <div class="metric"><b>${fmtKZT(i.out_kzt)}</b><span>отправил ${i.out_deg} получат. (${i.out_tx} перев.)</span></div>
      <div class="metric"><b>${pt}</b><span>пропуск дальше (out/in)</span></div>
      <div class="metric"><b>${i.near_seeds}</b><span>seed доходят за ≤2 перевода</span></div>
    </div>
    <div class="btn-row">
      <button class="btn primary" id="btn-flow">▶ Путь денег</button>
      <button class="btn" id="btn-ego">Окружение</button>
      <button class="btn" id="btn-ask">🤖 Спросить AI об узле</button>
    </div>
    ${flags.length ? `<div class="block flags"><h4>На что обратить внимание</h4>${flags.map((f) => `<div>⚠ ${esc(f)}</div>`).join("")}</div>` : ""}
    <div class="block"><h4>Из чего сложился приоритет</h4>
      ${Object.keys(prioNames).map((k) => `<div class="hbar"><span>${prioNames[k]} <span class="muted">×${prioW[k]}</span></span><div class="t"><div style="width:${d.prio[k] * 100}%"></div></div><span>${d.prio[k].toFixed(2)}</span></div>`).join("")}
    </div>
    <div class="block"><h4>Кто платил (${i.in_deg})</h4>${peers(d.neighbors.payers, "←")}</div>
    <div class="block"><h4>Кому платил (${i.out_deg})</h4>${peers(d.neighbors.receivers, "→")}</div>
    <div class="block"><h4>Переводы по датам (июль 2026)</h4>${txChart(d.tx)}</div>
    <div class="disclaimer">Гипотеза для углублённой проверки, не вывод о виновности.</div>`;
  $("#btn-flow").onclick = () => setMode("flow");
  $("#btn-ego").onclick = () => setMode("ego");
  $("#btn-ask").onclick = () => { switchTab("ai"); ask(`Объясни роль ${n.id} и кто его основные контрагенты`); };
}

function txChart(tx) {
  if (!tx.length) return '<div class="muted small">нет переводов</div>';
  const W = 400, H = 90, byDay = {};
  tx.forEach((t) => { const d = +t.date.slice(8, 10); byDay[d] = byDay[d] || { in: 0, out: 0 }; byDay[d][t.dir] += t.sum; });
  const mx = Math.max(...Object.values(byDay).map((v) => Math.max(v.in, v.out)));
  let bars = "";
  for (let d = 1; d <= 31; d++) {
    const x = 6 + (d - 1) * ((W - 12) / 31), v = byDay[d] || { in: 0, out: 0 }, hi = (v.in / mx) * 38, ho = (v.out / mx) * 38;
    if (v.in) bars += `<rect x="${x}" y="${45 - hi}" width="8" height="${hi}" fill="#22c55e"><title>${d} июля: вход ${fmtKZT(v.in)}</title></rect>`;
    if (v.out) bars += `<rect x="${x}" y="45" width="8" height="${ho}" fill="#ef4444"><title>${d} июля: выход ${fmtKZT(v.out)}</title></rect>`;
    if (d % 5 === 1) bars += `<text x="${x}" y="${H}" fill="#64748b" font-size="9">${d}</text>`;
  }
  return `<svg viewBox="0 0 ${W} ${H + 2}"><line x1="0" y1="45" x2="${W}" y2="45" stroke="#1e2a44"/>${bars}</svg>
    <div class="muted small"><span style="color:#22c55e">■</span> вход &nbsp; <span style="color:#ef4444">■</span> выход · ${tx.length} переводов</div>`;
}

/* ---------------- AI-аналитик */
async function ask(q) {
  q = q.trim(); if (!q) return;
  const chat = $("#chat");
  chat.insertAdjacentHTML("beforeend", `<div class="msg user">${esc(q)}</div>`);
  const bot = document.createElement("div"); bot.className = "msg bot";
  bot.innerHTML = `<div class="meta">агент подключается к MCP-серверу…</div><div class="typing"><i></i><i></i><i></i></div>`;
  chat.appendChild(bot); chat.scrollTop = chat.scrollHeight;
  try {
    const r = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
    const transport = r.transport === "mcp" ? "MCP-сервер graph-intel" : "прямой вызов (MCP недоступен)";
    bot.innerHTML = `<div class="meta">${r.mode.startsWith("llm") ? "LLM " + esc(r.mode.slice(4)) : "режим правил (без LLM)"} · ${transport} · ${r.ms} мс</div><div class="steps"></div><div class="ans"></div>`;
    const stepsEl = bot.querySelector(".steps");
    for (const [k, s] of r.steps.entries()) {
      await new Promise((res) => setTimeout(res, 260));
      stepsEl.insertAdjacentHTML("beforeend", `<details class="step"><summary><span class="via ${s.via}">${s.via === "mcp" ? "MCP" : "direct"}</span>${k + 1}. <b>${esc(s.tool)}</b>(${esc(JSON.stringify(s.args)).slice(0, 90)}) <span class="muted">· ${s.ms} мс</span></summary><pre>${esc(s.preview)}</pre></details>`);
      chat.scrollTop = chat.scrollHeight;
    }
    await new Promise((res) => setTimeout(res, 200));
    bot.querySelector(".ans").innerHTML = md(r.answer) + (r.highlight.length ? `<div class="btn-row"><button class="btn primary hl">Показать ${r.highlight.length} узлов на графе</button></div>` : "");
    const hl = bot.querySelector(".hl");
    if (hl) hl.onclick = () => showIds(r.highlight);
    if (r.highlight.length) showIds(r.highlight);
  } catch (e) { bot.innerHTML = `<div class="meta">ошибка</div>${esc(e.message)}`; }
  chat.scrollTop = chat.scrollHeight;
}
async function showIds(ids) {
  loader(true);
  try {
    const g = await api(`/api/graph?mode=ids&ids=${ids.slice(0, 12).join(",")}`);
    S.mode = "ids"; $$("#modes button").forEach((b) => b.classList.remove("active"));
    renderGraph(g, { focus: ids });
    $("#graph-info").textContent = `AI-аналитик: ${ids.length} найденных узлов (жёлтая обводка) и их контрагенты`;
  } finally { loader(false); }
}

/* ---------------- кластеры */
async function renderClusters() {
  const cs = await api("/api/clusters");
  $("#clusters").innerHTML = `<div class="muted small" style="margin-bottom:8px">Louvain на неориентированной проекции (вес = log(1+сумма)). Кластеров: ${cs.length}. Отсортированы по числу seed.</div>` +
    cs.filter((c) => c.n_nodes > 1).map((c) => `
    <div class="cl" data-cl="${c.cluster_id}" data-lead="${c.lead_gid}">
      <div class="h"><span>Кластер ${c.cluster_id}</span><span class="muted">${c.n_nodes} узлов</span></div>
      <div class="hyp">${esc(c.hypothesis)}</div>
      <div class="nums"><span>★ ${c.n_seed} seed</span><span>${fmtKZT(c.sum_kzt_internal)} внутри</span><span class="muted">${esc(c.roles || "")}</span></div>
    </div>`).join("");
  $$(".cl").forEach((el) => el.onclick = async () => {
    const lead = el.dataset.lead;
    S.sel = String(lead); S.nodeData = await api(`/api/node/${lead}`);
    S.mode = "cluster"; $$("#modes button").forEach((b) => b.classList.toggle("active", b.dataset.mode === "cluster"));
    loader(true); try { renderGraph(await api(`/api/graph?mode=cluster&cluster=${el.dataset.cl}`), { focus: [lead] }); } finally { loader(false); }
    $("#graph-info").textContent += ` · кластер ${el.dataset.cl}`;
  });
}

/* ---------------- стресс-тест */
let stressT = null;
async function runStress() {
  const n = +$("#stress-n").value; $("#stress-n-val").textContent = n;
  const r = await api(`/api/simulate?top_n=${n}`);
  S.removed = new Set(r.removed);
  $("#stress-kpis").innerHTML = `
    <div class="metric"><b>−${r.drop_pct.seed_reach_pairs}%</b><span>достижимость seed → узел</span></div>
    <div class="metric"><b>−${r.drop_pct.flow_kzt}%</b><span>оборот в сети</span></div>
    <div class="metric"><b>−${r.drop_pct.largest_component}%</b><span>крупнейшая компонента</span></div>`;
  chart(r.curve, n);
  if (S.mode !== "top") setMode("top"); else loadGraph();
  $("#graph-info").textContent = `Стресс-тест: заблокировано ${n} узлов (тёмные, пунктирная красная обводка)`;
}
function chart(curve, n) {
  const W = 400, H = 210, P = 34, mx = 100, xs = curve.map((p) => p.n), xMax = Math.max(...xs);
  const X = (v) => P + (v / xMax) * (W - P - 10), Y = (v) => H - P + 6 - (v / mx) * (H - P - 10);
  const line = (k, col) => `<polyline fill="none" stroke="${col}" stroke-width="2.5" points="${curve.map((p) => X(p.n) + "," + Y(p[k])).join(" ")}"/>` + curve.map((p) => `<circle cx="${X(p.n)}" cy="${Y(p[k])}" r="3" fill="${col}"><title>${p.n}: −${p[k]}%</title></circle>`).join("");
  let grid = "";
  for (let v = 0; v <= 100; v += 25) grid += `<line x1="${P}" x2="${W - 10}" y1="${Y(v)}" y2="${Y(v)}" stroke="#1e2a44"/><text x="4" y="${Y(v) + 4}" fill="#64748b" font-size="10">${v}%</text>`;
  xs.forEach((v) => (grid += `<text x="${X(v) - 5}" y="${H}" fill="#64748b" font-size="10">${v}</text>`));
  $("#stress-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H + 4}">${grid}<line x1="${X(n)}" x2="${X(n)}" y1="10" y2="${H - P + 6}" stroke="#fbbf24" stroke-dasharray="3 3"/>${line("random", "#64748b")}${line("priority", "#fbbf24")}</svg>
    <div class="small"><span style="color:#fbbf24">━</span> блокировка по нашему приоритету &nbsp; <span style="color:#64748b">━</span> случайные узлы</div>
    <div class="muted small" style="margin-top:6px">Разрыв между линиями — доказательство, что приоритет выделяет несущие узлы сети, хотя разметки ролей в данных нет.</div>`;
}

/* ---------------- методика */
function renderMethod() {
  const s = S.summary, m = s.model, t = s.thresholds;
  $("#method").innerHTML = `
    <h3>Правила ролей</h3>
    <table class="t">
      <tr><th>Роль</th><th>Правило</th></tr>
      <tr><td>${roleBadge("distributor")}</td><td>≥ ${t.distributor_min_receivers} разных получателей</td></tr>
      <tr><td>${roleBadge("consolidator")}</td><td>≥ ${t.consolidator_min_payers} разных плательщиков; скор растёт с числом плательщиков и долей удержанного</td></tr>
      <tr><td>${roleBadge("transit")}</td><td>вход и выход, out/in ∈ [${t.transit_pt_low}; ${t.transit_pt_high}], не seed; бонус за уход ≤ ${t.transit_fast_days} дн.</td></tr>
      <tr><td>${roleBadge("terminal")}</td><td>исходящих нет и (≥ 2 плательщиков или ≥ ${fmtKZT(t.terminal_min_kzt)}); для 4-го колена только при P(пересылает) &lt; ${t.terminal_fwd_prob_max}</td></tr>
      <tr><td>${roleBadge("coordinator")}</td><td>не seed; деньги ≥ ${t.coordinator_min_near_seeds} seed за ≤ 2 перевода; ≥ 1 посредник на входе, ≥ ${t.coordinator_min_role_payers} связей с посредниками; вход ≥ ${t.coordinator_min_in_kzt_pct * 100}-го перцентиля</td></tr>
      <tr><td>${roleBadge("peripheral")}</td><td>ни одно правило не сработало</td></tr>
    </table>
    <h3>Приоритет проверки</h3>
    <div class="evidence">0.30·роль×уверенность + 0.25·близость к seed + 0.20·оборот + 0.15·PageRank + 0.10·посредничество; seed ×0.6 (они уже известны — фокус на тех, кто выше)</div>
    <h3>Ловушка: обрыв на 4-м колене</h3>
    <div class="small">Ребро всегда записано на колене плательщика + 1: исходящие колен 0–3 собраны полностью, у 4-го — нет вообще (${s.depth4.total} узлов). Логистическая регрессия обучена на ${m.train_size} узлах колен 1–3 по входящему поведению.</div>
    <div class="grid">
      <div class="metric"><b>${m.holdout_auc}</b><span>AUC, отложенная выборка (бейзлайн ${m.baseline_auc_in_deg_only})</span></div>
      <div class="metric"><b>${m.boundary_test_train_depth_1_2_test_depth_3_auc}</b><span>AUC, граница 1–2 → 3 (бейзлайн ${m.boundary_test_baseline_auc})</span></div>
    </div>
    <div class="small muted">Перенос между коленами слабый, поэтому порог консервативный: terminal только ${s.depth4.terminal} из ${s.depth4.total} узлов 4-го колена, остальные честно помечены как неопределённые.</div>
    <h3 style="margin-top:14px">Архитектура</h3>
    <div class="small">Web UI → <b>FastAPI</b> (<a href="/docs" target="_blank">/docs</a>) → AI-агент (LLM + tool calling) → <b>MCP-клиент</b> → <b>MCP-сервер graph-intel</b> (8 tools) → граф. Пайплайн: ${s.runtime_sec} с, 8 автопроверок контрактов ТЗ.</div>`;
}

/* ---------------- вкладки, поиск, старт */
function switchTab(t) {
  const leavingStress = $("#tabs button.active")?.dataset.tab === "stress" && t !== "stress";
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
  if (leavingStress && S.removed.size) {  // блокировка — только сценарий стресс-теста, в других видах не показываем
    S.removed = new Set();
    if (S.mode === "top") loadGraph();
  }
  $$(".tab").forEach((el) => el.classList.toggle("hidden", el.id !== "tab-" + t));
  if (t === "stress") runStress();
}
$$("#tabs button").forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
$$("#modes button").forEach((b) => (b.onclick = () => {
  if (b.dataset.mode !== "top" && !S.sel) return alert("Сначала выберите узел в списке или на графе");
  if (b.dataset.mode === "top") S.removed.size && (S.removed = S.removed);
  setMode(b.dataset.mode);
}));
$$("#radius button").forEach((b) => (b.onclick = () => {
  S.radius = +b.dataset.r; $$("#radius button").forEach((x) => x.classList.toggle("active", x === b));
  if (S.mode === "ego") loadGraph();
}));
$("#btn-fit").onclick = () => S.net.fit({ animation: { duration: 400 } });
$("#stress-n").oninput = () => { $("#stress-n-val").textContent = $("#stress-n").value; clearTimeout(stressT); stressT = setTimeout(runStress, 250); };
$("#ask-form").onsubmit = (e) => { e.preventDefault(); const v = $("#ask-input").value; $("#ask-input").value = ""; ask(v); };
$("#ask-input").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask-form").requestSubmit(); } };

let searchT = null;
$("#search").oninput = () => {
  clearTimeout(searchT);
  const q = $("#search").value.trim(), box = $("#search-res");
  if (q.length < 2) return box.classList.remove("show");
  searchT = setTimeout(async () => {
    const res = await api(`/api/search?q=${encodeURIComponent(q)}`);
    box.innerHTML = res.length ? res.map((n) => `<div class="item" data-gid="${n.id}"><span></span><div><div class="gid">${n.id}</div>${roleBadge(n.role)}</div><span class="p">${n.priority.toFixed(2)}</span></div>`).join("") : '<div class="muted small" style="padding:10px">не найдено</div>';
    box.classList.add("show");
  }, 200);
};
document.addEventListener("click", (e) => { if (!e.target.closest(".search")) $("#search-res").classList.remove("show"); });

async function health() {
  try {
    const h = await api("/api/health");
    $("#pill-mcp").className = "pill " + (h.mcp ? "on" : "off");
    $("#pill-mcp").title = h.mcp ? `MCP-сервер онлайн: ${h.mcp_tools.join(", ")}` : "MCP-сервер не отвечает — агент вызывает инструменты напрямую";
    $("#pill-mcp").lastChild.textContent = h.mcp ? `MCP · ${h.mcp_tools.length} tools` : "MCP offline";
    $("#pill-llm").className = "pill " + (h.llm ? "on" : "off");
    $("#pill-llm").lastChild.textContent = h.llm ? "LLM" : "LLM: режим правил";
  } catch { }
}

async function start() {
  initGraph();
  S.summary = await api("/api/summary");
  const s = S.summary, R = s.roles;
  const kpi = (v, t, dot) => `<div class="kpi"><b>${v}</b><span>${dot ? `<i class="dot" style="background:${dot}"></i>` : ""}${t}</span></div>`;
  $("#kpis").innerHTML = kpi(fmtN(s.nodes), `узлов · ${s.seeds} seed`) + kpi((s.turnover / 1e6).toFixed(0) + " млн ₸", "оборот") +
    ["coordinator", "consolidator", "distributor", "transit", "terminal"].map((r) => kpi(R[r] || 0, s.role_ru[r], s.colors[r])).join("") + kpi(s.clusters, "кластеров");
  $("#legend").innerHTML = Object.keys(s.colors).map((r) => `<span><i style="background:${s.colors[r]}"></i>${s.role_ru[r]}</span>`).join("") + "<span>★ seed</span>";
  const top = await api("/api/top?n=30");
  $("#top-count").textContent = `· ${top.length}`;
  $("#toplist").innerHTML = top.map((n) => `
    <div class="item" data-id="${n.id}" onclick="selectNode('${n.id}', false)">
      <span class="rank">${n.rank}</span>
      <div><div class="gid">${n.id}</div><div style="margin-top:3px">${roleBadge(n.role)}</div><div class="bar"><div style="width:${n.priority * 100}%"></div></div></div>
      <span class="p">${n.priority.toFixed(2)}</span>
    </div>`).join("");
  const ex = await api("/api/examples");
  $("#examples").innerHTML = ex.map((q) => `<span class="chip" title="${esc(q)}">${esc(q)}</span>`).join("");
  $$(".chip").forEach((c) => (c.onclick = () => ask(c.title)));
  renderMethod(); renderClusters(); health(); setInterval(health, 30000);
  await loadGraph();
  selectNode(top[0].id, false);
}
start();
