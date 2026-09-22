/* ── Config ─────────────────────────────────────────────────────────────── */
const _qs = new URLSearchParams(window.location.search);
/* Parent React app can pass ?apiBase=... (supports VITE_API_URL in prod). */
const API_BASE = _qs.get("apiBase") || "/api/observability/delicato";
const PROJECT_ID = _qs.get("project") || "delicato";
const PAGE_TITLE = _qs.get("title") || "Delicato Invoice Processing";

/* Carrier master lists (empty / missing → hide carrier filter) */
const DELICATO_CARRIERS = [
  "Hillebrand Gori USA LLC",
  "Kuehne & Nagel, Inc.",
  "Albatrans Inc.",
  "Antonini Freight Express, Inc.",
  "Checchini Trucking Company, Inc.",
  "Bivio Transport & Logistics, LLC",
  "Cherokee Freight Lines Logistics",
  "Golden Gate Transportation Corp",
  "TQL Total Quality Logistics, LLC",
  "Sethmar Transportation, LLC",
  "Cobalt Wine Logistics, LLC",
  "S.S. Skikos",
  "Yandell Truckaway, Inc.",
  "Cornerstone Systems, Inc.",
  "Trans American Customhouse Brokers, LLC",
];
const GE_CARRIERS = [
  "Maersk",
  "Cosco",
  "Hapag-Lloyd",
  "MSC",
  "ZIM",
  "Matson",
  "NFI",
  "Einride",
  "MaxTrans Logistics",
  "Container Port Group",
  "Point Logistics",
  "RXO",
  "GulfCoast",
  "Buddy Moore Trucking",
  "M&M Cartage Co. Inc.",
  "Brown Trucking Company",
  "Hot Shot Freight and Services",
  "FitzMark LLC",
  "Taylor Truck Lines",
  "Eagle Steel and Metal Products",
  "Mesilla Valley Transportation",
  "V3 Transportation",
  "Western Express Inc",
  "Expedited Trucking Inc",
  "Axle Logistics LLC",
  "Dayton Freight Lines Inc",
  "XPO",
  "Averitt Express Inc",
  "ABFS",
  "CRETE",
  "GULF_RELAY",
  "JR_SCHUGEL",
  "NORTHFIELD_TRUCKING",
  "CC_WAREHOUSE",
  "ATS_TRANSPORTATION_SERVICES",
  "HOLLAND_SPECIAL_DELIVERY",
  "WORLDWIDE_LOGISTICS_INC",
  "SUMMITT_TRUCKING_LLC",
  "PRECISION_STRIP_TRANSPORT",
  "ARRIVE_LOGISTICS",
  "LOAD_ONE_LLC",
  "PRIVATE_FLEET_BACKHAUL_LLC",
  "M_S_LOGISTICS_LLC",
  "CHALLENGER_MOTOR_FREIGHT",
  "TRANSLOOP_LOGISTICS",
  "CHRISTENSON_TRANSPORTATION",
  "MAWSON_AND_MAWSON",
  "TRANSIT_SOLUTIONS",
  "Madison Logistics",
  "CARDINAL_MFG_CO",
  "TOTAL_QUALITY_LOGISTICS",
  "GP Transco",
  "Circle Logistics Inc",
  "TFA Logistics",
];
const JNJ_CARRIERS = [
  "MAGNO",
  "KWE",
  "KNFF",
  "WINA",
  "PRIME",
  "SCHENKER",
  "DHL",
  "DHL GLOBAL FORWARDING - CAN",
  "EXPEDITORS",
  "DSV",
  "UPSN",
  "PRIORITY SOLUTIONS INTERNATIONAL",
  "TOTE_MARITIME",
  "CROWLEY",
  "MAGIC TRANSPORT INC",
  "SCHNEIDER",
  "COYOTE LOGISTICS",
  "TRIBE EXPRESS",
  "C.H. ROBINSON",
  "SKY TRANSPORTATION SERVICES, LLC",
  "FIDELITONE",
  "J P TRANSPORT CORP",
  "MESILLA VALLEY TRANSPORTATION",
  "FRANCISCO VEGA OTERO INC",
  "FROZEN FOOD EXPRESS",
  "FEDEX",
  "LAD TRUCK LINES, INC.",
  "RXO EXPRESS LLC",
  "MAERSK",
  "QUICK INTERNATIONAL COURIER",
  "WORLD COURIER INC",
  "MARKEN LTD",
  "UPS SCS BROKERAGE",
  "EXPEDITORS INTL - BROKERAGE",
  "RUSSELL A FARROW - US",
  "RUSSELL A FARROW - CAN",
  "BDP INTL - BDP TRANSPORT",
  "CRST INTERNATIONAL",
  "FOWLERS EXPRESS INC",
  "CALDERON TRANSPORT",
  "ARCBEST",
  "OLD DOMINION FREIGHT LINE",
  "FEDEX FREIGHT",
  "FEDEX FREIGHT MEXICO",
  "TRAFFIX",
  "APPLE EXPRESS",
  "AIRSPACE TECHNOLOGIES",
  "D C EXPRESS INC",
  "GUARANTEED EXPRESS",
  "MNX GLOBAL LOGISTICS",
  "TECHNICAL TRANSPORTATION",
  "TEX-AIR DELIVERY INC",
  "ATS HEALTHCARE",
];
const CARRIERS_BY_PROJECT = {
  delicato: DELICATO_CARRIERS,
  ge: GE_CARRIERS,
  jnj: JNJ_CARRIERS,
};
const CARRIERS = CARRIERS_BY_PROJECT[PROJECT_ID] || [];

/* ── State ──────────────────────────────────────────────────────────────── */
let currentPage = 1;
let totalPages  = 1;
const PAGE_SIZE = 20;

/** Applied time filter used by loadInvoices */
let timeFilter = { kind: "none", label: "All time", after: null, before: null, chip: "" };
let timePopoverMode = "relative";

/** Custom Status / Carrier dropdown values */
let filterStatusValue = "";
let filterCarrierValue = "";
let filterInvoiceNoValue = "";
let _invoiceNoDebounce = null;

/* ── Boot ───────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  document.title = PAGE_TITLE;
  const brand = document.getElementById("brandName");
  if (brand) brand.textContent = PAGE_TITLE;

  const carrierGroup = document.getElementById("carrierSelectGroup");
  if (carrierGroup && CARRIERS.length === 0) {
    carrierGroup.hidden = true;
  }

  populateCarrierFilter();
  syncTimeChipUI();
  loadAll();
  loadStatuses();
});

function loadAll() {
  loadStats();
  currentPage = 1;
  loadInvoices();
}

function populateCarrierFilter() {
  const menu = document.getElementById("carrierSelectMenu");
  if (!menu) return;
  // Keep the All Carriers option; append carriers
  CARRIERS.forEach((name) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "custom-select-option";
    btn.dataset.value = name;
    btn.textContent = name;
    btn.onclick = () => pickCustomSelect("carrier", name, name);
    menu.appendChild(btn);
  });
}

/* ── Stats cards ─────────────────────────────────────────────────────────── */
async function loadStats() {
  try {
    const params = new URLSearchParams();
    if (filterStatusValue)  params.set("status",  filterStatusValue);
    if (filterCarrierValue) params.set("carrier", filterCarrierValue);
    if (filterInvoiceNoValue) params.set("invoice_number", filterInvoiceNoValue);
    const range = getCreatedRangeParams();
    if (range.created_after)  params.set("created_after",  range.created_after);
    if (range.created_before) params.set("created_before", range.created_before);

    const qs = params.toString();
    const data = await apiFetch(`/stats${qs ? `?${qs}` : ""}`);
    setText("stat-total",      data.total);
    setText("stat-completed",  data.completed);
    setText("stat-processing", data.processing + data.queued);
    setText("stat-failed",     data.failed);
    setText("stat-rejected",   data.rejected);
  } catch (e) {
    console.error("Stats error:", e);
  }
}

/* ── Custom Status / Carrier dropdowns ───────────────────────────────────── */
async function loadStatuses() {
  try {
    const data = await apiFetch("/statuses");
    const menu = document.getElementById("statusSelectMenu");
    if (!menu) return;
    data.statuses.forEach((s) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "custom-select-option";
      btn.dataset.value = s;
      btn.textContent = s;
      btn.onclick = () => pickCustomSelect("status", s, s);
      menu.appendChild(btn);
    });
  } catch (e) {
    console.error("Statuses error:", e);
  }
}

function toggleCustomSelect(which, event) {
  if (event) event.stopPropagation();
  closeTimePopover();
  const other = which === "status" ? "carrier" : "status";
  closeCustomSelect(other);

  const menu = document.getElementById(`${which}SelectMenu`);
  const trigger = document.getElementById(`${which}SelectTrigger`);
  if (!menu || !trigger) return;

  const opening = menu.hidden;
  menu.hidden = !opening;
  trigger.setAttribute("aria-expanded", opening ? "true" : "false");
  trigger.classList.toggle("open", opening);

  if (opening) {
    document.addEventListener("click", onCustomSelectOutside, true);
  } else {
    document.removeEventListener("click", onCustomSelectOutside, true);
  }
}

function closeCustomSelect(which) {
  const menu = document.getElementById(`${which}SelectMenu`);
  const trigger = document.getElementById(`${which}SelectTrigger`);
  if (menu) menu.hidden = true;
  if (trigger) {
    trigger.setAttribute("aria-expanded", "false");
    trigger.classList.remove("open");
  }
}

function closeAllCustomSelects() {
  closeCustomSelect("status");
  closeCustomSelect("carrier");
  document.removeEventListener("click", onCustomSelectOutside, true);
}

function onCustomSelectOutside(e) {
  const statusGroup = document.getElementById("statusSelectGroup");
  const carrierGroup = document.getElementById("carrierSelectGroup");
  if (statusGroup?.contains(e.target) || carrierGroup?.contains(e.target)) return;
  closeAllCustomSelects();
}

function pickCustomSelect(which, value, label) {
  if (which === "status") {
    filterStatusValue = value;
    document.getElementById("statusSelectLabel").textContent = label;
  } else {
    filterCarrierValue = value;
    document.getElementById("carrierSelectLabel").textContent = label;
  }

  const menu = document.getElementById(`${which}SelectMenu`);
  if (menu) {
    menu.querySelectorAll(".custom-select-option").forEach((opt) => {
      opt.classList.toggle("selected", opt.dataset.value === value);
    });
  }

  closeAllCustomSelects();
  applyFilters();
}

/* ── Invoice list ─────────────────────────────────────────────────────────── */
async function loadInvoices() {
  const status  = filterStatusValue;
  const carrier = filterCarrierValue;
  const invoiceNo = filterInvoiceNoValue;
  const range   = getCreatedRangeParams();

  const params = new URLSearchParams({
    page:      currentPage,
    page_size: PAGE_SIZE,
  });
  if (status)  params.set("status",  status);
  if (carrier) params.set("carrier", carrier);
  if (invoiceNo) params.set("invoice_number", invoiceNo);
  if (range.created_after)  params.set("created_after",  range.created_after);
  if (range.created_before) params.set("created_before", range.created_before);

  const tbody = document.getElementById("tableBody");
  tbody.innerHTML = `<tr><td colspan="7" class="loading">Loading…</td></tr>`;

  try {
    const data = await apiFetch(`/invoices?${params}`);
    totalPages = data.total_pages || 1;
    renderTable(data.items, data.total);
    renderPagination(data.total);
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">Error loading data: ${e.message}</td></tr>`;
  }
}

function getCreatedRangeParams() {
  const out = {};
  if (timeFilter.after)  out.created_after  = timeFilter.after;
  if (timeFilter.before) out.created_before = timeFilter.before;
  return out;
}

const PRESET_MS = {
  "1h":  1 * 60 * 60 * 1000,
  "12h": 12 * 60 * 60 * 1000,
  "1d":  1 * 24 * 60 * 60 * 1000,
  "3d":  3 * 24 * 60 * 60 * 1000,
  "7d":  7 * 24 * 60 * 60 * 1000,
};

const UNIT_MS = {
  m: 60 * 1000,
  h: 60 * 60 * 1000,
  d: 24 * 60 * 60 * 1000,
  w: 7 * 24 * 60 * 60 * 1000,
};

const UNIT_LABEL = { m: "minutes", h: "hours", d: "days", w: "weeks" };

function applyTimePreset(preset, btn) {
  closeTimePopover();
  if (!preset) {
    clearTimeFilter(false);
    applyFilters();
    return;
  }
  const ms = PRESET_MS[preset];
  if (!ms) return;
  timeFilter = {
    kind: "relative",
    label: `Last ${preset}`,
    after: new Date(Date.now() - ms).toISOString(),
    before: null,
    chip: preset,
  };
  syncTimeChipUI();
  applyFilters();
}

function clearTimeFilter(runApply = true) {
  timeFilter = { kind: "none", label: "All time", after: null, before: null, chip: "" };
  syncTimeChipUI();
  closeTimePopover();
  if (runApply) applyFilters();
}

function syncTimeChipUI() {
  document.querySelectorAll(".time-chip").forEach((el) => {
    const preset = el.getAttribute("data-preset");
    const isCustom = el.classList.contains("time-chip-custom");
    let active = false;
    if (isCustom) {
      active = timeFilter.kind === "absolute" || (timeFilter.kind === "relative" && !PRESET_MS[timeFilter.chip]);
    } else if (preset === "" && timeFilter.kind === "none") {
      active = true;
    } else if (preset && timeFilter.chip === preset) {
      active = true;
    }
    el.classList.toggle("active", active);
  });
  const label = document.getElementById("timeActiveLabel");
  if (label) label.textContent = timeFilter.label || "All time";
}

function toggleTimePopover(event) {
  if (event) event.stopPropagation();
  closeAllCustomSelects();
  const pop = document.getElementById("timePopover");
  if (!pop) return;
  const opening = pop.hidden;
  pop.hidden = !opening;
  if (opening) {
    setTimePopoverMode(timePopoverMode);
    document.addEventListener("click", onTimePopoverOutside, true);
  } else {
    document.removeEventListener("click", onTimePopoverOutside, true);
  }
}

function closeTimePopover() {
  const pop = document.getElementById("timePopover");
  if (pop) pop.hidden = true;
  document.removeEventListener("click", onTimePopoverOutside, true);
}

function onTimePopoverOutside(e) {
  const pop = document.getElementById("timePopover");
  const group = document.querySelector(".time-filter-group");
  if (!pop || pop.hidden) return;
  if (group && group.contains(e.target)) return;
  closeTimePopover();
}

function setTimePopoverMode(mode) {
  timePopoverMode = mode;
  document.querySelectorAll(".time-mode-btn").forEach((b) => {
    b.classList.toggle("active", b.getAttribute("data-mode") === mode);
  });
  document.getElementById("timeRelativePane").hidden = mode !== "relative";
  document.getElementById("timeAbsolutePane").hidden = mode !== "absolute";
}

function selectRelativeDraft(amount, unit) {
  document.getElementById("relDuration").value = amount;
  document.getElementById("relUnit").value = unit;
  document.querySelectorAll(".time-preset-btn").forEach((b) => b.classList.remove("active"));
  // Highlight the clicked button via event isn't passed — match by text + unit in onclick
  document.querySelectorAll(`.time-preset-btn[onclick*="selectRelativeDraft(${amount},'${unit}')"]`)
    .forEach((b) => b.classList.add("active"));
}

function applyTimePopover() {
  if (timePopoverMode === "relative") {
    const amount = parseInt(document.getElementById("relDuration").value, 10);
    const unit = document.getElementById("relUnit").value;
    if (!amount || amount < 1 || !UNIT_MS[unit]) {
      alert("Enter a valid duration.");
      return;
    }
    const ms = amount * UNIT_MS[unit];
    const unitName = UNIT_LABEL[unit];
    timeFilter = {
      kind: "relative",
      label: `Last ${amount} ${unitName}`,
      after: new Date(Date.now() - ms).toISOString(),
      before: null,
      chip: "custom",
    };
  } else {
    const from = document.getElementById("absFrom").value;
    const to = document.getElementById("absTo").value;
    if (!from && !to) {
      alert("Pick a From and/or To time.");
      return;
    }
    timeFilter = {
      kind: "absolute",
      label: [
        from ? `From ${from.replace("T", " ")}` : null,
        to ? `To ${to.replace("T", " ")}` : null,
      ].filter(Boolean).join(" · "),
      after: from ? new Date(from).toISOString() : null,
      before: to ? new Date(to).toISOString() : null,
      chip: "custom",
    };
  }
  syncTimeChipUI();
  closeTimePopover();
  applyFilters();
}

function renderTable(items, total) {
  const tbody = document.getElementById("tableBody");

  if (!items || items.length === 0) {
    const when = timeFilter.label || "the selected time";
    tbody.innerHTML = `<tr><td colspan="7" class="empty">No records for ${esc(when)}</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(inv => `
    <tr>
      <td>${esc(inv.invoice_number) || "—"}</td>
      <td><div class="carrier-cell">${esc(inv.carrier_name) || "—"}</div></td>
      <td>${esc(inv.invoice_date) || "—"}</td>
      <td>${statusBadge(inv.status)}</td>
      <td style="white-space:nowrap;font-size:12px;color:#64748b">${esc(inv.created_at) || "—"}</td>
      <td>
        ${inv.error_message
          ? `<div class="error-cell" title="${esc(inv.error_message)}">${esc(shortError(inv))}</div>`
          : '<span style="color:#94a3b8;font-size:12px">—</span>'}
      </td>
      <td>
        <button class="detail-btn"
          onclick="openDetail('${esc(inv.email_id)}','${esc(inv.attachment_id)}')">
          Details
        </button>
      </td>
    </tr>
  `).join("");
}

/* Prefer missing fields list as short error label */
function shortError(inv) {
  if (inv.missing_fields && inv.missing_fields.length > 0) {
    return `Missing: ${inv.missing_fields.slice(0,2).join(", ")}${inv.missing_fields.length > 2 ? "…" : ""}`;
  }
  if (inv.error_code) return inv.error_code;
  return inv.error_message ? inv.error_message.substring(0, 60) : "";
}

/* ── Pagination ──────────────────────────────────────────────────────────── */
function renderPagination(total) {
  const pageSize = PAGE_SIZE;
  const container = document.getElementById("pagination");

  if (total === 0) { container.innerHTML = ""; return; }

  const start = (currentPage - 1) * pageSize + 1;
  const end   = Math.min(currentPage * pageSize, total);

  let html = `<button class="page-btn" onclick="goPage(${currentPage - 1})"
    ${currentPage <= 1 ? "disabled" : ""}>‹ Prev</button>`;

  html += `<span class="page-info">${start}–${end} of ${total}</span>`;

  // Page number buttons — show up to 7 around current
  const range = pageRange(currentPage, totalPages);
  range.forEach(p => {
    if (p === "...") {
      html += `<span style="padding:0 4px;color:#94a3b8">…</span>`;
    } else {
      html += `<button class="page-btn ${p === currentPage ? "active" : ""}"
        onclick="goPage(${p})">${p}</button>`;
    }
  });

  html += `<button class="page-btn" onclick="goPage(${currentPage + 1})"
    ${currentPage >= totalPages ? "disabled" : ""}>Next ›</button>`;

  container.innerHTML = html;
}

function pageRange(current, total) {
  if (total <= 7) return Array.from({length: total}, (_, i) => i + 1);
  if (current <= 4) return [1, 2, 3, 4, 5, "...", total];
  if (current >= total - 3) return [1, "...", total-4, total-3, total-2, total-1, total];
  return [1, "...", current-1, current, current+1, "...", total];
}

function goPage(p) {
  if (p < 1 || p > totalPages) return;
  currentPage = p;
  loadInvoices();
}

/* ── Filters ─────────────────────────────────────────────────────────────── */
function onInvoiceNoInput() {
  const el = document.getElementById("invoiceNoInput");
  filterInvoiceNoValue = (el?.value || "").trim();
  if (_invoiceNoDebounce) clearTimeout(_invoiceNoDebounce);
  _invoiceNoDebounce = setTimeout(() => applyFilters(), 300);
}

function applyFilters() {
  currentPage = 1;
  loadStats();
  loadInvoices();
}

/* ── Detail modal ─────────────────────────────────────────────────────────── */
async function openDetail(emailId, attachmentId) {
  document.getElementById("modalTitle").textContent = "Loading…";
  document.getElementById("modalBody").innerHTML =
    `<p style="text-align:center;color:#94a3b8;padding:40px">Loading details…</p>`;
  document.getElementById("modalBackdrop").classList.add("open");
  document.getElementById("detailModal").classList.add("open");

  try {
    const inv = await apiFetch(`/invoices/${encodeURIComponent(emailId)}/${encodeURIComponent(attachmentId)}`);
    renderModal(inv);
  } catch (e) {
    document.getElementById("modalBody").innerHTML =
      `<p style="color:#ef4444;padding:20px">Error loading details: ${e.message}</p>`;
  }
}

function renderModal(inv) {
  document.getElementById("modalTitle").textContent =
    inv.filename || "Invoice Detail";

  /* Store email_id on the modal so the Re-trigger handler can read it */
  const modal = document.getElementById("detailModal");
  modal.dataset.emailId = inv.email_id || "";

  /* Render Re-trigger button in the modal header */
  const existingBtn = document.getElementById("retriggerBtn");
  if (existingBtn) existingBtn.remove();
  if (inv.email_id) {
    const btn = document.createElement("button");
    btn.id = "retriggerBtn";
    btn.type = "button";
    btn.className = "retrigger-btn";
    btn.title = "Delete all DynamoDB records for this email and re-upload to S3 to restart processing";
    btn.innerHTML = `
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
        aria-hidden="true">
        <polyline points="1 4 1 10 7 10"></polyline>
        <path d="M3.51 15a9 9 0 1 0 .49-3.5"></path>
      </svg>
      <span class="retrigger-label">Re-trigger</span>`;
    btn.onclick = () => retriggerInvoice(inv.email_id);
    document.querySelector("#detailModal .modal-header").insertBefore(
      btn,
      document.querySelector("#detailModal .modal-close")
    );
  }

  let html = "";

  /* ── Core info ── */
  html += section("Invoice Information", grid([
    ["Filename",       inv.filename,       false],
    ["Status",         statusBadge(inv.status), true],
    ["Carrier",        inv.carrier_name,   false],
    ["Invoice No",     inv.invoice_number, false],
    ["Invoice Date",   inv.invoice_date,   false],
    ["Mode",           inv.mode,           false],
    ["Created",        inv.created_at_iso || inv.created_at, false],
    ["Updated",        inv.updated_at_iso || inv.updated_at, false],
    ["Completed",      inv.completed_at_iso, false],
    ["Confidence",     inv.confidence_score, false],
    ["Textract Carrier", inv.textract_carrier, false],
    ["Vision Carrier",   inv.vision_carrier,  false],
  ]));

  /* ── Error ── */
  if (inv.error_code || inv.error_message || (inv.missing_fields && inv.missing_fields.length > 0)) {
    let errorHtml = `<div class="detail-grid">`;
    if (inv.error_code) {
      errorHtml += detailRow("Error Code", `<span class="detail-val error">${esc(inv.error_code)}</span>`, true);
    }
    if (inv.error_message) {
      errorHtml += `<div class="detail-row" style="grid-column:span 2">
        <div class="detail-key">Error Message</div>
        <div class="detail-val error">${esc(inv.error_message)}</div>
      </div>`;
    }
    if (inv.missing_fields && inv.missing_fields.length > 0) {
      const tags = inv.missing_fields.map(f => `<span class="missing-field-tag">${esc(f)}</span>`).join("");
      errorHtml += `<div class="detail-row" style="grid-column:span 2">
        <div class="detail-key">Missing Fields (${inv.missing_fields.length})</div>
        <div class="detail-val">${tags}</div>
      </div>`;
    }
    errorHtml += `</div>`;
    html += section("Error Details", errorHtml, true);
  }

  /* ── Transaction steps (pipeline timeline) ── */
  const steps = Array.isArray(inv.transaction_steps) ? inv.transaction_steps : [];
  if (steps.length > 0) {
    let stepsHtml = `<div class="steps-timeline">`;
    steps.forEach((step, idx) => {
      const key = step.step_key || `Step ${idx + 1}`;
      const stepStatus = step.step_status || "";
      const txStatus = step.transaction_status || "";
      const started = step.step_started_at || "";
      const completed = step.step_completed_at || "";
      const errCode = step.step_error_code || "";
      const errMsg = step.step_error_message || "";
      const hasError = !!(errCode || errMsg);
      stepsHtml += `
        <div class="step-row${hasError ? " step-row-error" : ""}">
          <div class="step-index">${idx + 1}</div>
          <div class="step-body">
            <div class="step-head">
              <span class="step-key">${esc(String(key).replace(/_/g, " "))}</span>
              ${stepStatus ? statusBadge(stepStatus) : ""}
              ${txStatus ? `<span class="step-tx">txn: ${statusBadge(txStatus)}</span>` : ""}
            </div>
            <div class="step-meta">
              ${started ? `<span>Started ${esc(started)}</span>` : ""}
              ${completed ? `<span>Completed ${esc(completed)}</span>` : ""}
            </div>
            ${hasError ? `
              <div class="step-error">
                ${errCode ? `<strong>${esc(errCode)}</strong>` : ""}
                ${errMsg ? `<span>${esc(errMsg)}</span>` : ""}
              </div>` : ""}
          </div>
        </div>`;
    });
    stepsHtml += `</div>`;
    html += section("Transaction Steps", stepsHtml, true);
  }

  /* ── Pipeline stages ── */
  const stages = [
    ["Clustering",      inv.stage_clustering],
    ["Textract",        inv.stage_textract],
    ["LLM Extraction",  inv.stage_llm_extraction],
    ["Validation",      inv.stage_validation],
  ].filter(([, v]) => v && Object.keys(v).length > 0);

  if (stages.length > 0) {
    let stagesHtml = `<div class="detail-grid">`;
    stages.forEach(([name, stage]) => {
      const missingCount = stage.missing_fields_count || stage.missing_fields?.length || 0;
      const fieldsExtracted = stage.fields_extracted != null ? ` · ${stage.fields_extracted} fields` : "";
      const clusters = stage.clusters_found != null ? ` · ${stage.clusters_found} clusters` : "";
      stagesHtml += `<div class="detail-row">
        <div class="detail-key">${name}</div>
        <div class="detail-val" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          ${stage.status ? statusBadge(stage.status) : '<span style="color:#94a3b8">—</span>'}
          ${missingCount > 0
            ? `<span style="font-size:12px;color:#ef4444">${missingCount} missing field${missingCount > 1 ? "s" : ""}</span>`
            : ""}
          ${fieldsExtracted
            ? `<span style="font-size:12px;color:#64748b">${fieldsExtracted}</span>`
            : ""}
          ${clusters
            ? `<span style="font-size:12px;color:#64748b">${clusters}</span>`
            : ""}
          ${stage.completed_at
            ? `<span style="font-size:12px;color:#94a3b8">${esc(stage.completed_at)}</span>`
            : ""}
        </div>
      </div>`;
    });
    stagesHtml += `</div>`;
    html += section("Pipeline Stages", stagesHtml, true);
  }

  /* ── API response ── */
  const apiCode = inv.api_status_code;
  const apiSuccess = inv.api_success;
  if (apiCode || apiSuccess) {
    const codeNum = parseInt(apiCode);
    const codeColor = codeNum === 200 ? "#22c55e" : codeNum >= 400 ? "#ef4444" : "#64748b";
    html += section("API Response", grid([
      ["Status Code", apiCode
        ? `<span style="font-weight:700;color:${codeColor}">${esc(apiCode)}</span>`
        : "—", true],
      ["Success", apiSuccess === "True" || apiSuccess === "true"
        ? `<span style="color:#22c55e;font-weight:600">✓ Yes</span>`
        : `<span style="color:#ef4444;font-weight:600">✗ No</span>`, true],
    ]));
  }

  /* ── API Payload (expandable JSON) ── */
  if (inv.api_payload != null) {
    const pretty = typeof inv.api_payload === "string"
      ? (() => {
          try { return JSON.stringify(JSON.parse(inv.api_payload), null, 2); }
          catch { return inv.api_payload; }
        })()
      : JSON.stringify(inv.api_payload, null, 2);
    const previewLimit = 400;
    const preview = pretty.length > previewLimit
      ? pretty.slice(0, previewLimit) + "\n…"
      : pretty;
    const id = "payload-" + Math.random().toString(36).slice(2, 9);
    const canExpand = pretty.length > previewLimit;
    window.__payloadStore = window.__payloadStore || {};
    window.__payloadStore[id] = pretty;

    const actions = `
      <div class="payload-actions">
        ${canExpand
          ? `<button type="button" class="payload-expand-btn" id="${id}-toggle"
               onclick="togglePayload('${id}', this)">Expand</button>`
          : ""}
        <button type="button" class="payload-copy-btn" title="Copy payload"
          onclick="copyPayload('${id}', this)" aria-label="Copy payload">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
            aria-hidden="true">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
          </svg>
          <span class="payload-copy-label">Copy</span>
        </button>
      </div>`;

    html += `
      <div class="detail-section" id="${id}-section">
        <div class="detail-section-title with-actions">
          <span>API Payload</span>
          ${actions}
        </div>
        <div class="payload-wrap" id="${id}-wrap">
          <pre class="payload-pre payload-preview" id="${id}-preview">${esc(preview)}</pre>
          <pre class="payload-pre payload-full" id="${id}-full" hidden>${esc(pretty)}</pre>
        </div>
      </div>`;
  }

  document.getElementById("modalBody").innerHTML = html;
}

function togglePayload(id, btn) {
  const wrap = document.getElementById(id + "-wrap");
  const section = document.getElementById(id + "-section");
  const preview = document.getElementById(id + "-preview");
  const full = document.getElementById(id + "-full");
  if (!preview || !full) return;

  const willExpand = full.hidden;
  full.hidden = !willExpand;
  preview.hidden = willExpand;
  btn.textContent = willExpand ? "Collapse" : "Expand";
  if (wrap) wrap.classList.toggle("expanded", willExpand);
  if (section) section.classList.toggle("payload-expanded", willExpand);

  if (willExpand && full) {
    full.scrollTop = 0;
  }
}

async function copyPayload(id, btn) {
  const text = (window.__payloadStore && window.__payloadStore[id])
    || (document.getElementById(id + "-full") || document.getElementById(id + "-preview"))?.textContent
    || "";
  const label = btn.querySelector(".payload-copy-label");
  try {
    await navigator.clipboard.writeText(text);
    if (label) label.textContent = "Copied";
    btn.classList.add("copied");
    setTimeout(() => {
      if (label) label.textContent = "Copy";
      btn.classList.remove("copied");
    }, 1600);
  } catch (e) {
    if (label) label.textContent = "Failed";
    setTimeout(() => { if (label) label.textContent = "Copy"; }, 1600);
  }
}

/* ── Re-trigger ───────────────────────────────────────────────────────────── */

/**
 * Show an inline confirmation banner at the top of the modal body.
 * If confirmed, delete all DynamoDB rows for this email and re-PUT to S3.
 */
function retriggerInvoice(emailId) {
  if (!emailId) return;

  /* Remove any existing confirmation panel first */
  const existing = document.getElementById("retriggerConfirmPanel");
  if (existing) { existing.remove(); return; }

  const panel = document.createElement("div");
  panel.id = "retriggerConfirmPanel";
  panel.className = "retrigger-confirm-panel";
  panel.innerHTML = `
    <div class="retrigger-confirm-icon">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
        <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
      </svg>
    </div>
    <div class="retrigger-confirm-body">
      <p class="retrigger-confirm-title">Re-trigger invoice processing?</p>
      <div class="retrigger-pk-badge">
        <span class="retrigger-pk-label">PK</span>
        <span class="retrigger-pk-value">EMAIL#${esc(emailId)}</span>
      </div>
      <ul class="retrigger-confirm-list">
        <li>All DynamoDB records under this PK will be deleted</li>
        <li>The original file will be re-uploaded to S3 to restart the pipeline</li>
        <li>This action cannot be undone</li>
      </ul>
    </div>
    <div class="retrigger-confirm-actions">
      <button type="button" class="retrigger-cancel-btn" onclick="document.getElementById('retriggerConfirmPanel')?.remove()">Cancel</button>
      <button type="button" class="retrigger-confirm-btn" id="retriggerConfirmBtn" onclick="executeRetrigger('${esc(emailId)}')">Confirm</button>
    </div>`;

  /* Insert at the top of the modal body */
  const body = document.getElementById("modalBody");
  if (body) body.insertBefore(panel, body.firstChild);
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function executeRetrigger(emailId) {
  const panel = document.getElementById("retriggerConfirmPanel");
  const confirmBtn = document.getElementById("retriggerConfirmBtn");
  const cancelBtn = panel && panel.querySelector(".retrigger-cancel-btn");

  /* Switch panel to loading state */
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = "Retriggering…"; }
  if (cancelBtn)  { cancelBtn.disabled = true; }

  /* Header button loading state */
  const btn = document.getElementById("retriggerBtn");
  const label = btn && btn.querySelector(".retrigger-label");
  if (btn) { btn.disabled = true; btn.classList.add("retrigger-loading"); }
  if (label) label.textContent = "Retriggering…";

  try {
    const result = await apiFetch(
      `/invoices/${encodeURIComponent(emailId)}/retrigger`,
      { method: "POST" }
    );

    /* Remove confirmation panel and show inline success banner */
    if (panel) panel.remove();

    const successPanel = document.createElement("div");
    successPanel.className = "retrigger-success-panel";
    successPanel.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
        stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="20 6 9 17 4 12"/>
      </svg>
      Re-triggered successfully — ${result.deleted_count} record${result.deleted_count !== 1 ? "s" : ""} deleted, pipeline restarted.`;
    const body = document.getElementById("modalBody");
    if (body) body.insertBefore(successPanel, body.firstChild);
    setTimeout(() => successPanel.remove(), 6000);

    /* Reset header button */
    if (btn) { btn.classList.remove("retrigger-loading"); btn.classList.add("retrigger-success"); btn.disabled = false; }
    if (label) label.textContent = "Retriggered!";
    setTimeout(() => {
      if (label) label.textContent = "Re-trigger";
      if (btn) btn.classList.remove("retrigger-success");
    }, 4000);

  } catch (e) {
    /* Show inline error inside the confirmation panel */
    if (panel) {
      const errEl = document.createElement("div");
      errEl.className = "retrigger-error-msg";
      errEl.textContent = `Failed: ${e.message}`;
      panel.appendChild(errEl);
    }
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = "Retry"; }
    if (cancelBtn)  { cancelBtn.disabled = false; }
    if (btn) { btn.disabled = false; btn.classList.remove("retrigger-loading"); }
    if (label) label.textContent = "Re-trigger";
  }
}

function section(title, innerHtml, rawInner = false) {
  return `<div class="detail-section">
    <div class="detail-section-title">${title}</div>
    ${rawInner ? innerHtml : `<div class="detail-grid">${innerHtml}</div>`}
  </div>`;
}

function grid(rows) {
  return rows
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v, raw]) => detailRow(k, v, raw))
    .join("");
}

function detailRow(key, val, raw = false) {
  return `<div class="detail-row">
    <div class="detail-key">${key}</div>
    ${raw
      ? `<div class="detail-val">${val}</div>`
      : `<div class="detail-val">${esc(String(val ?? ""))}</div>`}
  </div>`;
}

function closeModal() {
  document.getElementById("modalBackdrop").classList.remove("open");
  document.getElementById("detailModal").classList.remove("open");
}

/* ── Status badge ─────────────────────────────────────────────────────────── */
function statusBadge(status) {
  if (!status) return "—";
  const cls = {
    "COMPLETED":                  "badge-completed",
    "SUCCESS":                    "badge-completed",
    "PASSED":                     "badge-completed",
    "FAILED":                     "badge-failed",
    "API_FAILED":                 "badge-api-failed",
    "PROCESSING":                 "badge-processing",
    "QUEUED":                     "badge-queued",
    "BATCH_QUEUED":               "badge-queued",
    "REJECTED_MULTIPLE_INVOICES": "badge-rejected",
    "REJECTED_FILENAME_TOO_LONG": "badge-rejected",
  }[status] || "badge-default";
  return `<span class="badge ${cls}">${status.replace(/_/g, " ")}</span>`;
}

/* ── Utils ───────────────────────────────────────────────────────────────── */
function esc(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val ?? "—";
}

async function apiFetch(path, options = {}) {
  const token = localStorage.getItem("pando_token");
  const headers = { ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const resp = await fetch(API_BASE + path, { ...options, headers });
  if (resp.status === 401) {
    localStorage.removeItem("pando_token");
    localStorage.removeItem("pando_user");
    (window.top || window).location.href = "/login";
    throw new Error("Session expired");
  }
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`HTTP ${resp.status}: ${text}`);
  }
  return resp.json();
}
