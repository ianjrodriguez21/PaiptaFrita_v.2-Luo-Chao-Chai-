const collator = new Intl.Collator("es", { numeric: true, sensitivity: "base" });
const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;

function compareValues(left, right, direction) {
  if (!left || !right) return Number(!left) - Number(!right);
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  const numeric = decimal.test(left) && decimal.test(right) &&
    Number.isFinite(leftNumber) && Number.isFinite(rightNumber);
  return direction * (numeric ? leftNumber - rightNumber : collator.compare(left, right));
}

function normalizeSearch(value) {
  return value.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase("es").trim();
}

for (const table of document.querySelectorAll(".table-scroll table")) {
  const headings = Array.from(table.tHead.rows[0].cells);
  const body = table.tBodies[0];
  const controls = table.closest(".table-scroll").previousElementSibling;
  const search = controls.querySelector(".table-search");
  const results = controls.querySelector(".table-results");
  search.addEventListener("input", () => {
    const terms = normalizeSearch(search.value).split(/\s+/);
    let visible = 0;
    for (const row of body.rows) {
      const text = normalizeSearch(Array.from(row.cells, cell => cell.textContent).join(" "));
      row.hidden = !terms.every(term => text.includes(term));
      if (!row.hidden) visible++;
    }
    results.textContent = search.value.trim()
      ? (visible ? `${visible} de ${body.rows.length} resultados` : "Sin coincidencias")
      : "";
  });
  headings.forEach((heading, index) => {
    const button = heading.querySelector(".sort-button");
    button.addEventListener("click", () => {
      const direction = heading.getAttribute("aria-sort") === "ascending" ? -1 : 1;
      for (const other of headings) {
        other.removeAttribute("aria-sort");
        other.querySelector("span").textContent = "↕";
      }
      heading.setAttribute("aria-sort", direction === 1 ? "ascending" : "descending");
      button.querySelector("span").textContent = direction === 1 ? "↑" : "↓";
      const rows = Array.from(body.rows);
      rows.sort((left, right) =>
        compareValues(left.cells[index].dataset.sort, right.cells[index].dataset.sort, direction)
      );
      body.append(...rows);
    });
  });
}
