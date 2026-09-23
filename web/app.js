/* Граф денег — фронтенд. Все gid — строки (18-значные числа не помещаются в JS Number). */
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const S = { summary: null, mode: "top", radius: 1, sel: null, net: null, nodes: null, edges: null, removed: new Set(), flowTimer: null, view: "node", graphRequest: 0, nodeRequest: 0, asking: false };

const api = async (url, opts) => {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
};
const fmtKZT = (x) => x >= 1e6 ? (x / 1e6).toFixed(1).replace(".", ",") + " млн ₸" : x >= 1e3 ? Math.round(x / 1e3) + " тыс ₸" : Math.round(x) + " ₸";
const fmtN = (x) => Number(x).toLocaleString("ru-RU");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const short = (g) => "…" + String(g).slice(-6);

const COLORS = { coordinator: "#bd8078", consolidator: "#c7a071", distributor: "#a593bb", transit: "#7aa0b6", terminal: "#80a893", peripheral: "#b1beb5" };
const INKS = { coordinator: "#985c56", consolidator: "#9b7646", distributor: "#807092", transit: "#577f96", terminal: "#567f69", peripheral: "#788e7e" };
const paths = {
 network: '<circle cx="5" cy="6" r="3"/><circle cx="19" cy="6" r="3"/><circle cx="12" cy="19" r="3"/><path d="M8 6h8M6.5 8.5l4 8M17.5 8.5l-4 8"/>',
 spark: '<path d="m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7L12 3ZM20 2v4M18 4h4"/>',
 grid: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
 shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
 book: '<path d="M12 5v16M3 4c4-1 6-1 9 1 3-2 5-2 9-1v15c-4-1-6-1-9 1-3-2-5-2-9-1V4Z"/>',
 search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
 download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
 arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
 users: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m2 4c3 1 3 3 3 6"/>',
 money: '<rect x="2" y="5" width="20" height="14" rx="3"/><circle cx="12" cy="12" r="3"/><path d="M6 12h.01M18 12h.01"/>',
 fit: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/><circle cx="12" cy="12" r="3"/>',
 info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.01"/>',
 copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V3H3v13h5"/>',
 close: '<path d="m6 6 12 12M6 18 18 6"/>'
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.info}</svg>`;
const roleBadge = (role) => `<span class="badge" style="color:${INKS[role]}"><i></i>${S.summary.role_ru[role]}</span>`;
const loader = (on) => $("#loader").classList.toggle("hidden", !on);
let toastTimer;
function toast(message) {
  const el = $("#toast"); el.textContent = message; el.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.add("hidden"), 4500);
}
const handle = (promise) => Promise.resolve(promise).catch((e) => toast("Не удалось выполнить действие: " + e.message));


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
  return out.replace(/\b(\d{15,20})\b/g, '<button class="gidlink" data-gid="$1">$1</button>');
}
document.addEventListener("click", (e) => {
  const g = e.target.closest("[data-gid]");
  if (g) { handle(selectNode(g.dataset.gid, true)); $("#search-res").classList.remove("show"); }
});

/* ---------------- граф */

function initGraph() {
  S.nodes = new vis.DataSet(); S.edges = new vis.DataSet();
  S.net = new vis.Network($("#graph"), { nodes: S.nodes, edges: S.edges }, {
    autoResize: true,
    nodes: { shape: "dot", font: { color: "#1f2937", size: 14, face: "Segoe UI", strokeWidth: 4, strokeColor: "#ffffff" }, borderWidth: 2 },
    edges: { arrows: { to: { enabled: true, scaleFactor: .7 } }, smooth: { type: "continuous" }, color: { color: "#94a3b8", opacity: .6, highlight: "#426e52", hover: "#64748b" }, selectionWidth: 1 },
    physics: { solver: "barnesHut", barnesHut: { gravitationalConstant: -8000, springLength: 140, avoidOverlap: 0.6, springConstant: .04, damping: .3 }, stabilization: { iterations: 220 } },
    interaction: { hover: true, tooltipDelay: 160, zoomView: true, dragView: true, zoomSpeed: .55, navigationButtons: false },
    layout: { improvedLayout: false, randomSeed: 42 }
  });
  S.net.on("click", (p) => { if (p.nodes.length) handle(selectNode(p.nodes[0], false)); });
  S.net.on("doubleClick", async (p) => {
    if (p.nodes.length) { await selectNode(p.nodes[0], false); await setMode("ego"); }
  });
  S.net.on("stabilizationIterationsDone", () => {
    S.net.setOptions({ physics: false });
    scheduleGraphFit();
  });
  S.net.on("resize", () => requestAnimationFrame(scheduleGraphFit));
  S.net.on("animationFinished", () => {
    graphFitActive = false;
    if (graphFitQueued && !graphViewManual) requestAnimationFrame(fitGraph);
  });
  // Cancel camera motion before vis handles the wheel or starts a drag.
  $("#graph").addEventListener("wheel", takeGraphControl, { capture: true, passive: true });
  $("#graph").addEventListener("pointerdown", takeGraphControl, { capture: true, passive: true });
  S.net.on("zoom", takeGraphControl); // Also covers touch pinch zoom.
}

let graphFitTimer, graphFitActive = false, graphFitQueued = false, graphViewManual = false;
function fitGraph() {
  if (graphViewManual || !S.net || !S.nodes.length || !$("#graph").clientWidth || !$("#graph").clientHeight) return;
  // Overlapping vis animations can leave a redraw callback that resets wheel zoom.
  if (graphFitActive) { graphFitQueued = true; return; }
  graphFitQueued = false;
  graphFitActive = true;
  S.net.fit({ animation: { duration: 400 } });
}
function scheduleGraphFit() {
  if (graphViewManual) return;
  clearTimeout(graphFitTimer);
  fitGraph();
  graphFitTimer = setTimeout(fitGraph, 300);
}
function takeGraphControl() {
  graphViewManual = true;
  clearTimeout(graphFitTimer);
  graphFitQueued = false;
  if (!graphFitActive) return;
  const position = S.net.getViewPosition(), scale = S.net.getScale();
  graphFitActive = false;
  // Advance even a just-started animation before cancelling through the public API.
  S.net.redraw();
  S.net.moveTo({ position, scale, animation: false });
}
function zoomGraph(factor) {
  if (!S.net) return;
  takeGraphControl();
  S.net.moveTo({ scale: Math.max(.08, Math.min(S.net.getScale() * factor, 4)), animation: false });
}

function refreshGraphSelection() {
  S.nodes.update(S.nodes.get().map(n => toVisNode(n._raw, {
    focus: S.mode === "ids" && n._focus, label: n._label
  })));
  if (S.nodes.get(S.sel)) {
    S.net.selectNodes([S.sel]);
  }
}

function toVisNode(n, opts = {}) {
  const removed = S.removed.has(n.id), selected = n.id === S.sel, c = COLORS[n.role];
  return {
    id: n.id, label: opts.label || opts.focus || selected ? short(n.id) : "", shape: n.seed ? "star" : "dot",
    size: n.seed ? 10 : 8 + 18 * n.priority,
    color: removed ? {
      background: "#cbd5e1", border: "#b45353",
      highlight: { background: "#cbd5e1", border: "#991b1b" },
      hover: { background: "#dbe2eb", border: "#991b1b" }
    } : { background: c, border: opts.focus || selected ? "#426e52" : "#ffffff",
      highlight: { background: c, border: "#426e52" }, hover: { background: c, border: "#739c80" } },
    borderWidth: removed ? 3 : opts.focus || selected ? 4 : 2,
    shapeProperties: removed ? { borderDashes: [4, 3] } : {},
    font: { color: "#1f2937", size: 14, strokeWidth: 4, strokeColor: "#ffffff" },
    title: `${n.id}\n${n.role_ru} · уверенность ${n.score.toFixed(2)}\nприоритет ${n.priority.toFixed(3)} · колено ${n.depth}${n.seed ? " · исходный клиент" : ""}\n\n${n.evidence}`,
    level: n.depth, _raw: n, _focus: !!opts.focus, _label: !!opts.label
  };
}


function renderGraph(g, { focus = [], hierarchical = false } = {}) {
  takeGraphControl();
  graphViewManual = false;
  clearFlow();
  const fs = new Set(focus.map(String));
  const maxSum = Math.max(1, ...g.edges.map((e) => e.sum));
  S.net.setOptions({
    layout: hierarchical ? { hierarchical: { enabled: true, direction: "LR", levelSeparation: 230, nodeSpacing: 120, sortMethod: "directed" } } : { hierarchical: { enabled: false }, improvedLayout: false },
    physics: hierarchical ? { enabled: false } : { enabled: true },
  });
  S.nodes.clear(); S.edges.clear();
  const labeled = new Set([...g.nodes].sort((a, b) => b.priority - a.priority).slice(0, 15).map(n => n.id));
  S.nodes.add(g.nodes.map((n) => toVisNode(n, { focus: fs.has(n.id), label: labeled.has(n.id) })));
  S.edges.add(g.edges.map((e, i) => {
    const w = 0.4 + 2.4 * Math.pow(Math.log1p(e.sum) / Math.log1p(maxSum), 3);
    const hot = fs.size <= 2 ? (fs.has(e.from) || fs.has(e.to)) : (fs.has(e.from) && fs.has(e.to));
    const dead = S.removed.has(e.from) || S.removed.has(e.to);
    return { id: "e" + i, from: e.from, to: e.to, width: w, _w: w, title: `${fmtKZT(e.sum)} · ${e.n_tx} перев.`,
      color: dead ? { color: "#94a3b8", opacity: 0.45 } : hot ? { color: "#527b60", opacity: 0.85 } : { color: "#94a3b8", opacity: 0.6 } };
  }));
  $("#graph-info").textContent = `${g.nodes.length} узлов · ${g.edges.length} связей`;
  $("#graph-title").textContent = ({top:"Карта ключевых связей",ego:"Окружение клиента",cluster:"Связи внутри кластера",flow:"Путь движения денег",ids:"Связи найденных клиентов"})[S.mode] || "Карта связей";
  if (!hierarchical) { S.net.setOptions({ physics: { enabled: true } }); S.net.stabilize(260); }
  requestAnimationFrame(scheduleGraphFit);
}


async function loadGraph() {
  const request = ++S.graphRequest;
  loader(true);
  try {
    let g, opts = {};
    if (S.mode === "ego" && S.sel) { g = await api(`/api/graph?mode=ego&gid=${S.sel}&radius=${S.radius}`); opts.focus = [S.sel]; }
    else if (S.mode === "cluster" && S.sel) { g = await api(`/api/graph?mode=cluster&cluster=${S.nodeData.node.cluster}`); opts.focus = [S.sel]; }
    else if (S.mode === "flow" && S.sel) { await playFlow(S.sel, request); return; }
    else { g = await api(`/api/graph?mode=top&n=${Math.max(45, S.removed.size + 20)}`); opts.focus = S.sel ? [S.sel] : []; }
    if (request !== S.graphRequest) return;
    renderGraph(g, opts);
  } catch (e) { if (request === S.graphRequest) toast("Не удалось загрузить граф: " + e.message); }
  finally { if (request === S.graphRequest) loader(false); }
}
async function setMode(m) {
  if (m !== "top" && !S.nodeData) { toast("Сначала выберите клиента в списке или на графе"); return; }
  S.mode = m;
  $$("#modes button").forEach(b => { b.classList.toggle("active", b.dataset.mode === m); b.setAttribute("aria-pressed", String(b.dataset.mode === m)); });
  $("#radius").classList.toggle("hidden", m !== "ego");
  await loadGraph();
}


/* ---------------- анимация пути денег */
function clearFlow() { if (S.flowTimer) { clearTimeout(S.flowTimer); S.flowTimer = null; } $("#flow-banner").classList.add("hidden"); }
async function playFlow(gid, request) {
  const f = await api(`/api/flow/${gid}`);
  if (request !== S.graphRequest) return;
  renderGraph(f.graph, { focus: [gid], hierarchical: true });
  // все рёбра приглушаем
  S.edges.update(S.edges.get().map((e) => ({ id: e.id, color: { color: "#94a3b8", opacity: 0.45 }, width: Math.max(0.6, e._w * 0.6) })));
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
    if (id) S.edges.update({ id, color: { color: s.up ? "#166534" : "#1d4ed8", opacity: 1 }, width: 5, label: fmtKZT(s.sum), font: { color: "#1f2937", size: 14, strokeWidth: 4, strokeColor: "#ffffff", align: "top" } });
    S.nodes.update({ id: s.to, borderWidth: 5 });
    S.flowTimer = setTimeout(step, 320);
  };
  S.flowTimer = setTimeout(step, 500);
}

/* ---------------- выбор узла + карточка */

async function selectNode(gid, focusGraph, openDetail = true) {
  gid = String(gid);
  const request = ++S.nodeRequest;
  try {
    const d = await api(`/api/node/${encodeURIComponent(gid)}`);
    if (request !== S.nodeRequest) return;
    S.sel = gid; S.nodeData = d;
    switchTab("node");
    document.body.classList.remove("detail-collapsed");
    document.body.classList.toggle("detail-open", openDetail);
    $$(".item").forEach(it => it.classList.toggle("sel", it.dataset.gid === gid));
    renderNode(d);
    if (focusGraph) await setMode(["top", "ids"].includes(S.mode) ? "ego" : S.mode);
    else if (["ego", "cluster", "flow"].includes(S.mode)) await loadGraph();
    else if (S.nodes.get(gid)) refreshGraphSelection();
  } catch (e) { toast("Клиент не найден или недоступен: " + e.message); }
}
function renderNode(d) {
  const n = d.node, i = d.info;
  const pt = i.pass_through == null ? "—" : Math.round(i.pass_through * 100) + "%";
  const flags = [];
  if (i.structuring_flag) flags.push(`Признаки дробления: ${Math.round(i.small_tx_share * 100)}% входящих переводов от 5 до 10 тыс ₸.`);
  if (i.anomaly_flag) flags.push(`Аномальный профиль: ${i.anomaly_reason}.`);
  if (i.fast_transit_share >= .5) flags.push(`${Math.round(i.fast_transit_share * 100)}% исходящих ушло в течение 2 дней после поступления.`);
  if (i.max_payers_same_day >= 3) flags.push(`До ${i.max_payers_same_day} плательщиков в один день.`);
  if (i.cycles) flags.push(`Участвует в ${i.cycles} циклах (до 6 шагов).`);
  if (n.depth === 4) flags.push(`Исходящие 4-го колена не выгружены. P(пересылает) = ${i.p_forward.toFixed(2)}. Запросите выписку.`);
  if (n.seed) flags.push("Исходный клиент: входящие извне выборки не видны, отношение выхода к входу неполно.");
  const prioNames = {role:"Роль × уверенность",reach:"Близость к seed",money:"Оборот",pagerank:"PageRank",betw:"Посредничество"};
  const prioW = {role:.30,reach:.25,money:.20,pagerank:.15,betw:.10};
  const peers = (arr, dir) => (arr || []).slice(0, 8).map(p => `<button class="peer" data-gid="${p.gid}" title="Открыть клиента ${p.gid}"><span><span class="g">${short(p.gid)}</span>${roleBadge(p.role)}</span><span>${dir} ${fmtKZT(p.sum_kzt)}</span></button>`).join("") || '<div class="empty-state">Нет переводов в выборке</div>';
  const disclosure = (title, count, content) => `<details class="disclosure"><summary>${title}<span>${count ?? ""}</span></summary>${content}</details>`;
  $("#tab-node").innerHTML = `
    <div class="node-top"><span class="node-avatar">${icon("users")}</span><div><small>Клиент сети</small><div class="node-short" title="${n.id}">${short(n.id)}</div></div><button class="icon" id="btn-copy-gid" aria-label="Скопировать полный gid" title="Скопировать полный gid">${icon("copy")}</button></div>
    <h2 class="node-role">${S.summary.role_ru[n.role]}</h2>
    <div class="node-context">Колено ${n.depth} <span>·</span> Кластер ${n.cluster}${n.seed ? " · Исходный клиент" : ""}</div>
    <div class="priority-card"><div class="priority-line"><span>Приоритет проверки</span><b>${n.priority.toFixed(2)}</b></div><div class="priority-track"><i style="width:${n.priority * 100}%"></i></div><small>№ ${i.priority_rank} из ${fmtN(S.summary.nodes)} клиентов</small></div>
    <div class="evidence"><span class="evidence-label">Почему стоит проверить</span>${esc(n.evidence)}</div>
    <div class="grid"><div class="metric"><span class="metric-label">Входящий оборот ↙</span><b>${fmtKZT(i.in_kzt)}</b><span>${i.in_deg} плательщиков · ${i.in_tx} переводов</span></div><div class="metric"><span class="metric-label">Исходящий оборот ↗</span><b>${fmtKZT(i.out_kzt)}</b><span>${i.out_deg} получателей · ${i.out_tx} переводов</span></div></div>
    <div class="btn-row node-actions"><button class="btn primary" id="btn-flow">${icon("arrow")}Путь денег</button><button class="btn" id="btn-ego">${icon("network")}Окружение</button><button class="btn ai-node-button" id="btn-ask">${icon("spark")}Спросить AI об этом клиенте</button></div>
    <div class="node-meta"><div><span>Полный gid</span><b class="node-gid">${n.id}</b></div><div><span>Уверенность в роли</span><b>${n.score.toFixed(2)}</b></div><div><span>Отправил / получил</span><b>${pt}</b></div><div><span>Seed в пределах двух переводов</span><b>${i.near_seeds}</b></div></div>
    ${flags.length ? disclosure("Сигналы для проверки", flags.length, flags.map(f => `<div class="flag">${esc(f)}</div>`).join("")) : ""}
    ${(i.data_requests || []).length ? disclosure("Какие данные запросить", i.data_requests.length, i.data_requests.map(r => `<div class="request-note">${esc(r.request)}<small>${esc(r.reason)}</small></div>`).join("")) : ""}
    ${disclosure("Из чего сложился приоритет", "", Object.keys(prioNames).map(k => `<div class="hbar"><span>${prioNames[k]} ×${prioW[k]}</span><div class="t"><div style="width:${d.prio[k]*100}%"></div></div><span>${d.prio[k].toFixed(2)}</span></div>`).join(""))}
    ${disclosure("Кто переводил деньги", i.in_deg, peers(d.neighbors.payers, "←"))}
    ${disclosure("Кому переводил деньги", i.out_deg, peers(d.neighbors.receivers, "→"))}
    ${disclosure("Переводы по датам", "Июль 2026", txChart(d.tx))}
    <div class="disclaimer">Гипотеза для углублённой проверки, не вывод о виновности.</div>`;
  $("#btn-flow").onclick = () => handle(setMode("flow"));
  $("#btn-ego").onclick = () => handle(setMode("ego"));
  $("#btn-ask").onclick = () => { switchTab("ai"); $("#ask-input").value = `Объясни роль ${n.id} и кто его основные контрагенты`; $("#ask-input").focus(); };
  $("#btn-copy-gid").onclick = async () => { try { await navigator.clipboard.writeText(n.id); toast("Полный gid скопирован"); } catch { toast("Копирование недоступно. Полный gid можно выделить ниже в карточке."); } };
}


function txChart(tx) {
  if (!tx.length) return '<div class="muted small">нет переводов</div>';
  const W = 400, H = 90, byDay = {};
  tx.forEach((t) => { const d = +t.date.slice(8, 10); byDay[d] = byDay[d] || { in: 0, out: 0 }; byDay[d][t.dir] += t.sum; });
  const mx = Math.max(...Object.values(byDay).map((v) => Math.max(v.in, v.out)));
  let bars = "";
  for (let d = 1; d <= 31; d++) {
    const x = 6 + (d - 1) * ((W - 12) / 31), v = byDay[d] || { in: 0, out: 0 }, hi = (v.in / mx) * 38, ho = (v.out / mx) * 38;
    if (v.in) bars += `<rect x="${x}" y="${45 - hi}" width="8" height="${hi}" fill="#79a48d"><title>${d} июля: вход ${fmtKZT(v.in)}</title></rect>`;
    if (v.out) bars += `<rect x="${x}" y="45" width="8" height="${ho}" fill="#c48980"><title>${d} июля: выход ${fmtKZT(v.out)}</title></rect>`;
    if (d % 5 === 1) bars += `<text x="${x}" y="${H}" fill="#83968a" font-size="9">${d}</text>`;
  }
  return `<svg class="transaction-chart" viewBox="0 0 ${W} ${H + 2}"><line x1="0" y1="45" x2="${W}" y2="45" stroke="#e3ebe5"/>${bars}</svg>
    <div class="muted small"><span style="color:#79a48d">■</span> вход &nbsp; <span style="color:#c48980">■</span> выход · ${tx.length} переводов</div>`;
}

/* ---------------- AI-аналитик */
async function ask(q) {
  q = q.trim(); if (!q || S.asking) return;
  S.asking = true; $("#ask-form button").disabled = true;
  $(".chat-empty")?.remove();
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
    if (hl) hl.onclick = () => handle(showIds(r.highlight));
    if (r.highlight.length) await showIds(r.highlight);
  } catch (e) { bot.innerHTML = `<div class="meta">ошибка</div>${esc(e.message)}`; }
  chat.scrollTop = chat.scrollHeight;
  S.asking = false; $("#ask-form button").disabled = false;
}

async function showIds(ids) {
  const request = ++S.graphRequest, shown = [...new Set(ids.map(String))].slice(0, 40);
  loader(true);
  try {
    const g = await api(`/api/graph?mode=ids&ids=${shown.join(",")}`);
    if (request !== S.graphRequest) return;
    S.mode = "ids"; $("#radius").classList.add("hidden");
    $$("#modes button").forEach(b => b.classList.remove("active"));
    renderGraph(g, {focus: shown});
    $("#graph-info").textContent = `${shown.length} найденных клиентов и их контрагенты`;
  } finally { if (request === S.graphRequest) loader(false); }
}


/* ---------------- кластеры */

async function renderClusters() {
  const cs = await api("/api/clusters");
  $("#clusters").innerHTML = `<div class="cluster-summary">${cs.length} кластеров · Louvain · показаны группы из двух и более клиентов</div>` +
    cs.filter(c => c.n_nodes > 1).map(c => `<button class="cl" data-cl="${c.cluster_id}" data-lead="${c.lead_gid}"><div class="h"><span>Кластер ${c.cluster_id}</span><span class="muted">${c.n_nodes} клиентов</span></div><div class="hyp">${esc(c.hypothesis)}</div><div class="nums"><span>★ ${c.n_seed} seed</span><span>${fmtKZT(c.sum_kzt_internal)}</span></div><div class="cluster-action">Исследовать связи ${icon("arrow")}</div></button>`).join("");
  $$(".cl").forEach(el => el.onclick = () => handle((async () => { await selectNode(el.dataset.lead, false); await setMode("cluster"); })()));
}


/* ---------------- стресс-тест */
let stressT = null;

async function runStress() {
  const n = +$("#stress-n").value;
  $("#stress-n-val").textContent = n;
  try {
    const r = await api(`/api/simulate?top_n=${n}`);
    if (S.view !== "stress" || n !== +$("#stress-n").value) return;
    S.removed = new Set(r.removed);
    $("#stress-kpis").innerHTML = `<div class="metric"><b>−${r.drop_pct.seed_reach_pairs}%</b><span>достижимость от seed</span></div><div class="metric"><b>−${r.drop_pct.flow_kzt}%</b><span>оборот в сети</span></div><div class="metric"><b>−${r.drop_pct.largest_component}%</b><span>крупнейшая компонента</span></div>`;
    chart(r.curve, n);
    await setMode("top");
    if (S.view === "stress") $("#graph-info").textContent = `Блокировка ${n} узлов · серые узлы с пунктирной обводкой`;
  } catch (e) { toast("Не удалось рассчитать сценарий: " + e.message); }
}


function chart(curve, n) {
  const W = 400, H = 210, P = 34, mx = 100, xs = curve.map((p) => p.n), xMax = Math.max(...xs);
  const X = (v) => P + (v / xMax) * (W - P - 10), Y = (v) => H - P + 6 - (v / mx) * (H - P - 10);
  const line = (k, col) => `<polyline fill="none" stroke="${col}" stroke-width="2.5" points="${curve.map((p) => X(p.n) + "," + Y(p[k])).join(" ")}"/>` + curve.map((p) => `<circle cx="${X(p.n)}" cy="${Y(p[k])}" r="3" fill="${col}"><title>${p.n}: −${p[k]}%</title></circle>`).join("");
  let grid = "";
  for (let v = 0; v <= 100; v += 25) grid += `<line x1="${P}" x2="${W - 10}" y1="${Y(v)}" y2="${Y(v)}" stroke="#e3ebe5"/><text x="4" y="${Y(v) + 4}" fill="#83968a" font-size="10">${v}%</text>`;
  xs.forEach((v) => (grid += `<text x="${X(v) - 5}" y="${H}" fill="#83968a" font-size="10">${v}</text>`));
  $("#stress-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H + 4}">${grid}<line x1="${X(n)}" x2="${X(n)}" y1="10" y2="${H - P + 6}" stroke="#427e59" stroke-dasharray="3 3"/>${line("random", "#83968a")}${line("priority", "#427e59")}</svg>
    <div class="small"><span style="color:#427e59">━</span> блокировка по нашему приоритету &nbsp; <span style="color:#83968a">━</span> случайные узлы</div>
    <div class="muted small" style="margin-top:6px">Сравнение показывает структурный эффект выбранного приоритета. Оно не подтверждает роли или виновность клиентов.</div>`;
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
    <h3 style="margin-top:14px">Дробление и аномальные профили</h3>
    <div class="small">Признаки дробления: не менее ${t.structuring_min_tx} входящих переводов, из них ≥ ${t.structuring_share * 100}% на сумму 5 000–9 999,99 ₸. IsolationForest выделяет верхние ${t.anomaly_top_share * 100}% активных узлов после нормировки признаков внутри колена. Объяснение — наибольшее отклонение признака; это сигнал для проверки.</div>
    <h3 style="margin-top:14px">Оценка полноты: какие данные запросить</h3>
    <div class="small muted">Топ-12 запросов по приоритету узла. Для одного gid возможны разные запросы.</div>
    <div id="data-requests" class="small">Загрузка рекомендаций…</div>
    <p class="small"><a href="/api/download/data_requests.csv">Скачать все запросы CSV</a></p>
    <h3 style="margin-top:14px">Архитектура</h3>
    <div class="small">Web UI → <b>FastAPI</b> (<a href="/docs" target="_blank">/docs</a>) → AI-агент (LLM + tool calling) → <b>MCP-клиент</b> → <b>MCP-сервер graph-intel</b> (9 tools) → граф. Пайплайн: ${s.runtime_sec} с, 8 автопроверок контрактов ТЗ.</div>`;
  renderDataRequests();
}

async function renderDataRequests() {
  const box = $("#data-requests");
  try {
    const rows = await api("/api/data_requests?n=12");
    box.innerHTML = rows.length ? rows.map((r) => `<div class="block"><span class="gidlink" data-gid="${esc(r.gid)}">${esc(r.gid)}</span> · приоритет ${r.priority_score.toFixed(3)}<br><b>${esc(r.request)}</b><br><span class="muted">${esc(r.reason)}</span></div>`).join("") : "Нет рекомендуемых запросов данных.";
  } catch (e) {
    box.textContent = `Не удалось загрузить запросы: ${e.message}`;
  }
}


/* Navigation, keyboard access and startup. All gid values stay strings. */
const views = {
 node: ["Вся сеть. Ясная картина.", "Находите ключевых участников и прослеживайте движение денег."],
 ai: ["Вопросы к данным. Ответы по делу.", "Исследуйте связи вместе с AI-аналитиком."],
 clusters: ["Сообщества внутри сети.", "От отдельных переводов — к структуре взаимодействий."],
 stress: ["Проверка на устойчивость.", "Оцените, какие участники сильнее всего влияют на связность сети."],
 method: ["Каждый вывод объясним.", "Правила анализа, ограничения и рекомендации по запросу данных."]
};
function switchTab(t) {
  if (!views[t]) return;
  const leavingStress = S.view === "stress" && t !== "stress";
  S.view = t; document.body.dataset.view = t;
  $("#page-title").textContent = views[t][0]; $("#page-description").textContent = views[t][1];
  $$("#tabs button").forEach(b => { b.classList.toggle("active", b.dataset.tab === t); if (b.dataset.tab === t) b.setAttribute("aria-current","page"); else b.removeAttribute("aria-current"); });
  $$("[data-nav]").forEach(b => b.classList.toggle("active", b.dataset.nav === t));
  $$(".tab").forEach(el => el.classList.toggle("hidden", el.id !== "tab-" + t));
  if (leavingStress) { clearTimeout(stressT); S.removed = new Set(); handle(loadGraph()); }
  if (t === "stress") handle(runStress());
  requestAnimationFrame(() => S.net?.redraw());
}
$$("#tabs button").forEach(b => b.onclick = () => switchTab(b.dataset.tab));
$$("[data-nav]").forEach(b => b.onclick = () => switchTab(b.dataset.nav));
$$("#modes button").forEach(b => b.onclick = () => handle(setMode(b.dataset.mode)));
$$("#radius button").forEach(b => b.onclick = () => { S.radius = +b.dataset.r; $$("#radius button").forEach(x => x.classList.toggle("active",x === b)); if (S.mode === "ego") handle(loadGraph()); });
$$("#btn-fit, #btn-fit-toolbar").forEach(button => {
  button.onclick = () => { graphViewManual = false; scheduleGraphFit(); };
});
$("#btn-zoom-in").onclick = () => zoomGraph(1.25);
$("#btn-zoom-out").onclick = () => zoomGraph(1 / 1.25);
$("#btn-close-detail").onclick = () => { document.body.classList.remove("detail-open"); document.body.classList.add("detail-collapsed"); requestAnimationFrame(() => S.net?.redraw()); };
$("#stress-n").oninput = () => { $("#stress-n-val").textContent = $("#stress-n").value; clearTimeout(stressT); stressT = setTimeout(runStress,250); };
$("#ask-form").onsubmit = e => { e.preventDefault(); if(S.asking) return; const q=$("#ask-input").value; $("#ask-input").value=""; handle(ask(q)); };
$("#ask-input").onkeydown = e => { if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();$("#ask-form").requestSubmit();} };

let searchT, searchRequest = 0;
$("#search").oninput = () => {
  clearTimeout(searchT);
  const q=$("#search").value.trim(), box=$("#search-res"), request=++searchRequest;
  if(q.length<2){box.classList.remove("show");return;}
  searchT=setTimeout(async()=>{try{
    const rows=await api(`/api/search?q=${encodeURIComponent(q)}`);
    if(request!==searchRequest) return;
    box.innerHTML=rows.length ? rows.map(n=>`<button class="item" data-gid="${n.id}" title="${n.id}"><span class="rank">↗</span><span><span class="gid">${short(n.id)}</span><br>${roleBadge(n.role)}</span><span class="p">${n.priority.toFixed(2)}</span></button>`).join("") : '<div class="empty-state">Клиент не найден.<br>Проверьте gid.</div>';
    box.classList.add("show");
  }catch(e){if(request===searchRequest){box.innerHTML='<div class="empty-state">Поиск временно недоступен</div>';box.classList.add("show");}}},200);
};
$("#search").onkeydown = e => {
 if(e.key==="Enter"){e.preventDefault(); const first=$("#search-res [data-gid]"); if(first) first.click(); else if(/^\d{15,20}$/.test(e.target.value.trim())) handle(selectNode(e.target.value.trim(),true));}
 if(e.key==="Escape") $("#search-res").classList.remove("show");
};
document.addEventListener("click",e=>{
 if(!e.target.closest(".search")) $("#search-res").classList.remove("show");
 $$(".header-actions details[open]").forEach(el=>{if(!el.contains(e.target))el.open=false;});
});
document.addEventListener("keydown",e=>{
 if(e.key==="/"&&!e.target.closest("input,textarea")){e.preventDefault();switchTab("node");$("#search").focus();}
 if(e.key==="Escape"){document.body.classList.remove("detail-open");$$(".header-actions details[open]").forEach(el=>el.open=false);}
});
async function health() {
 try {
  const h=await api("/api/health");
  $("#pill-mcp").className="pill "+(h.mcp?"on":"off");
  $("#pill-mcp").lastChild.textContent=h.mcp?`Инструменты онлайн · ${h.mcp_tools.length}`:"Инструменты: локальный режим";
  $("#pill-mcp").title=h.mcp?h.mcp_tools.join(", "):"MCP недоступен, используются прямые вызовы";
  $("#pill-llm").className="pill "+(h.llm?"on":"off");
  $("#pill-llm").lastChild.textContent=h.llm?"AI подключён":"AI: режим правил";
  $(".status-dot").style.background=h.mcp?"#4a9772":"#c69c5e";
 }catch{$("#pill-mcp").lastChild.textContent="Статус временно недоступен";}
}
async function start() {
 $$("[data-icon]").forEach(el=>el.innerHTML=icon(el.dataset.icon));
 initGraph();
 S.summary=await api("/api/summary");
 S.summary.colors=COLORS;
 const s=S.summary;
 const kpi=(name,value,label,note)=>`<div class="kpi"><span class="kpi-icon">${icon(name)}</span><div><div class="kpi-label">${label}</div><b>${value}</b><small>${note}</small></div></div>`;
 $("#kpis").innerHTML=kpi("users",fmtN(s.nodes),"Участников сети",`${s.seeds} исходный клиент · seed`)+
  kpi("money",`${(s.turnover/1e6).toFixed(0)} <span class="unit">млн ₸</span>`,"Общий оборот",`${fmtN(s.edges)} связей между клиентами`)+
  kpi("shield",s.roles.coordinator||0,"Кандидатов в координаторы","Гипотезы для углублённой проверки")+
  kpi("grid",s.clusters,"Кластеров сети","Группы связанных участников");
 const graphRoles = { coordinator: "координатор", consolidator: "консолидатор", distributor: "распределитель", transit: "транзит", terminal: "конечный", peripheral: "периферия" };
 $("#legend").innerHTML=Object.keys(COLORS).map(r=>`<span><i style="background:${COLORS[r]}"></i>${graphRoles[r]}</span>`).join("")+'<span title="Клиенты исходного списка">★ seed</span>';
 const top=await api("/api/top?n=30");
 $("#top-count").textContent=top.length;
 $("#toplist").innerHTML=top.map(n=>`<button class="item" data-gid="${n.id}" title="Клиент ${n.id}"><span class="rank">${String(n.rank).padStart(2,"0")}</span><span><span class="gid">${short(n.id)}</span><br>${roleBadge(n.role)}<span class="bar" style="display:block"><span style="display:block;height:100%;width:${n.priority*100}%;background:#94b99f"></span></span></span><span class="p">${n.priority.toFixed(2)}</span></button>`).join("");
 // Selecting the list opens a card, retaining the current overview.
 $("#toplist").addEventListener("click",e=>{const el=e.target.closest("[data-gid]");if(el){e.stopPropagation();handle(selectNode(el.dataset.gid,false));}});
 renderMethod(); handle(renderClusters()); handle(health()); setInterval(health,30000);
 try{
  const examples=await api("/api/examples");
  const labels=["Общие получатели","Кого проверить первым?","Объяснить роль","Блокировка топ-20","Границы данных"];
  $("#examples").innerHTML=examples.map((q,i)=>`<button class="chip" title="${esc(q)}">${labels[i]||"Пример вопроса"}</button>`).join("");
  $$(".chip").forEach((b,i)=>b.onclick=()=>{if(!S.asking)handle(ask(examples[i]));});
 }catch{$("#examples").textContent="Введите вопрос ниже.";}
 await loadGraph();
 if(top.length) await selectNode(top[0].id,false,false);
}
start().catch(e=>{loader(false);$("#graph-info").textContent="Не удалось загрузить данные. Проверьте запуск сервера.";toast("Ошибка загрузки: "+e.message);});
