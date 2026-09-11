// Click-to-sort for server-rendered tables.
//
// Mark a table with data-sortable="<storage key>" and each sortable header
// with data-sort="text" or data-sort="date". A cell's data-sort-value, when
// present, is sorted on instead of its text (e.g. an ISO date). Empty values
// always sort last. The chosen column is remembered per table.
(function () {
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

  function cellValue(row, index) {
    const cell = row.children[index];
    if (!cell) return "";
    const v = cell.dataset.sortValue;
    return (v !== undefined ? v : cell.textContent).trim();
  }

  function sortBy(table, index, dir) {
    const tbody = table.tBodies[0];
    const rows = Array.from(tbody.rows).filter((r) => !r.querySelector("td[colspan]"));
    rows.sort((a, b) => {
      const va = cellValue(a, index);
      const vb = cellValue(b, index);
      if (!va && !vb) return 0;
      if (!va) return 1;
      if (!vb) return -1;
      return dir * collator.compare(va, vb);
    });
    rows.forEach((r) => tbody.appendChild(r));
    table.querySelectorAll("th[data-sort]").forEach((th) => {
      const active = th.cellIndex === index;
      th.setAttribute("aria-sort", active ? (dir > 0 ? "ascending" : "descending") : "none");
      th.querySelector(".sort-arrow").textContent = active ? (dir > 0 ? "▲" : "▼") : "↕";
    });
  }

  function remember(key, value) {
    try {
      if (value) localStorage.setItem(key, JSON.stringify(value));
    } catch (e) { /* storage unavailable */ }
  }

  function recall(key) {
    try {
      return JSON.parse(localStorage.getItem(key) || "null");
    } catch (e) {
      return null;
    }
  }

  function init(table) {
    if (table.dataset.sortReady) return;
    table.dataset.sortReady = "1";
    const key = "sort:" + table.dataset.sortable;
    table.querySelectorAll("th[data-sort]").forEach((th) => {
      th.classList.add("cursor-pointer", "select-none", "hover:text-slate-900");
      th.setAttribute("aria-sort", "none");
      th.title = th.title || "Click to sort";
      const arrow = document.createElement("span");
      arrow.className = "sort-arrow ml-1 text-slate-400";
      arrow.textContent = "↕";
      th.appendChild(arrow);
      th.addEventListener("click", () => {
        const current = th.getAttribute("aria-sort");
        // Dates start newest-first; everything else A→Z.
        const first = th.dataset.sort === "date" ? -1 : 1;
        const dir = current === "none" ? first : current === "ascending" ? -1 : 1;
        sortBy(table, th.cellIndex, dir);
        remember(key, { index: th.cellIndex, dir: dir });
      });
    });
    const saved = recall(key);
    if (saved) {
      const th = table.tHead.rows[0].cells[saved.index];
      if (th && th.dataset.sort) sortBy(table, saved.index, saved.dir);
    }
  }

  function initAll(root) {
    (root || document).querySelectorAll("table[data-sortable]").forEach(init);
  }

  document.addEventListener("DOMContentLoaded", () => initAll());
  document.addEventListener("htmx:afterSwap", (e) => initAll(e.target));
})();
