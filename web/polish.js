/* polish.js — улучшения UX поверх app.js. Всё в try/catch: при любой ошибке сайт работает как без этого файла. */
(function () {
  const safe = (fn) => { try { fn(); } catch (e) { console.warn("polish:", e); } };

  // 1) читаемые подписи на графе: крупнее шрифт, подпись только у заметных узлов
  function tuneGraph() {
    if (typeof S === "undefined" || !S.net || !S.nodes) return false;
    S.net.setOptions({
      nodes: { font: { size: 15, color: "#e8edf5", face: "Inter, Segoe UI, system-ui", strokeWidth: 4, strokeColor: "#0a0e17" } },
      edges: { color: { color: "#64748b", opacity: 0.45, highlight: "#f5b400", hover: "#cbd5e1" }, hoverWidth: 1.5 },
      interaction: { hover: true, tooltipDelay: 80, zoomSpeed: 0.7 },
    });
    // подписи оставляем крупным/выделенным узлам — остальные показываются при наведении
    const relabel = () => safe(() => {
      // подписи: топ-15 узлов по размеру (= приоритету) + выделенные; остальные — во всплывающей подсказке
      const all = S.nodes.get();
      const keep = new Set(all.slice().sort((a, b) => (b.size || 0) - (a.size || 0)).slice(0, 15).map((n) => n.id));
      all.forEach((n) => { if ((n.borderWidth || 0) >= 4) keep.add(n.id); });
      const upd = [];
      all.forEach((n) => {
        const want = keep.has(n.id) ? "…" + String(n.id).slice(-6) : "";
        if (n.label !== want) upd.push({ id: n.id, label: want });
      });
      if (upd.length) S.nodes.update(upd);
    });
    S.nodes.on("add", () => setTimeout(relabel, 0));
    relabel();
    return true;
  }

  // 2) короткие gid в списках (полный номер — во всплывающей подсказке)
  function shortenGids(root) {
    (root || document).querySelectorAll(".item .gid, .peer .g").forEach((el) => {
      const full = el.dataset.full || el.textContent.trim();
      if (!/^\d{12,}$/.test(full.replace("…", ""))) return;
      if (el.dataset.full) return;
      el.dataset.full = full;
      el.title = full + " — клик, чтобы открыть";
      el.textContent = "…" + full.slice(-6);
    });
  }

  // 3) подсказка «как пользоваться» поверх графа
  function helpCard() {
    const c = document.querySelector(".center");
    if (!c || c.querySelector(".g-help")) return;
    let hidden = false;
    try { hidden = localStorage.getItem("mg_help_hidden") === "1"; } catch { }
    if (hidden) return;
    const d = document.createElement("div");
    d.className = "g-help";
    d.innerHTML = `<span class="x" title="Скрыть">✕</span><b>Как пользоваться</b><br>
      • <b>клик</b> по узлу — карточка справа<br>
      • <b>двойной клик</b> — окружение узла<br>
      • <b>▶ Путь денег</b> — как деньги seed доходят до узла<br>
      • колёсико — масштаб, перетаскивание — сдвиг<br>
      <span class="muted">★ seed · размер = приоритет · толщина стрелки = сумма</span>`;
    d.querySelector(".x").onclick = () => { d.remove(); try { localStorage.setItem("mg_help_hidden", "1"); } catch { } };
    c.appendChild(d);
  }

  // 4) Enter в поиске открывает первый результат; Esc закрывает выпадашку
  function searchKeys() {
    const inp = document.getElementById("search");
    if (!inp || inp.dataset.polished) return;
    inp.dataset.polished = "1";
    inp.addEventListener("keydown", (e) => {
      const box = document.getElementById("search-res");
      if (e.key === "Enter") {
        const first = box && box.querySelector("[data-gid]");
        const q = inp.value.trim();
        if (first) first.click();
        else if (/^\d{15,20}$/.test(q) && window.selectNode) selectNode(q, true);
      } else if (e.key === "Escape" && box) box.classList.remove("show");
    });
  }

  function boot() {
    safe(helpCard); safe(searchKeys); safe(() => shortenGids());
    new MutationObserver(() => safe(() => shortenGids())).observe(document.body, { childList: true, subtree: true });
    let tries = 0;
    const t = setInterval(() => { if (tuneGraph() || ++tries > 40) clearInterval(t); }, 250);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
