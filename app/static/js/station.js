// Inspection station: infeed, queue, detail view and review (step 4).
// All rules are evaluated on the server; the browser only displays and sends decisions.
(() => {
  "use strict";

  const cfg = JSON.parse(document.getElementById("station-config").textContent);
  const $ = (id) => document.getElementById(id);
  const SVG_NS = "http://www.w3.org/2000/svg";

  let selectedId = null;
  let current = null;      // detail of the selected inspection
  let infeedTimer = null;
  let busy = false;
  let showMarks = true;

  // --- helpers -------------------------------------------------------------------

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child !== null && child !== undefined) node.append(child);
    }
    return node;
  }

  function svg(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  }

  const num = (value, digits) =>
    value.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });

  async function api(method, url, body) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const res = await fetch(url, options);
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      const detail = data && typeof data.detail === "string" ? data.detail : `Fehler ${res.status}: ungültige Anfrage.`;
      throw new Error(detail);
    }
    return data;
  }

  function showError(message) {
    const hint = $("error-hint");
    hint.textContent = message || "";
    hint.hidden = !message;
  }

  function verdictBadge(verdict, extraClass = "") {
    return el("span", { class: `badge verdict-${verdict} ${extraClass}`, text: cfg.verdicts[verdict] });
  }

  // --- state: counters and queue ----------------------------------------------------

  async function refresh() {
    const state = await api("GET", "/api/station/state");
    renderCounters(state.counts);
    renderQueue(state.queue);
    $("done-hint").hidden = !state.all_done;
    if (state.all_done) stopInfeed();

    if (selectedId === null && state.queue.length) await select(state.queue[0].id);
    return state;
  }

  function renderCounters(c) {
    const items = [
      ["Heute geprüft", c.today, "today"],
      ["Automatisch freigegeben", c.today_auto_ok, "auto"],
      ["Zu prüfen", c.open, "open"],
    ];
    $("counters").replaceChildren(
      ...items.map(([label, value, key]) =>
        el("div", { class: `counter counter-${key}` }, el("dt", { text: label }), el("dd", { text: value.toLocaleString("de-DE") }))
      )
    );
  }

  function renderQueue(queue) {
    $("queue-count").textContent = queue.length;
    $("queue-empty").hidden = queue.length > 0;
    $("queue").replaceChildren(
      ...queue.map((item) =>
        el(
          "li",
          {},
          el(
            "button",
            {
              type: "button",
              class: `queue-item${item.id === selectedId ? " selected" : ""}`,
              "data-id": item.id,
              onclick: () => select(item.id),
            },
            el("span", { class: "queue-serial", text: item.serial }),
            verdictBadge(item.ai_verdict, "small"),
            el("span", { class: "queue-reason", text: item.ai_reason })
          )
        )
      )
    );
  }

  // --- detail -----------------------------------------------------------------------

  async function select(id) {
    selectedId = id;
    try {
      current = await api("GET", `/api/station/inspections/${id}`);
      renderDetail(current);
      document.querySelectorAll("[data-id]").forEach((node) => {
        node.classList.toggle("selected", Number(node.dataset.id) === id);
      });
      showError("");
    } catch (err) {
      showError(err.message);
    }
  }

  function renderDetail(d) {
    const header = el(
      "div",
      { class: "detail-head" },
      el("h2", { text: d.serial }),
      el("span", { class: "muted", text: `${d.line_name} · Charge ${d.batch}` })
    );

    const figure = el("div", { class: "figure" });
    const img = el("img", { src: `/api/station/inspections/${d.id}/image`, alt: `Aufnahme von Teil ${d.serial}` });
    figure.append(img, buildOverlay(d.detections));
    const toggle = el(
      "label",
      { class: "switch" },
      el("input", {
        type: "checkbox",
        checked: showMarks,
        onchange: (e) => {
          showMarks = e.target.checked;
          figure.classList.toggle("hide-marks", !showMarks);
        },
      }),
      el("span", { text: "Markierungen anzeigen" })
    );
    figure.classList.toggle("hide-marks", !showMarks);

    const result = el(
      "div",
      { class: "result" },
      el("div", { class: `verdict-big verdict-${d.ai_verdict}` }, el("small", { text: "KI-Ergebnis" }), cfg.verdicts[d.ai_verdict]),
      el("p", { class: "reason", text: d.ai_reason }),
      buildDetectionList(d.detections),
      buildActions(d)
    );

    $("detail").replaceChildren(
      header,
      el("div", { class: "detail-grid" }, el("div", {}, figure, toggle), result)
    );
  }

  function buildOverlay(detections) {
    const overlay = svg("svg", {
      class: "overlay",
      viewBox: `0 0 ${cfg.image_width} ${cfg.image_height}`,
      preserveAspectRatio: "none",
      "aria-hidden": "true",
    });
    detections.forEach((det, index) => {
      const group = svg("g", { class: `mark sev-${det.severity}${det.certain ? "" : " uncertain"}` });
      group.append(svg("rect", { x: det.x, y: det.y, width: det.w, height: det.h, rx: 3 }));
      const label = svg("text", { x: det.x, y: Math.max(det.y - 6, 14) });
      label.textContent = det.is_defect ? `${index + 1} ${det.type_name}` : `${index + 1} ${det.type_name} – kein Fehler`;
      group.append(label);
      overlay.append(group);
    });
    return overlay;
  }

  function buildDetectionList(detections) {
    if (!detections.length) return el("p", { class: "muted", text: "Keine Erkennungen." });
    const limitPct = cfg.confidence_min * 100;
    const rows = detections.map((det, index) =>
      el(
        "tr",
        { class: det.certain ? "" : "row-uncertain" },
        el("td", { text: String(index + 1) }),
        el("td", {}, el("span", { class: `sev-dot sev-${det.severity}` }), det.type_name),
        el("td", { text: det.is_defect ? det.severity_name : "kein Fehler" }),
        el("td", { class: "num", text: det.length_mm === null ? "–" : `${num(det.length_mm, 1)} mm` }),
        el(
          "td",
          {},
          el(
            "div",
            { class: "conf", title: `Grenze ${num(cfg.confidence_min, 2)}` },
            el("div", { class: `conf-fill${det.certain ? "" : " low"}`, style: `width:${det.confidence * 100}%` }),
            el("div", { class: "conf-limit", style: `left:${limitPct}%` })
          ),
          el("span", { class: "conf-value", text: num(det.confidence, 2) })
        )
      )
    );
    return el(
      "table",
      { class: "detections" },
      el(
        "thead",
        {},
        el("tr", {}, el("th", { text: "#" }), el("th", { text: "Typ" }), el("th", { text: "Schweregrad" }), el("th", { text: "Länge" }), el("th", { text: `Konfidenz (Grenze ${num(cfg.confidence_min, 2)})` }))
      ),
      el("tbody", {}, rows)
    );
  }

  function buildActions(d) {
    if (d.review) {
      const r = d.review;
      const text = r.decision === "CONFIRMED" ? "Bestätigt" : "Korrigiert";
      return el(
        "div",
        { class: "review-done" },
        el("p", {}, el("strong", { text: `${text} · Endurteil ` }), verdictBadge(r.final_verdict)),
        r.comment ? el("p", { class: "muted", text: `Kommentar: ${r.comment}` }) : null
      );
    }
    if (d.status === "AUTO_DONE") return el("p", { class: "muted", text: "Automatisch als OK eingestuft." });

    const correct = el("button", { type: "button", class: "btn", onclick: openCorrection }, "Korrigieren ", el("kbd", { text: "K" }));
    if (d.ai_verdict === "NOK") {
      return el(
        "div",
        { class: "actions" },
        el("button", { type: "button", class: "btn btn-primary", onclick: () => confirm("NOK") }, "Bestätigen (NOK) ", el("kbd", { text: "Enter" })),
        correct
      );
    }
    return el(
      "div",
      { class: "actions" },
      el("p", { class: "hint", text: "Die KI ist unsicher. Bitte entscheiden Sie:" }),
      el("button", { type: "button", class: "btn btn-ok", onclick: () => confirm("OK") }, "Freigeben (OK) ", el("kbd", { text: "O" })),
      el("button", { type: "button", class: "btn btn-nok", onclick: () => confirm("NOK") }, "Aussortieren (NOK) ", el("kbd", { text: "N" })),
      correct
    );
  }

  // --- review -----------------------------------------------------------------------

  async function submitReview(body) {
    const id = current.id;
    await api("POST", `/api/station/inspections/${id}/review`, body);
    selectedId = null;
    current = null;
    $("detail").replaceChildren(el("p", { class: "muted", text: "Gespeichert. Kein weiteres Teil offen." }));
    await refresh();
  }

  async function confirm(finalVerdict) {
    if (!current || current.review || current.status !== "PENDING_REVIEW") return;
    try {
      await submitReview({ decision: "CONFIRMED", final_verdict: finalVerdict, changes: [] });
      showError("");
    } catch (err) {
      showError(err.message);
    }
  }

  function typeOptions(selected, includeReflection) {
    return Object.entries(cfg.defect_types)
      .filter(([code]) => includeReflection || !cfg.non_defect_types.includes(code))
      .map(([code, name]) => el("option", { value: code, selected: code === selected, text: name }));
  }

  function openCorrection() {
    if (!current || current.review || current.status !== "PENDING_REVIEW") return;
    $("correct-serial").textContent = current.serial;
    $("correct-error").hidden = true;
    $("correct-comment").value = "";
    $("correct-comment").maxLength = cfg.comment_max_length;
    document.querySelectorAll('input[name="final_verdict"]').forEach((radio) => {
      radio.checked = false; // Thomas decides explicitly
    });

    const rows = current.detections.map((det, index) => {
      const typeSelect = el("select", { class: "new-type", disabled: true, "aria-label": "Richtiger Typ" }, typeOptions(null, true));
      const choice = el(
        "select",
        {
          class: "choice",
          "data-detection": det.id,
          "aria-label": `Einschätzung zu Erkennung ${index + 1}`,
          onchange: (e) => {
            typeSelect.disabled = e.target.value !== "TYPE_CHANGED";
          },
        },
        el("option", { value: "", text: "stimmt" }),
        el("option", { value: "REJECTED", text: "falsch erkannt" }),
        el("option", { value: "TYPE_CHANGED", text: "anderer Typ" })
      );
      const known = det.length_mm === null ? "" : ` (${num(det.length_mm, 1)} mm)`;
      return el("tr", {}, el("td", { text: `${index + 1} ${det.type_name}${known}` }), el("td", {}, choice), el("td", {}, typeSelect));
    });
    $("correct-detections").replaceChildren(...rows);
    $("correct-no-detections").hidden = rows.length > 0;
    $("missed-list").replaceChildren();
    $("correct-dialog").showModal();
  }

  function addMissedRow() {
    const row = el(
      "div",
      { class: "missed-row" },
      el("select", { class: "missed-type", "aria-label": "Typ des übersehenen Fehlers" }, typeOptions(null, false)),
      el("button", { type: "button", class: "btn btn-small", onclick: () => row.remove() }, "Entfernen")
    );
    $("missed-list").append(row);
  }

  async function saveCorrection(event) {
    event.preventDefault();
    const finalRadio = document.querySelector('input[name="final_verdict"]:checked');
    const changes = [];
    document.querySelectorAll("#correct-detections tr").forEach((row) => {
      const choice = row.querySelector(".choice");
      if (!choice.value) return;
      const change = { change: choice.value, detection_id: Number(choice.dataset.detection) };
      if (choice.value === "TYPE_CHANGED") change.new_defect_type = row.querySelector(".new-type").value;
      changes.push(change);
    });
    document.querySelectorAll("#missed-list .missed-type").forEach((select) => {
      changes.push({ change: "MISSED", new_defect_type: select.value });
    });

    const errorBox = $("correct-error");
    if (!finalRadio) {
      errorBox.textContent = "Bitte ein Endurteil wählen.";
      errorBox.hidden = false;
      return;
    }
    try {
      await submitReview({
        decision: "CORRECTED",
        final_verdict: finalRadio.value,
        comment: $("correct-comment").value,
        changes,
      });
      $("correct-dialog").close();
      showError("");
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
    }
  }

  // --- infeed -----------------------------------------------------------------------

  // Runs by itself like a conveyor: starts with the page, stops when all demo parts are in.
  async function next() {
    if (busy) return;
    busy = true;
    try {
      const result = await api("POST", "/api/station/next");
      showError("");
      await refresh();
      if (result.done) stopInfeed();
    } catch (err) {
      showError(err.message); // keep running; a temporary error must not stop the line
    } finally {
      busy = false;
    }
  }

  function startInfeed() {
    if (infeedTimer === null) infeedTimer = window.setInterval(next, cfg.interval_ms);
  }

  function stopInfeed() {
    if (infeedTimer !== null) window.clearInterval(infeedTimer);
    infeedTimer = null;
    $("infeed-status").classList.add("stopped");
    $("infeed-text").textContent = "Einlauf beendet";
  }

  // --- wiring -----------------------------------------------------------------------

  $("btn-add-missed").addEventListener("click", addMissedRow);
  $("btn-cancel").addEventListener("click", () => $("correct-dialog").close());
  $("correct-form").addEventListener("submit", saveCorrection);

  document.addEventListener("keydown", (e) => {
    if ($("correct-dialog").open || e.target.closest("input, textarea, select, button")) return;
    if (!current || current.review || current.status !== "PENDING_REVIEW") return;
    const key = e.key.toLowerCase();
    if (key === "enter" && current.ai_verdict === "NOK") confirm("NOK");
    else if (key === "k") openCorrection();
    else if (key === "o" && current.ai_verdict === "MANUAL") confirm("OK");
    else if (key === "n" && current.ai_verdict === "MANUAL") confirm("NOK");
    else return;
    e.preventDefault();
  });

  refresh()
    .then((state) => {
      if (!state.all_done) startInfeed();
    })
    .catch((err) => {
      showError(err.message);
      startInfeed();
    });
})();
