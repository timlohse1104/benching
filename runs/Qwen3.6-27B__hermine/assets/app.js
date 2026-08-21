(function () {
  const data = window.RESULTS || { prompts: [], models: [], cells: [], summary: {} };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function fmtMs(ms) {
    if (ms == null) return "-";
    return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(2)}s`;
  }
  function fmtTokens(n) { return n == null ? "-" : String(n); }
  function fmtCost(c) {
    if (c == null) return "-";
    if (c === 0) return "free";
    return `$${c.toFixed(4)}`;
  }
  function fmtTokPerSec(cell) {
    const tokens = cell.completion_tokens;
    const ms = cell.latency_ms;
    if (tokens == null || ms == null || ms <= 0) return "-";
    const tps = tokens / (ms / 1000);
    return `${tps.toFixed(1)} tok/s`;
  }

  // In the aggregate dashboard the same model_id can appear in several runs,
  // so models/cells are keyed by `uid` / `model_uid` (`{run_dir}::{model_id}`).
  // Per-model dashboards have no uid and fall back to the plain id.
  function modelKeyOf(m) { return m.uid || m.id; }
  function cellKey(promptId, modelKey) { return `${promptId}\u0000${modelKey}`; }
  const cellIndex = new Map();
  data.cells.forEach(c => cellIndex.set(cellKey(c.prompt_id, c.model_uid || c.model_id), c));

  // --- Header meta ---
  function renderMeta() {
    const s = data.summary || {};
    const counts = s.status_counts || {};
    const total = s.total || 0;
    const parts = [
      data.aggregate ? `aggregate (${s.run_count} runs)` : `run ${data.timestamp || "?"}`,
      `${s.model_count != null ? s.model_count + ' models · ' : ''}${total} cells`,
      data.dry_run ? "DRY-RUN" : null,
      `cost: ${fmtCost(s.total_cost_usd)}`,
    ].filter(Boolean);
    Object.entries(counts).forEach(([k, v]) => parts.push(`${k}:${v}`));
    $("#run-meta").textContent = parts.join("  ·  ");
  }

  // --- Tabs ---
  $$(".tab").forEach(btn => {
    btn.addEventListener("click", () => {
      $$(".tab").forEach(b => b.classList.toggle("active", b === btn));
      const tab = btn.dataset.tab;
      $$(".tab-panel").forEach(p => p.classList.toggle("active", p.id === `tab-${tab}`));
    });
  });

  document.addEventListener("keydown", e => {
    if (e.target.matches("input, textarea, select")) return;
    if (e.key === "1") $('.tab[data-tab="overview"]').click();
    if (e.key === "2") $('.tab[data-tab="compare"]').click();
    if (e.key === "3") $('.tab[data-tab="errors"]').click();
    if (e.key === "/") { e.preventDefault(); const active = $(".tab.active").dataset.tab;
      const input = $(`#tab-${active} input[type=search]`); if (input) input.focus(); }
    if (e.key === "Escape") closeModal();
  });

  // --- Overview matrix ---
  // Rows = models, Columns = prompts.
  function buildMatrix(filter) {
    const root = $("#overview-matrix");
    const f = (filter || "").trim().toLowerCase();

    const table = document.createElement("table");
    table.className = "matrix";

    // Header row: corner cell + one column per prompt.
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    const corner = document.createElement("th");
    corner.className = "model-head-corner";
    corner.textContent = "model \\ prompt";
    headRow.appendChild(corner);
    data.prompts.forEach(p => {
      const th = document.createElement("th");
      th.className = "prompt-head";
      th.title = p.id;
      th.textContent = p.id;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    // Body rows: one per model.
    const tbody = document.createElement("tbody");
    data.models.forEach(m => {
      const row = document.createElement("tr");
      const th = document.createElement("th");
      th.className = "model-head";
      th.title = m.label + (m.model_name ? " — " + m.model_name : "");
      const link = m.run_dir
        ? `<a class="linklike" href="${m.run_dir}/index.html">open run</a>`
        : "";
      const kindBadge = m.kind
        ? `<span class="badge badge-device" title="device / label">${escapeHtml(m.kind)}</span>`
        : "";
      const runTs = data.aggregate && m.run_timestamp
        ? `<span class="run-ts" title="run timestamp">${escapeHtml(m.run_timestamp)}</span>`
        : "";
      th.innerHTML = `
        <span class="model-label">${escapeHtml(m.label)}</span>
        <span class="model-sub">
          <span class="badge">${m.is_local ? "local" : "cloud"}</span>
          ${kindBadge}
          ${link}
        </span>
        ${runTs}`;
      row.appendChild(th);

      let anyVisible = false;
      data.prompts.forEach(p => {
        const td = document.createElement("td");
        const cell = cellIndex.get(cellKey(p.id, modelKeyOf(m)));
        if (!cell) { td.textContent = "-"; row.appendChild(td); return; }
        const blob = [
          p.id, m.id, m.label, m.kind || "", m.run_timestamp || "", cell.status, cell.error || "",
        ].join(" ").toLowerCase();
        if (f && !blob.includes(f)) { td.style.opacity = "0.25"; }
        else { anyVisible = true; }

        td.className = "cell";
        const badge = `<span class="badge badge-${cell.status}">${cell.status}</span>`;
        const thumb = cell.thumbnail
          ? `<img class="thumb" loading="lazy" src="${cell.thumbnail}" alt="">`
          : `<div class="thumb"></div>`;
        const meta = [
          fmtMs(cell.latency_ms),
          fmtTokens(cell.completion_tokens) + " tok",
          fmtTokPerSec(cell),
        ];
        if (cell.cost_usd != null && cell.cost_usd > 0) {
          meta.push(fmtCost(cell.cost_usd));
        }
        td.innerHTML = `
          ${thumb}
          <div class="meta-row">${badge}<span>${meta.join(" | ")}</span></div>
          ${cell.error ? `<div class="meta-row" style="color:var(--bad)">${escapeHtml(cell.error)}</div>` : ""}
        `;
        td.addEventListener("click", () => {
          if (cell.html_file) openModal(`${p.id} · ${m.label}`, cell.html_file);
        });
        row.appendChild(td);
      });
      if (!f || anyVisible || m.label.toLowerCase().includes(f) || m.id.toLowerCase().includes(f)) {
        tbody.appendChild(row);
      }
    });
    table.appendChild(tbody);

    root.replaceChildren(table);
  }

  $("#overview-filter").addEventListener("input", e => buildMatrix(e.target.value));

  // --- Compare ---
  function modelOptionText(m) {
    const parts = [m.label];
    if (m.kind) parts.push(m.kind);
    if (data.aggregate && m.run_timestamp) parts.push(m.run_timestamp);
    return parts.join(" · ");
  }

  function fillCompareSelects() {
    const promptSel = $("#compare-prompt");
    const aSel = $("#compare-a");
    const bSel = $("#compare-b");
    promptSel.innerHTML = data.prompts.map(p => `<option value="${p.id}">${p.id}</option>`).join("");
    const modelOpts = data.models
      .map(m => `<option value="${escapeHtml(modelKeyOf(m))}">${escapeHtml(modelOptionText(m))}</option>`)
      .join("");
    aSel.innerHTML = modelOpts;
    bSel.innerHTML = modelOpts;
    if (data.models.length >= 2) bSel.selectedIndex = 1;
  }

  function renderCompare() {
    const promptId = $("#compare-prompt").value;
    const sides = [
      { side: "a", modelKey: $("#compare-a").value },
      { side: "b", modelKey: $("#compare-b").value },
    ];
    sides.forEach(({ side, modelKey }) => {
      const pane = $(`.compare-pane[data-side="${side}"]`);
      const cell = cellIndex.get(cellKey(promptId, modelKey));
      const head = $(".compare-head", pane);
      const frame = $("iframe", pane);
      const body = $(".details-body", pane);
      if (!cell || !cell.html_file) {
        head.textContent = "no result";
        frame.src = "about:blank";
        body.textContent = "";
        return;
      }
      const m = data.models.find(x => modelKeyOf(x) === modelKey);
      const headerMeta = [
        fmtMs(cell.latency_ms),
        fmtTokens(cell.completion_tokens) + " tok",
        fmtTokPerSec(cell),
      ];
      if (cell.cost_usd != null && cell.cost_usd > 0) {
        headerMeta.push(fmtCost(cell.cost_usd));
      }
      const kindBadge = m && m.kind
        ? `<span class="badge badge-device" title="device / label">${escapeHtml(m.kind)}</span>`
        : "";
      const runTs = data.aggregate && m && m.run_timestamp
        ? ` · <span title="run timestamp">${escapeHtml(m.run_timestamp)}</span>`
        : "";
      head.innerHTML = `<strong>${escapeHtml(m ? m.label : modelKey)}</strong>
        <span class="badge badge-${cell.status}">${cell.status}</span>
        ${kindBadge}${runTs}
        · ${headerMeta.join(" | ")}
        · <a class="linklike" href="${cell.html_file}" target="_blank">open raw</a>`;
      frame.src = cell.html_file;

      const v = cell.validation || {};
      const flags = [
        `doctype: ${v.has_doctype ? "yes" : "no"}`,
        `html/head/body: ${[v.has_html, v.has_head, v.has_body].map(x => x ? "Y" : "N").join("/")}`,
      ];
      const parse = (v.parse_issues || []).map(i => `parse · ${i.kind}: ${i.message}`);
      const runtime = (v.runtime_issues || []).map(i => `runtime · ${i.kind}: ${i.message}`);
      body.innerHTML = [...flags, ...parse, ...runtime].map(escapeHtml).map(s => `<div>${s}</div>`).join("");
    });
  }

  $("#compare-prompt").addEventListener("change", renderCompare);
  $("#compare-a").addEventListener("change", renderCompare);
  $("#compare-b").addEventListener("change", renderCompare);
  $("#compare-swap").addEventListener("click", () => {
    const a = $("#compare-a"), b = $("#compare-b");
    [a.value, b.value] = [b.value, a.value];
    renderCompare();
  });

  // --- Compare scroll lock ---
  // The output iframes are sandboxed without `allow-same-origin`, so their
  // scroll position cannot be touched from here. Instead, when the lock is
  // engaged, each iframe is stretched to a tall fixed canvas (CSS class on
  // .compare-grid) and the surrounding .frame-scroll wrappers do the actual
  // scrolling - those live in this document and can be kept in sync.
  (function initScrollLock() {
    const btn = $("#compare-scroll-lock");
    const grid = $(".compare-grid");
    const wrappers = $$(".frame-scroll", grid);
    let locked = false;
    let syncing = false;

    function setLocked(on) {
      locked = on;
      grid.classList.toggle("scroll-locked", on);
      btn.textContent = on ? "Scroll lock: on" : "Scroll lock: off";
      btn.setAttribute("aria-pressed", String(on));
      if (on && wrappers.length === 2) {
        // Align pane B to pane A when engaging the lock.
        wrappers[1].scrollTop = wrappers[0].scrollTop;
        wrappers[1].scrollLeft = wrappers[0].scrollLeft;
      }
    }

    btn.addEventListener("click", () => setLocked(!locked));

    wrappers.forEach(el => {
      el.addEventListener("scroll", () => {
        if (!locked || syncing) return;
        const other = wrappers.find(x => x !== el);
        if (!other) return;
        syncing = true;
        other.scrollTop = el.scrollTop;
        other.scrollLeft = el.scrollLeft;
        requestAnimationFrame(() => { syncing = false; });
      });
    });
  })();

  // --- Errors ---
  function renderErrors(filter) {
    const root = $("#errors-list");
    const f = (filter || "").trim().toLowerCase();
    const groups = new Map();

    function add(kind, payload) {
      if (!groups.has(kind)) groups.set(kind, []);
      groups.get(kind).push(payload);
    }

    data.cells.forEach(cell => {
      if (cell.status === "ok") return;
      const v = cell.validation || {};
      (v.parse_issues || []).forEach(i => add(`parse · ${i.kind}`, { cell, issue: i }));
      (v.runtime_issues || []).forEach(i => add(`runtime · ${i.kind}`, { cell, issue: i }));
      if (cell.error) add("request · failed", { cell, issue: { kind: "failed", message: cell.error }});
    });

    const sections = [];
    Array.from(groups.entries()).sort((a, b) => a[0].localeCompare(b[0])).forEach(([kind, items]) => {
      const filtered = items.filter(({ cell, issue }) => {
        if (!f) return true;
        const blob = [
          kind, cell.prompt_id, cell.model_id, cell.model_label,
          issue.message, cell.error || "",
        ].join(" ").toLowerCase();
        return blob.includes(f);
      });
      if (filtered.length === 0) return;
      const group = document.createElement("div");
      group.className = "error-group";
      const h = document.createElement("h3");
      h.textContent = `${kind}  (${filtered.length})`;
      group.appendChild(h);
      filtered.forEach(({ cell, issue }) => {
        const row = document.createElement("div");
        row.className = "error-row";
        row.innerHTML = `
          <div>${escapeHtml(cell.prompt_id)}</div>
          <div>${escapeHtml(cell.model_label || cell.model_id)}</div>
          <div class="msg">${escapeHtml(issue.message || "")}</div>
          <div>${cell.html_file ? `<a class="linklike" data-open="${cell.html_file}" data-title="${escapeHtml(cell.prompt_id + " · " + (cell.model_label || cell.model_id))}">open</a>` : ""}</div>
        `;
        group.appendChild(row);
      });
      sections.push(group);
    });

    root.replaceChildren(...sections);
    if (sections.length === 0) {
      const empty = document.createElement("div");
      empty.style.color = "var(--fg-dim)";
      empty.textContent = "No issues found.";
      root.appendChild(empty);
    }
  }

  $("#errors-filter").addEventListener("input", e => renderErrors(e.target.value));
  $("#errors-list").addEventListener("click", e => {
    const a = e.target.closest("[data-open]");
    if (!a) return;
    openModal(a.dataset.title, a.dataset.open);
  });

  // --- Modal ---
  function openModal(title, url) {
    $("#modal-title").textContent = title;
    $("#modal-frame").src = url;
    $("#modal").hidden = false;
  }
  function closeModal() {
    $("#modal").hidden = true;
    $("#modal-frame").src = "about:blank";
  }
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });

  // --- Util ---
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // --- Boot ---
  renderMeta();
  buildMatrix("");
  fillCompareSelects();
  renderCompare();
  renderErrors("");
})();
