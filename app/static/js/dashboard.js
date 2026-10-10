// Dashboard charts (step 6). All numbers are computed on the server; this script only draws.
(() => {
  "use strict";

  const data = JSON.parse(document.getElementById("chart-data").textContent);
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();

  // Categorical slots in fixed order: total first, then the lines (colour follows the entity).
  const SERIES = {
    total: color("--series-1"),
    L1: color("--series-2"),
    L2: color("--series-3"),
    L3: color("--series-4"),
  };
  const pct = (v) => (v === null || v === undefined ? "–" : `${(v * 100).toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`);
  const int = (v) => v.toLocaleString("de-DE");

  Chart.defaults.font.family = '"Segoe UI", system-ui, sans-serif';
  Chart.defaults.color = color("--muted");
  Chart.defaults.borderColor = color("--grid");

  // --- period: date fields only matter for "Frei wählbar" ---------------------------
  const period = document.getElementById("period");
  const syncRange = () => {
    const custom = period.value === "custom";
    document.querySelectorAll(".custom-range input").forEach((input) => (input.disabled = !custom));
    document.querySelectorAll(".custom-range").forEach((label) => label.classList.toggle("inactive", !custom));
  };
  period.addEventListener("change", syncRange);
  syncRange();

  // --- defect rate over time ---------------------------------------------------------
  // Provisional values (open inspections counted with the AI verdict): dashed segment
  // leading to the point and a hollow, larger point.
  const surface = color("--surface");
  const lineSeries = (label, series, key, emphasis) => {
    const radius = data.days.length > 12 ? 2 : 4;
    return {
      label,
      data: series.rate,
      provisional: series.provisional,
      borderColor: SERIES[key],
      backgroundColor: SERIES[key],
      borderWidth: emphasis ? 2.5 : 2,
      pointRadius: series.provisional.map((p) => (p ? 5 : radius)),
      pointBackgroundColor: series.provisional.map((p) => (p ? surface : SERIES[key])),
      pointBorderWidth: series.provisional.map((p) => (p ? 2 : 1)),
      pointHoverRadius: 6,
      segment: { borderDash: (ctx) => (series.provisional[ctx.p1DataIndex] ? [6, 4] : undefined) },
      spanGaps: true,
      tension: 0,
    };
  };
  const datasets = [lineSeries("Gesamt", data.total, "total", true)];
  for (const [code, series] of Object.entries(data.lines)) {
    datasets.push(lineSeries(`${code} – ${data.line_names[code]}`, series, code, false));
  }
  new Chart(document.getElementById("trend-chart"), {
    type: "line",
    data: { labels: data.days, datasets },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: datasets.length > 1, position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } },
        tooltip: {
          callbacks: {
            label: (ctx) =>
              ` ${ctx.dataset.label}: ${pct(ctx.parsed.y)}${ctx.dataset.provisional[ctx.dataIndex] ? " (vorläufig)" : ""}`,
          },
        },
      },
      scales: {
        y: { beginAtZero: true, ticks: { callback: (v) => pct(v) }, grid: { color: color("--grid") } },
        x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
      },
    },
  });

  // --- defect types after corrections; reflections separate as "filtered" -------------
  const labels = [
    ...data.defects.map((d) => d.label),
    ...data.filtered.map((d) => `${d.label} (gefiltert, kein Fehler)`),
  ];
  const counts = [...data.defects.map((d) => d.count), ...data.filtered.map((d) => d.count)];
  const colors = [...data.defects.map(() => SERIES.total), ...data.filtered.map(() => color("--filtered"))];
  new Chart(document.getElementById("types-chart"), {
    type: "bar",
    data: { labels, datasets: [{ label: "Teile", data: counts, backgroundColor: colors, borderRadius: 4, borderSkipped: "start", barPercentage: 0.7 }] },
    options: {
      indexAxis: "y",
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => ` ${int(ctx.parsed.x)} Teile` } },
      },
      scales: {
        x: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: color("--grid") } },
        y: { grid: { display: false }, ticks: { color: color("--text") } },
      },
    },
  });
})();
