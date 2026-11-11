/* JewelLeads front end. No build step: this file is served as-is. */

const VIEWS = ["dashboard", "search", "leads", "import", "settings"];
const VIEW_TITLES = {
  dashboard: "Dashboard",
  search: "Find Shops",
  leads: "My Leads",
  import: "Import",
  settings: "Settings",
};
const STATUSES = [
  "New",
  "Contacted",
  "Interested",
  "Demo Scheduled",
  "Demo Done",
  "Customer",
  "Not Interested",
  "Wrong Number",
];
const SOURCES = ["OpenStreetMap", "Facebook", "Manual", "Import"];
const CHIPS = [
  { id: "jewellers", label: "Jewellers" },
  { id: "gold", label: "Gold" },
  { id: "sarafa", label: "Sarafa" },
  { id: "goldsmith", label: "Goldsmith" },
  { id: "ornaments", label: "Ornaments" },
];
const STATUS_CHART_COLORS = {
  New: "#94a3b8",
  Contacted: "#38bdf8",
  Interested: "#f59e0b",
  "Demo Scheduled": "#a78bfa",
  "Demo Done": "#818cf8",
  Customer: "#34d399",
  "Not Interested": "#fb7185",
  "Wrong Number": "#fb923c",
};

const state = {
  view: "dashboard",
  settings: null,
  dashboard: null,
  chart: null,
  leads: [],
  total: 0,
  page: 1,
  pageSize: 25,
  cities: [],
  filters: {
    q: "",
    city: "",
    status: "",
    source: "",
    has_phone: "",
    has_whatsapp: "",
    sort: "created_at",
    direction: "desc",
  },
  selected: new Set(),
  search: {
    city: "",
    keyword: "",
    area: "",
    chips: ["jewellers"],
    refresh: false,
    loading: false,
    error: "",
    result: null,
    local: "",
  },
  importPreview: null,
  importResult: null,
  drawer: null,
  modalResolve: null,
};

let renderToken = 0;
let searchTimer = 0;
let enrichToken = 0;
let lastFocus = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function statusClass(status) {
  return "status-" + String(status || "New").replace(/\s+/g, "");
}

function toast(message, kind) {
  const root = document.getElementById("toast-root");
  const item = document.createElement("div");
  item.className = "toast" + (kind === "error" ? " error" : "");
  item.textContent = message;
  root.appendChild(item);
  setTimeout(() => item.remove(), 4200);
}

async function api(path, options) {
  const opts = options || {};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeout || 30000);
  const headers = Object.assign({}, opts.headers || {});
  let body = opts.body;
  if (body && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  try {
    const response = await fetch(path, {
      method: opts.method || "GET",
      headers,
      body,
      signal: controller.signal,
      credentials: "same-origin",
    });
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (_error) {
        data = { detail: text.slice(0, 240) };
      }
    }
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("Sign in required.");
    }
    if (!response.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === "string" ? detail : "Request failed.");
    }
    return data;
  } catch (error) {
    const message = error.name === "AbortError" ? "The request timed out. Try again." : error.message;
    if (!opts.silent) toast(message, "error");
    throw new Error(message);
  } finally {
    clearTimeout(timer);
  }
}

function confirmDialog(options) {
  return new Promise((resolve) => {
    const root = document.getElementById("modal-root");
    const extra = options.extraLabel
      ? `<button type="button" class="btn btn-secondary" data-choice="extra">${esc(options.extraLabel)}</button>`
      : "";
    root.innerHTML = `
      <div class="modal-backdrop">
        <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
          <h2 id="modal-title">${esc(options.title)}</h2>
          <p>${esc(options.body)}</p>
          <div class="row" style="justify-content:flex-end">
            <button type="button" class="btn btn-ghost" data-choice="cancel">${esc(options.cancelLabel || "Cancel")}</button>
            ${extra}
            <button type="button" class="btn btn-primary" data-choice="confirm">${esc(options.confirmLabel || "Confirm")}</button>
          </div>
        </div>
      </div>`;
    root.hidden = false;
    state.modalResolve = (value) => {
      state.modalResolve = null;
      root.hidden = true;
      root.innerHTML = "";
      resolve(value);
    };
    const confirm = root.querySelector("[data-choice=confirm]");
    if (confirm) confirm.focus();
  });
}

function closeModal(value) {
  if (state.modalResolve) state.modalResolve(value || "cancel");
}

function shortPlace(label) {
  const parts = String(label || "").split(",").map((part) => part.trim()).filter(Boolean);
  return parts.slice(0, 2).join(", ") || label || "";
}

function gemSvg() {
  return `<svg width="72" height="72" viewBox="0 0 64 64" aria-hidden="true"><path d="M32 6 54 24 32 58 10 24Z" fill="#fde68a"/><path d="M10 24h44L32 14Z" fill="#d97706"/></svg>`;
}

function setTheme(dark) {
  document.documentElement.classList.toggle("dark", dark);
  localStorage.setItem("jewelleads-theme", dark ? "dark" : "light");
  const button = document.getElementById("theme-toggle");
  if (button) {
    button.textContent = dark ? "Light mode" : "Dark mode";
    button.setAttribute("aria-pressed", dark ? "true" : "false");
  }
  if (state.view === "dashboard" && state.dashboard) drawChart();
}

function syncThemeButton() {
  setTheme(document.documentElement.classList.contains("dark"));
}

function closeSidebar() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("backdrop").hidden = true;
}

function openSidebar() {
  document.getElementById("sidebar").classList.add("open");
  document.getElementById("backdrop").hidden = false;
}

function syncNav() {
  document.querySelectorAll("[data-nav]").forEach((button) => {
    const active = button.dataset.nav === state.view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const title = document.getElementById("top-title");
  if (title) title.textContent = VIEW_TITLES[state.view] || "JewelLeads";
}

function showView(view) {
  if (!VIEWS.includes(view)) view = "dashboard";
  state.view = view;
  const hash = "#/" + view;
  if (location.hash !== hash) location.hash = hash;
  syncNav();
  closeDrawer();
  closeSidebar();
  destroyChart();
  const main = document.getElementById("main");
  renderToken += 1;
  if (view === "dashboard") renderDashboard(main);
  else if (view === "search") renderSearch(main);
  else if (view === "leads") renderLeads(main);
  else if (view === "import") renderImport(main);
  else renderSettings(main);
  const heading = main.querySelector("h1");
  if (heading) {
    heading.tabIndex = -1;
    heading.focus();
  }
}

function destroyChart() {
  if (state.chart) {
    state.chart.destroy();
    state.chart = null;
  }
}

function drawChart() {
  destroyChart();
  const canvas = document.getElementById("status-chart");
  if (!canvas || typeof Chart === "undefined" || !state.dashboard) return;
  const counts = state.dashboard.by_status || {};
  const labels = STATUSES.filter((status) => counts[status]);
  if (!labels.length) return;
  const styles = getComputedStyle(document.documentElement);
  const text = styles.getPropertyValue("--muted").trim() || "#64748b";
  state.chart = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data: labels.map((status) => counts[status]),
        backgroundColor: labels.map((status) => STATUS_CHART_COLORS[status]),
        borderWidth: 0,
        hoverOffset: 4,
      }],
    },
    options: {
      plugins: {
        legend: { position: "bottom", labels: { color: text, boxWidth: 12, padding: 12 } },
      },
      cutout: "68%",
    },
  });
}

async function renderDashboard(main) {
  const token = renderToken;
  main.innerHTML = `<div class="skeleton"><i></i><i></i><i></i></div>`;
  let data;
  try {
    data = await api("/api/dashboard");
  } catch (_error) {
    return;
  }
  if (token !== renderToken || state.view !== "dashboard") return;
  state.dashboard = data;
  const cards = [
    ["", "Total leads", data.total],
    ["New", "New", data.new],
    ["Contacted", "Contacted", data.contacted],
    ["Interested", "Interested", data.interested],
    ["Customer", "Customers", data.customers],
    ["followups", "Follow-ups due today", data.followups_today],
  ];
  const followups = data.followups || [];
  const followHtml = followups.length
    ? followups.map((lead) => `
        <div class="follow-item">
          <div>
            <strong>${esc(lead.name)}</strong>
            <div class="tiny">${esc(lead.city || "No city")} · ${esc(lead.follow_up_date)}
              ${lead.overdue ? '<span class="overdue">Overdue</span>' : ""}
            </div>
          </div>
          <button type="button" class="btn btn-secondary btn-sm" data-action="open-lead" data-id="${lead.id}">Open</button>
        </div>`).join("")
    : `<div class="empty">${gemSvg()}<p>No follow-ups due. Set a date on a lead and it will show up here.</p></div>`;
  const overdueNote = data.followups_overdue
    ? `<p class="tiny">${data.followups_overdue} overdue</p>`
    : "";
  main.innerHTML = `
    <div class="stack">
      <div>
        <h1>Dashboard</h1>
        <p class="muted lead">A private list of jewellery shops. Nothing here is sent to a paid service.</p>
      </div>
      <div class="grid-stats">
        ${cards.map(([status, label, value]) => `
          <button type="button" class="stat" data-action="goto-status" data-status="${esc(status)}">
            <span>${esc(label)}</span>
            <b>${esc(value)}</b>
            ${status === "followups" ? overdueNote : ""}
          </button>`).join("")}
      </div>
      <div class="grid-2">
        <section class="card">
          <h2>Leads by status</h2>
          ${data.total ? '<canvas id="status-chart" height="220" aria-label="Leads by status"></canvas>' : `<div class="empty">${gemSvg()}<p>Save a shop and the chart will fill in.</p><button type="button" class="btn btn-primary" data-nav="search">Find shops</button></div>`}
        </section>
        <section class="card">
          <div class="spread"><h2>Today's follow-ups</h2><span class="tiny">${esc(data.today)}</span></div>
          ${followHtml}
        </section>
      </div>
    </div>`;
  drawChart();
}

function renderSearch(main) {
  const chips = CHIPS.map((chip) => {
    const on = state.search.chips.includes(chip.id);
    return `<button type="button" class="chip${on ? " active" : ""}" data-action="chip" data-chip="${chip.id}" aria-pressed="${on ? "true" : "false"}">${esc(chip.label)}</button>`;
  }).join("");
  main.innerHTML = `
    <div class="stack">
      <div class="spread">
        <div>
          <h1>Find shops</h1>
          <p class="muted lead">Search OpenStreetMap for jewellery shops and goldsmiths. Keywords also match shop names such as gold, sarafa, and zewar.</p>
        </div>
        <div class="row">
          <button type="button" class="btn btn-secondary" data-action="facebook-search">Search on Facebook</button>
          <button type="button" class="btn btn-primary" data-action="add-lead">Add lead manually</button>
        </div>
      </div>
      <form id="search-form" class="card stack">
        <div class="form-grid two">
          <label>City
            <input class="field" id="search-city" name="city" required placeholder="Rawalpindi" value="${esc(state.search.city)}" autocomplete="address-level2">
          </label>
          <label>Area <span class="tiny">(optional)</span>
            <input class="field" id="search-area" placeholder="Saddar, Satellite Town…" value="${esc(state.search.area)}">
          </label>
        </div>
        <div>
          <span class="label">Shop type</span>
          <div class="chips">${chips}</div>
        </div>
        <label>Custom keyword
          <input class="field" id="search-keyword" placeholder="zewar, sona  or a pattern like jewel|gold" value="${esc(state.search.keyword)}">
          <p class="hint tiny">Leave this empty to use the selected chips. A bar (|) is treated as a pattern.</p>
        </label>
        <div class="spread">
          <label class="check"><input type="checkbox" id="search-refresh" ${state.search.refresh ? "checked" : ""}> Bypass the 24-hour cache</label>
          <button type="submit" class="btn btn-primary" id="search-button">Search</button>
        </div>
      </form>
      <div id="search-results"></div>
      <p class="tiny">Shop locations © OpenStreetMap contributors. After a search, JewelLeads reads each shop's public Google listing for a phone, address, hours, and rating. Facebook opens in a new tab and is not collected automatically.</p>
    </div>`;
  paintSearchResults();
}

function setSearchBusy(busy) {
  const button = document.getElementById("search-button");
  if (!button) return;
  button.disabled = busy;
  button.innerHTML = busy ? '<span class="spinner" aria-hidden="true"></span> Searching map data…' : "Search";
}

function visibleShops() {
  const result = state.search.result;
  if (!result) return [];
  const needle = state.search.local.trim().toLowerCase();
  if (!needle) return result.shops || [];
  return (result.shops || []).filter((shop) => {
    const hay = [shop.name, shop.phone, shop.address, shop.area, shop.city].join(" ").toLowerCase();
    return hay.includes(needle);
  });
}

function paintSearchResults() {
  const host = document.getElementById("search-results");
  if (!host) return;
  setSearchBusy(state.search.loading);
  if (state.search.loading) {
    host.innerHTML = `<div class="card"><p>Asking OpenStreetMap for shops. The first search can take up to a minute. Repeats of the same city are instant for 24 hours.</p><div class="skeleton"><i></i><i></i><i></i></div></div>`;
    return;
  }
  if (state.search.error) {
    host.innerHTML = `<div class="banner" role="alert">${esc(state.search.error)}</div>`;
    return;
  }
  const result = state.search.result;
  if (!result) {
    host.innerHTML = `<div class="card empty">${gemSvg()}<h2>Search a city</h2><p class="muted">Try Rawalpindi with the Jewellers chip.</p></div>`;
    return;
  }
  const shops = visibleShops();
  const fresh = (result.shops || []).filter((shop) => !shop.lead_id).length;
  const banner = result.used_bbox
    ? `<p class="note">This place has no polygon in OpenStreetMap, so the search used a bounding box. Results near the edge of the city may be included.</p>`
    : "";
  const cards = shops.length
    ? `<div class="cards">${shops.map((shop) => shopCard(shop)).join("")}</div>`
    : `<div class="card empty"><h2>No shops in this view</h2><p class="muted">Try clearing the area or the on-page filter. Coverage in OpenStreetMap varies by neighbourhood.</p></div>`;
  host.innerHTML = `
    <div class="stack">
      ${banner}
      <div class="spread">
        <div>
          <h2 title="${esc(result.place.label)}">${esc(shortPlace(result.place.label))}</h2>
          <p class="tiny">${result.count} shops${result.cached ? " · loaded from cache" : ""}${result.truncated ? " · list capped at 400" : ""}${result.took_ms != null ? " · " + result.took_ms + " ms" : ""}</p>
          ${enrichNote()}
        </div>
        <div class="row">
          <input class="field" id="result-filter" placeholder="Filter these results" value="${esc(state.search.local)}" style="max-width:220px">
          <button type="button" class="btn btn-primary" data-action="save-all" ${fresh ? "" : "disabled"}>Save all new (${fresh})</button>
        </div>
      </div>
      ${cards}
    </div>`;
}

function shopCard(shop) {
  const index = (state.search.result.shops || []).indexOf(shop);
  const saved = shop.lead_id
    ? `<span class="pill">In My Leads · ${esc(shop.lead_status || "Saved")}</span>`
    : `<span class="pill">New</span>`;
  const phone = shop.phone
    ? esc(shop.phone)
    : (shop.listing_checked
      ? "<span class='muted'>No public phone found</span>"
      : "<span class='muted'>Looking up the Google listing…</span>");
  const map = mapUrl(shop);
  const tel = telUrl(shop);
  const extra = [
    shop.rating ? `${esc(shop.rating)}★` : "",
    shop.opening_hours ? esc(shop.opening_hours) : "",
  ].filter(Boolean).join(" · ");
  const site = /^https?:\/\//i.test(String(shop.website || ""))
    ? `<a class="btn btn-secondary btn-sm" href="${esc(shop.website)}" target="_blank" rel="noopener noreferrer">Website</a>`
    : "";
  return `
    <article class="card shop-card">
      <div class="spread">
        <h3>${esc(shop.name || "Jewellery shop")}</h3>
        ${saved}
      </div>
      <p class="addr">${esc(shop.address || "No address on the map")}${shop.area ? " · " + esc(shop.area) : ""}</p>
      <p>${phone}${shop.whatsapp ? " · <span class='tiny'>WhatsApp</span>" : ""}</p>
      ${extra ? `<p class="tiny">${extra}</p>` : ""}
      <div class="row">
        <button type="button" class="btn btn-primary btn-sm" data-action="save-one" data-index="${index}" ${shop.lead_id ? "disabled" : ""}>${shop.lead_id ? "Saved" : "Save to My Leads"}</button>
        ${tel ? `<a class="btn btn-secondary btn-sm" href="${esc(tel)}">Call</a>` : ""}
        ${site ? site : ""}
        ${map ? `<a class="btn btn-secondary btn-sm" href="${esc(map)}" target="_blank" rel="noopener noreferrer" title="Opens this shop by name on Google Maps.">Google listing</a>` : ""}
      </div>
    </article>`;
}

async function runSearch() {
  if (state.search.loading) return;
  const city = document.getElementById("search-city").value.trim();
  state.search.city = city;
  state.search.area = document.getElementById("search-area").value.trim();
  state.search.keyword = document.getElementById("search-keyword").value.trim();
  state.search.refresh = document.getElementById("search-refresh").checked;
  enrichToken += 1;
  const token = enrichToken;
  if (city.length < 2) {
    toast("Enter a city name.", "error");
    return;
  }
  state.search.loading = true;
  state.search.error = "";
  paintSearchResults();
  try {
    const data = await api("/api/search", {
      method: "POST",
      timeout: 150000,
      silent: true,
      body: {
        city,
        keyword: state.search.keyword,
        chips: state.search.chips,
        area: state.search.area,
        refresh: state.search.refresh,
      },
    });
    state.search.result = data;
    state.search.refresh = false;
    const box = document.getElementById("search-refresh");
    if (box) box.checked = false;
  } catch (error) {
    state.search.error = error.message;
  } finally {
    state.search.loading = false;
    paintSearchResults();
    if (!state.search.error && state.search.result) enrichListings(token);
  }
}

function enrichNote() {
  const progress = state.search.enrich;
  if (!progress) return "";
  if (progress.error) return `<p class="tiny">${esc(progress.error)}</p>`;
  if (progress.done >= progress.total) {
    return `<p class="tiny">Listing lookup finished. ${progress.phones} phone number${progress.phones === 1 ? "" : "s"} filled from public Google listings.</p>`;
  }
  return `<p class="tiny">Reading public Google listings for phone numbers… ${progress.done} of ${progress.total}</p>`;
}

async function enrichListings(token) {
  const result = state.search.result;
  const shops = result && result.shops ? result.shops : [];
  const jobs = [];
  shops.forEach((shop, index) => {
    if (!shop.listing_checked && (isListedName(shop.name) || hasPoint(shop))) jobs.push(index);
    else shop.listing_checked = true;
  });
  state.search.enrich = { done: 0, total: jobs.length, phones: 0, error: "" };
  if (state.view === "search") paintSearchResults();
  for (let start = 0; start < jobs.length; start += 4) {
    if (token !== enrichToken || state.view !== "search") return;
    const slice = jobs.slice(start, start + 4);
    let data;
    try {
      data = await api("/api/listings", {
        method: "POST",
        timeout: 120000,
        silent: true,
        body: { shops: slice.map((index) => listingPayload(shops[index])) },
      });
    } catch (error) {
      state.search.enrich.error = error.message;
      if (state.view === "search") paintSearchResults();
      return;
    }
    (data.results || []).forEach((row, offset) => {
      const shop = shops[slice[offset]];
      if (!shop) return;
      const updates = row.updates || {};
      const gainedPhone = !shop.phone && updates.phone;
      Object.keys(updates).forEach((key) => {
        if (updates[key] != null && updates[key] !== "") shop[key] = updates[key];
      });
      shop.listing_checked = true;
      if (gainedPhone) state.search.enrich.phones += 1;
      if (shop.lead_id && Object.keys(updates).length) {
        api("/api/leads/" + shop.lead_id, { method: "PATCH", body: updates, silent: true }).catch(() => {});
      }
    });
    state.search.enrich.done = Math.min(jobs.length, start + slice.length);
    if (data.error) {
      state.search.enrich.error = data.error;
      if (state.view === "search") paintSearchResults();
      return;
    }
    if (state.view === "search" && token === enrichToken) paintSearchResults();
  }
}

function hasPoint(shop) {
  return shop.lat != null && shop.lng != null && shop.lat !== "" && shop.lng !== "";
}

function isListedName(name) {
  return !/^(jewellery shop|jewelry shop|shop|unnamed shop)$/i.test(String(name || "").trim());
}

function listingPayload(shop) {
  return {
    name: shop.name || "",
    phone: shop.phone || "",
    whatsapp: shop.whatsapp || "",
    website: shop.website || "",
    address: shop.address || "",
    city: shop.city || "",
    area: shop.area || "",
    opening_hours: shop.opening_hours || "",
    lat: shop.lat,
    lng: shop.lng,
    rating: shop.rating == null || shop.rating === "" ? null : shop.rating,
  };
}

function shopPayload(shop) {
  return {
    name: shop.name || "",
    phone: shop.phone || "",
    whatsapp: shop.whatsapp || "",
    email: shop.email || "",
    website: shop.website || "",
    facebook: shop.facebook || "",
    instagram: shop.instagram || "",
    address: shop.address || "",
    city: shop.city || "",
    area: shop.area || "",
    lat: shop.lat,
    lng: shop.lng,
    opening_hours: shop.opening_hours || "",
    listing_url: shop.listing_url || "",
    osm_type: shop.osm_type || "",
    osm_id: String(shop.osm_id || ""),
    source: "OpenStreetMap",
    rating: shop.rating == null || shop.rating === "" ? null : shop.rating,
  };
}

async function saveShops(indexes) {
  const shops = state.search.result ? state.search.result.shops : [];
  const picked = indexes.map((index) => shops[index]).filter((shop) => shop && !shop.lead_id);
  if (!picked.length) {
    toast("Those shops are already in My Leads.");
    return;
  }
  let response;
  try {
    response = await api("/api/leads/save", {
      method: "POST",
      body: { shops: picked.map(shopPayload) },
    });
  } catch (_error) {
    return;
  }
  response.items.forEach((item) => {
    const shop = picked[item.index];
    if (!shop) return;
    shop.lead_id = item.id;
    shop.lead_status = item.status;
  });
  const created = response.created || 0;
  const merged = response.merged || 0;
  toast(`Saved ${created} new, updated ${merged}, left ${response.unchanged || 0} unchanged.`);
  paintSearchResults();
}

function facebookSearchUrl() {
  const labels = { jewellers: "jewellers", gold: "gold", sarafa: "sarafa", goldsmith: "goldsmith", ornaments: "ornaments" };
  const bits = state.search.chips.map((id) => labels[id]).filter(Boolean);
  if (state.search.keyword.trim()) bits.push(state.search.keyword.trim());
  if (!bits.length) bits.push("jewellers");
  const query = bits.concat([state.search.city.trim()]).filter(Boolean).join(" ");
  return "https://www.facebook.com/search/pages/?q=" + encodeURIComponent(query);
}

function leadQuery() {
  const params = new URLSearchParams();
  const filters = state.filters;
  ["q", "city", "status", "source", "has_phone", "has_whatsapp", "sort", "direction"].forEach((key) => {
    if (filters[key]) params.set(key, filters[key]);
  });
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  return params.toString();
}

function renderLeads(main) {
  main.innerHTML = `
    <div class="stack">
      <div class="spread">
        <div>
          <h1>My Leads</h1>
          <p class="muted">Search, update status, and export the rows you are looking at.</p>
        </div>
        <div class="row">
          <button type="button" class="btn btn-secondary" data-action="export" data-kind="csv">Download CSV</button>
          <button type="button" class="btn btn-secondary" data-action="export" data-kind="xlsx">Download Excel</button>
          <button type="button" class="btn btn-primary" data-action="add-lead">Add lead</button>
        </div>
      </div>
      <div class="card stack">
        <div class="form-grid two">
          <label>Search
            <input class="field" id="lead-q" placeholder="Name, phone, address, notes" value="${esc(state.filters.q)}">
          </label>
          <label>City
            <select class="select" id="filter-city"></select>
          </label>
        </div>
        <div class="form-grid two">
          <label>Status
            <select class="select" id="filter-status">
              <option value="">Any status</option>
              ${STATUSES.map((status) => `<option value="${esc(status)}"${state.filters.status === status ? " selected" : ""}>${esc(status)}</option>`).join("")}
            </select>
          </label>
          <label>Source
            <select class="select" id="filter-source">
              <option value="">Any source</option>
              ${SOURCES.map((source) => `<option value="${esc(source)}"${state.filters.source === source ? " selected" : ""}>${esc(source)}</option>`).join("")}
            </select>
          </label>
          <label>Phone
            <select class="select" id="filter-phone">
              ${["", "yes", "no"].map((value) => `<option value="${value}"${state.filters.has_phone === value ? " selected" : ""}>${value === "" ? "Any" : value === "yes" ? "Has phone" : "No phone"}</option>`).join("")}
            </select>
          </label>
          <label>WhatsApp
            <select class="select" id="filter-wa">
              ${["", "yes", "no"].map((value) => `<option value="${value}"${state.filters.has_whatsapp === value ? " selected" : ""}>${value === "" ? "Any" : value === "yes" ? "Has WhatsApp" : "No WhatsApp"}</option>`).join("")}
            </select>
          </label>
        </div>
      </div>
      <div id="lead-table-wrap"></div>
      <div class="spread" id="lead-meta"></div>
    </div>
    <div class="bulk" id="bulk-bar" hidden>
      <strong id="bulk-count">0 selected</strong>
      <select class="select" id="bulk-status">${STATUSES.map((status) => `<option>${esc(status)}</option>`).join("")}</select>
      <button type="button" class="btn btn-secondary btn-sm" data-action="bulk-status">Set status</button>
      <button type="button" class="btn btn-secondary btn-sm" data-action="export" data-kind="xlsx">Export selected</button>
      <button type="button" class="btn btn-danger btn-sm" data-action="bulk-delete">Delete</button>
      <button type="button" class="btn btn-ghost btn-sm" data-action="bulk-clear">Clear</button>
    </div>`;
  loadLeads();
}

async function loadLeads() {
  const token = renderToken;
  let data;
  try {
    data = await api("/api/leads?" + leadQuery());
  } catch (_error) {
    return;
  }
  if (token !== renderToken || state.view !== "leads") return;
  state.leads = data.items || [];
  state.total = data.total || 0;
  state.cities = data.cities || [];
  fillCitySelect();
  const wrap = document.getElementById("lead-table-wrap");
  const meta = document.getElementById("lead-meta");
  if (wrap) wrap.innerHTML = leadsTable();
  if (meta) meta.innerHTML = leadsMeta();
  updateBulkBar();
}

function fillCitySelect() {
  const select = document.getElementById("filter-city");
  if (!select) return;
  const current = state.filters.city;
  const cities = state.cities.slice();
  if (current && !cities.some((city) => city.toLowerCase() === current.toLowerCase())) cities.unshift(current);
  select.innerHTML = `<option value="">All cities</option>` + cities.map((city) => {
    const selected = city.toLowerCase() === current.toLowerCase() ? " selected" : "";
    return `<option value="${esc(city)}"${selected}>${esc(city)}</option>`;
  }).join("");
}

function sortMark(column) {
  if (state.filters.sort !== column) return "";
  return state.filters.direction === "asc" ? " ↑" : " ↓";
}

function leadsTable() {
  if (!state.leads.length) {
    const filtered = Object.entries(state.filters).some(([key, value]) => !["sort", "direction"].includes(key) && value);
    return `<div class="card empty">${gemSvg()}<h2>${filtered ? "No leads match" : "No leads yet"}</h2><p class="muted">${filtered ? "Clear a filter or try another word." : "Search OpenStreetMap, paste a Facebook page, or import a spreadsheet."}</p><div class="row" style="justify-content:center"><button type="button" class="btn btn-primary" data-nav="search">Find shops</button><button type="button" class="btn btn-secondary" data-action="add-lead">Add lead</button></div></div>`;
  }
  const headers = [
    ["name", "Name"],
    ["phone", "Phone"],
    ["city", "City"],
    ["status", "Status"],
    ["follow_up_date", "Follow-up"],
    ["notes", "Notes"],
  ];
  const allChecked = state.leads.every((lead) => state.selected.has(lead.id));
  const body = state.leads.map((lead) => {
    const map = mapUrl(lead);
    const tel = telUrl(lead);
    const wa = waDigits(lead);
    return `<tr>
      <td><input type="checkbox" data-action="select-row" data-id="${lead.id}" ${state.selected.has(lead.id) ? "checked" : ""} aria-label="Select ${esc(lead.name)}"></td>
      <td>
        <button type="button" class="btn btn-ghost" data-action="open-lead" data-id="${lead.id}" style="padding-left:0"><strong>${esc(lead.name)}</strong></button>
        <div><span class="badge badge-${esc(lead.source)}">${esc(lead.source)}</span></div>
      </td>
      <td>${esc(lead.phone || "—")}<div class="tiny">${esc(lead.phone_normalized || "")}</div></td>
      <td>${esc(lead.city || "—")}<div class="tiny">${esc(lead.area || "")}</div></td>
      <td><select class="status-select ${statusClass(lead.status)}" data-action="status" data-id="${lead.id}" aria-label="Status for ${esc(lead.name)}">${STATUSES.map((status) => `<option value="${esc(status)}"${status === lead.status ? " selected" : ""}>${esc(status)}</option>`).join("")}</select></td>
      <td><input class="field" type="date" data-action="followup" data-id="${lead.id}" value="${esc(lead.follow_up_date || "")}" aria-label="Follow-up for ${esc(lead.name)}"></td>
      <td><input class="field" data-action="notes" data-id="${lead.id}" data-original="${esc(lead.notes || "")}" value="${esc(lead.notes || "")}" placeholder="Add a note" aria-label="Notes for ${esc(lead.name)}"></td>
      <td class="row">
        <button type="button" class="btn btn-wa btn-sm" data-action="wa" data-id="${lead.id}" ${wa ? "" : "disabled"}>WhatsApp</button>
        ${tel ? `<a class="btn btn-secondary btn-sm" href="${esc(tel)}">Call</a>` : ""}
        ${map ? `<a class="btn btn-secondary btn-sm" href="${esc(map)}" target="_blank" rel="noopener noreferrer" title="Opens this shop by name on Google Maps. The listing usually shows the phone number.">Google listing</a>` : ""}
      </td>
    </tr>`;
  }).join("");
  return `<div class="table-scroll"><table class="data">
    <thead><tr>
      <th><input type="checkbox" id="select-all" ${allChecked ? "checked" : ""} aria-label="Select all on this page"></th>
      ${headers.map(([key, label]) => `<th><button type="button" class="sort-btn" data-action="sort" data-sort="${key}">${label}${sortMark(key)}</button></th>`).join("")}
      <th>Actions</th>
    </tr></thead>
    <tbody>${body}</tbody>
  </table></div>`;
}

function leadsMeta() {
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  const start = state.total ? (state.page - 1) * state.pageSize + 1 : 0;
  const end = Math.min(state.total, state.page * state.pageSize);
  return `
    <p class="tiny">Showing ${start}–${end} of ${state.total}</p>
    <div class="row">
      <button type="button" class="btn btn-secondary btn-sm" data-action="page" data-dir="-1" ${state.page <= 1 ? "disabled" : ""}>Previous</button>
      <span class="tiny">Page ${state.page} of ${pages}</span>
      <button type="button" class="btn btn-secondary btn-sm" data-action="page" data-dir="1" ${state.page >= pages ? "disabled" : ""}>Next</button>
    </div>`;
}

function updateBulkBar() {
  const bar = document.getElementById("bulk-bar");
  if (!bar) return;
  bar.hidden = state.selected.size === 0;
  const label = document.getElementById("bulk-count");
  if (label) label.textContent = state.selected.size + " selected";
}

function exportHref(kind) {
  const params = new URLSearchParams();
  if (state.selected.size) params.set("ids", Array.from(state.selected).join(","));
  else {
    ["q", "city", "status", "source", "has_phone", "has_whatsapp"].forEach((key) => {
      if (state.filters[key]) params.set(key, state.filters[key]);
    });
  }
  params.set("sort", state.filters.sort);
  params.set("direction", state.filters.direction);
  return `/api/export/${kind}?` + params.toString();
}

function download(url) {
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function findLead(id) {
  const numeric = Number(id);
  return state.leads.find((lead) => lead.id === numeric)
    || (state.dashboard && (state.dashboard.followups || []).find((lead) => lead.id === numeric))
    || (state.drawer && state.drawer.lead && state.drawer.lead.id === numeric ? state.drawer.lead : null);
}

function mapUrl(lead) {
  // After a listing lookup, open that exact business card. A name search
  // can land on a different shop and hide the phone number.
  if (lead.listing_url) return lead.listing_url;
  // A lat,lng query opens a pin and hides the business card, so the phone
  // number on Google never appears. Search by shop name and only use the
  // coordinates to center the map. An unnamed pin must not search the city.
  const name = String(lead.name || "").trim();
  const usableName = /^(jewellery shop|jewelry shop|shop)$/i.test(name) ? "" : name;
  const hasPoint = lead.lat != null && lead.lng != null && lead.lat !== "" && lead.lng !== "";
  if (!usableName && hasPoint) {
    return `https://www.google.com/maps/search/jewellery/@${lead.lat},${lead.lng},19z`;
  }
  const city = String(lead.city || "").trim();
  const parts = [];
  if (usableName) parts.push(usableName);
  if (city && !parts.join(", ").toLowerCase().includes(city.toLowerCase())) parts.push(city);
  const query = parts.join(", ");
  if (query && hasPoint) {
    return `https://www.google.com/maps/search/${encodeURIComponent(query)}/@${lead.lat},${lead.lng},18z`;
  }
  if (query) return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(query);
  if (hasPoint) return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(String(lead.lat) + "," + String(lead.lng))}`;
  return "";
}

function telUrl(lead) {
  const raw = String(lead.phone_normalized || lead.phone || "").split(";")[0];
  const cleaned = raw.replace(/[^\d+]/g, "");
  return cleaned ? "tel:" + cleaned : "";
}

function waDigits(lead) {
  return String(lead.whatsapp || "").replace(/\D/g, "");
}

function fillTemplate(template, lead) {
  const myName = (state.settings && state.settings.my_name.trim()) || "JewelLeads";
  return String(template || "")
    .replace(/\{shop_name\}/g, lead.name || "there")
    .replace(/\{my_name\}/g, myName);
}

async function patchLead(id, body, options) {
  const opts = options || {};
  const updated = await api("/api/leads/" + id, { method: "PATCH", body, silent: opts.silent });
  const index = state.leads.findIndex((lead) => lead.id === Number(id));
  if (index >= 0) state.leads[index] = updated;
  if (state.drawer && state.drawer.lead && state.drawer.lead.id === Number(id)) state.drawer.lead = updated;
  if (!opts.quiet) toast("Saved");
  return updated;
}

async function openWhatsApp(id) {
  let lead = findLead(id);
  if (!lead) {
    try {
      lead = await api("/api/leads/" + id);
    } catch (_error) {
      return;
    }
  }
  const digits = waDigits(lead);
  if (!digits) {
    toast("This lead has no mobile number for WhatsApp.", "error");
    return;
  }
  const url = "https://wa.me/" + digits + "?text=" + encodeURIComponent(fillTemplate(state.settings.whatsapp_template, lead));
  const canMark = lead.status === "New";
  const choice = await confirmDialog({
    title: "Open WhatsApp?",
    body: canMark
      ? "Open the chat and mark " + lead.name + " as Contacted?"
      : "Open a chat with " + lead.name + ". Status stays " + lead.status + ".",
    confirmLabel: canMark ? "Open and mark Contacted" : "Open WhatsApp",
    extraLabel: canMark ? "Open only" : "",
    cancelLabel: "Cancel",
  });
  if (choice === "cancel") return;
  window.open(url, "_blank", "noopener,noreferrer");
  if (choice === "confirm" && canMark) {
    try {
      await patchLead(id, { status: "Contacted" }, { quiet: true });
      toast("Marked as Contacted");
      if (state.view === "leads") loadLeads();
      if (state.view === "dashboard") renderDashboard(document.getElementById("main"));
    } catch (_error) {
      /* api already toasted */
    }
  }
}

function blankLead() {
  return {
    name: "",
    phone: "",
    whatsapp: "",
    email: "",
    website: "",
    facebook: "",
    instagram: "",
    address: "",
    city: state.search.city || state.filters.city || "",
    area: "",
    lat: "",
    lng: "",
    rating: "",
    source: "Manual",
    status: "New",
    notes: "",
    follow_up_date: "",
    opening_hours: "",
  };
}

function openDrawer(mode, lead) {
  lastFocus = document.activeElement;
  state.drawer = { mode, lead: Object.assign({}, lead) };
  paintDrawer();
  const first = document.querySelector("#drawer-root input, #drawer-root textarea");
  if (first) first.focus();
}

function closeDrawer() {
  if (!state.drawer) return;
  state.drawer = null;
  const root = document.getElementById("drawer-root");
  if (root) root.innerHTML = "";
  if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
}

function paintDrawer() {
  const root = document.getElementById("drawer-root");
  if (!state.drawer) {
    root.innerHTML = "";
    return;
  }
  const lead = state.drawer.lead;
  const creating = state.drawer.mode === "create";
  const sources = creating ? ["Manual", "Facebook"] : SOURCES;
  const field = (id, label, value, type) => `
    <label>${label}
      <input class="field" id="${id}" type="${type || "text"}" value="${esc(value ?? "")}">
    </label>`;
  root.innerHTML = `
    <div class="drawer-backdrop">
      <form class="drawer stack" id="drawer-form" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <div class="spread">
          <h2 id="drawer-title">${creating ? "Add a lead" : "Edit lead"}</h2>
          <button type="button" class="btn btn-ghost" data-action="drawer-close">Close</button>
        </div>
        ${creating ? `<label>Quick paste
            <textarea class="field" id="d-paste" placeholder="Paste a Facebook about section: name, phone, address, link"></textarea>
          </label>
          <button type="button" class="btn btn-secondary" data-action="paste-fill">Fill the form from this text</button>` : ""}
        <div class="form-grid two">
          ${field("d-name", "Shop name", lead.name)}
          ${field("d-phone", "Phone", lead.phone)}
          ${field("d-whatsapp", "WhatsApp", lead.whatsapp)}
          ${field("d-email", "Email", lead.email, "email")}
          ${field("d-website", "Website", lead.website, "url")}
          ${field("d-facebook", "Facebook page", lead.facebook, "url")}
          ${field("d-instagram", "Instagram", lead.instagram, "url")}
          ${field("d-city", "City", lead.city)}
          ${field("d-area", "Area", lead.area)}
          <label>Status
            <select class="select" id="d-status">${STATUSES.map((status) => `<option ${status === (lead.status || "New") ? "selected" : ""}>${esc(status)}</option>`).join("")}</select>
          </label>
          <label>Source
            <select class="select" id="d-source">${sources.map((source) => `<option ${source === (lead.source || "Manual") ? "selected" : ""}>${esc(source)}</option>`).join("")}</select>
          </label>
          ${field("d-follow", "Follow-up", lead.follow_up_date || "", "date")}
          ${field("d-rating", "Rating (0–5)", lead.rating ?? "", "number")}
          ${field("d-lat", "Latitude", lead.lat ?? "", "number")}
          ${field("d-lng", "Longitude", lead.lng ?? "", "number")}
        </div>
        <label>Address
          <textarea class="field" id="d-address">${esc(lead.address || "")}</textarea>
        </label>
        <label>Opening hours
          <input class="field" id="d-hours" value="${esc(lead.opening_hours || "")}">
        </label>
        <label>Notes
          <textarea class="field" id="d-notes">${esc(lead.notes || "")}</textarea>
        </label>
        <div class="row" style="justify-content:flex-end">
          ${creating ? "" : `<button type="button" class="btn btn-danger" data-action="drawer-delete" data-id="${lead.id}">Delete</button>`}
          <button type="submit" class="btn btn-primary">${creating ? "Add lead" : "Save changes"}</button>
        </div>
      </form>
    </div>`;
  const rating = document.getElementById("d-rating");
  if (rating) {
    rating.min = "0";
    rating.max = "5";
    rating.step = "0.1";
  }
  ["d-lat", "d-lng"].forEach((id) => {
    const input = document.getElementById(id);
    if (input) input.step = "any";
  });
}

function readDrawer() {
  const text = (id) => {
    const element = document.getElementById(id);
    return element ? element.value.trim() : "";
  };
  const num = (id) => {
    const raw = text(id);
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  };
  return {
    name: text("d-name"),
    phone: text("d-phone"),
    whatsapp: text("d-whatsapp"),
    email: text("d-email"),
    website: text("d-website"),
    facebook: text("d-facebook"),
    instagram: text("d-instagram"),
    address: text("d-address"),
    city: text("d-city"),
    area: text("d-area"),
    status: text("d-status"),
    source: text("d-source"),
    follow_up_date: text("d-follow"),
    notes: text("d-notes"),
    opening_hours: text("d-hours"),
    rating: num("d-rating"),
    lat: num("d-lat"),
    lng: num("d-lng"),
  };
}

async function saveDrawer() {
  const payload = readDrawer();
  if (!payload.name && !payload.phone && !payload.whatsapp) {
    toast("Enter a shop name or a phone number.", "error");
    return;
  }
  try {
    if (state.drawer.mode === "create") {
      const created = await api("/api/leads", { method: "POST", body: payload });
      const verb = created.action === "merged" ? "Merged into an existing lead" : "Lead added";
      toast(verb);
    } else {
      await patchLead(state.drawer.lead.id, payload, { quiet: true });
      toast("Lead updated");
    }
  } catch (_error) {
    return;
  }
  closeDrawer();
  if (state.view === "leads") loadLeads();
  if (state.view === "dashboard") renderDashboard(document.getElementById("main"));
}

async function fillFromPaste() {
  const text = document.getElementById("d-paste").value;
  if (!text.trim()) {
    toast("Paste some text first.", "error");
    return;
  }
  let parsed;
  try {
    parsed = await api("/api/parse-text", { method: "POST", body: { text } });
  } catch (_error) {
    return;
  }
  const pairs = {
    name: "d-name",
    phone: "d-phone",
    whatsapp: "d-whatsapp",
    email: "d-email",
    website: "d-website",
    facebook: "d-facebook",
    instagram: "d-instagram",
    address: "d-address",
    notes: "d-notes",
  };
  Object.entries(pairs).forEach(([key, id]) => {
    if (!parsed[key]) return;
    const element = document.getElementById(id);
    if (element) element.value = parsed[key];
  });
  if (parsed.source) {
    const source = document.getElementById("d-source");
    if (source) source.value = parsed.source;
  }
  toast("Form filled from the pasted text.");
}

function renderImport(main) {
  const preview = state.importPreview;
  let body = `
    <div class="card stack" id="drop-zone">
      <h2>Upload a file</h2>
      <p class="muted">CSV or Excel (.xlsx) from an association list or directory. The file stays on this computer.</p>
      <input class="field" id="import-file" type="file" accept=".csv,.xlsx,.xlsm,text/csv">
    </div>`;
  if (preview) {
    const fields = ["name", "phone", "whatsapp", "email", "website", "facebook", "instagram", "address", "city", "area", "notes", "status", "source", "follow_up_date", "rating"];
    const headers = preview.headers || [];
    const selects = fields.map((field) => {
      const suggested = (preview.suggested || {})[field] || "";
      const options = [`<option value="">Ignore</option>`].concat(headers.map((header) => {
        const selected = header === suggested ? " selected" : "";
        return `<option value="${esc(header)}"${selected}>${esc(header)}</option>`;
      }));
      return `<label>${esc(field.split("_").join(" "))}<select class="select" data-map="${esc(field)}">${options.join("")}</select></label>`;
    }).join("");
    const sampleHeaders = headers.map((header) => `<th>${esc(header)}</th>`).join("");
    const sampleRows = (preview.sample || []).map((row) => {
      const cells = headers.map((header) => `<td>${esc(row[header] || "")}</td>`).join("");
      return `<tr>${cells}</tr>`;
    }).join("");
    body += `
      <form id="import-form" class="card stack">
        <div class="spread">
          <h2>${esc(preview.filename)}</h2>
          <span class="tiny">${preview.row_count} rows</span>
        </div>
        <div class="form-grid two">${selects}</div>
        <label>Source for imported rows
          <select class="select" id="import-source">
            ${SOURCES.map((source) => `<option ${source === "Import" ? "selected" : ""}>${esc(source)}</option>`).join("")}
          </select>
        </label>
        <div class="table-scroll"><table class="data"><thead><tr>${sampleHeaders}</tr></thead><tbody>${sampleRows}</tbody></table></div>
        <div class="row" style="justify-content:flex-end"><button type="submit" class="btn btn-primary">Import ${preview.row_count} rows</button></div>
      </form>`;
  }
  if (state.importResult) {
    const result = state.importResult;
    body += `<div class="note"><strong>Import finished.</strong> ${result.created} new, ${result.merged} merged, ${result.unchanged} unchanged, ${result.skipped} skipped.</div>`;
  }
  main.innerHTML = `
    <div class="stack">
      <div>
        <h1>Import</h1>
        <p class="muted lead">Bring in leads you already have. Columns can be named however you like — match them below.</p>
      </div>
      ${body}
    </div>`;
}

async function previewFile(file) {
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  let data;
  try {
    data = await api("/api/import/preview", { method: "POST", body });
  } catch (_error) {
    return;
  }
  state.importPreview = data;
  state.importResult = null;
  if (state.view === "import") renderImport(document.getElementById("main"));
}

async function commitImport() {
  if (!state.importPreview) return;
  const mapping = {};
  document.querySelectorAll("[data-map]").forEach((select) => {
    if (select.value) mapping[select.dataset.map] = select.value;
  });
  const source = document.getElementById("import-source").value || "Import";
  let result;
  try {
    result = await api("/api/import/commit", {
      method: "POST",
      body: { token: state.importPreview.token, mapping, source },
    });
  } catch (_error) {
    return;
  }
  state.importResult = result;
  state.importPreview = null;
  toast("Import finished");
  renderImport(document.getElementById("main"));
}

async function renderSettings(main) {
  const token = renderToken;
  if (!state.settings) {
    try {
      state.settings = await api("/api/settings");
    } catch (_error) {
      return;
    }
  }
  if (token !== renderToken || state.view !== "settings") return;
  const settings = state.settings;
  main.innerHTML = `
    <form id="settings-form" class="stack" style="max-width:720px">
      <div>
        <h1>Settings</h1>
        <p class="muted lead">Used when cleaning phone numbers and opening WhatsApp. Stored only in the local database.</p>
      </div>
      <div class="card stack">
        <label>Your name
          <input class="field" id="set-name" maxlength="80" value="${esc(settings.my_name || "")}" placeholder="How you introduce yourself">
        </label>
        <label>Country calling code
          <input class="field" id="set-cc" inputmode="numeric" value="${esc(settings.country_code || "92")}" placeholder="92">
          <p class="hint tiny">Pakistan is 92. Numbers like 0300… are stored as +92…</p>
        </label>
        <label>WhatsApp message
          <textarea class="field" id="set-template" maxlength="1000">${esc(settings.whatsapp_template || "")}</textarea>
          <p class="hint tiny">Placeholders: {shop_name} and {my_name}.</p>
        </label>
        <div class="note" id="template-preview"></div>
        <div class="row" style="justify-content:flex-end"><button type="submit" class="btn btn-primary">Save settings</button></div>
      </div>
    </form>`;
  paintTemplatePreview();
}

function paintTemplatePreview() {
  const box = document.getElementById("template-preview");
  const template = document.getElementById("set-template");
  const name = document.getElementById("set-name");
  if (!box || !template) return;
  const myName = (name && name.value.trim()) || "JewelLeads";
  const text = template.value.replace(/\{shop_name\}/g, "Al-Rahim Jewellers").replace(/\{my_name\}/g, myName);
  box.textContent = "Preview: " + text;
}

async function saveSettings() {
  const body = {
    country_code: document.getElementById("set-cc").value,
    my_name: document.getElementById("set-name").value,
    whatsapp_template: document.getElementById("set-template").value,
  };
  try {
    state.settings = await api("/api/settings", { method: "PUT", body });
  } catch (_error) {
    return;
  }
  toast("Settings saved");
  paintTemplatePreview();
}

function onClick(event) {
  const choice = event.target.closest("[data-choice]");
  if (choice && state.modalResolve) {
    state.modalResolve(choice.dataset.choice);
    return;
  }
  const nav = event.target.closest("[data-nav]");
  if (nav && nav.dataset.nav) {
    showView(nav.dataset.nav);
    return;
  }
  const actionEl = event.target.closest("[data-action]");
  if (!actionEl) {
    if (event.target.classList && event.target.classList.contains("drawer-backdrop")) closeDrawer();
    if (event.target.id === "backdrop") closeSidebar();
    return;
  }
  const action = actionEl.dataset.action;
  if (action === "menu") {
    const sidebar = document.getElementById("sidebar");
    if (sidebar.classList.contains("open")) closeSidebar();
    else openSidebar();
    return;
  }
  if (action === "theme") {
    setTheme(!document.documentElement.classList.contains("dark"));
    return;
  }
  if (action === "logout") {
    event.preventDefault();
    fetch("/logout", { method: "POST", credentials: "same-origin" }).finally(() => {
      window.location.href = "/login";
    });
    return;
  }
  if (action === "drawer-close") {
    event.preventDefault();
    closeDrawer();
    return;
  }
  if (action === "chip") {
    const id = actionEl.dataset.chip;
    const chips = new Set(state.search.chips);
    if (chips.has(id)) chips.delete(id);
    else chips.add(id);
    state.search.chips = Array.from(chips);
    actionEl.classList.toggle("active", chips.has(id));
    actionEl.setAttribute("aria-pressed", chips.has(id) ? "true" : "false");
    return;
  }
  if (action === "facebook-search") {
    const city = (document.getElementById("search-city") || {}).value || state.search.city;
    state.search.city = (city || "").trim();
    if (!state.search.city) {
      toast("Enter a city first.", "error");
      return;
    }
    window.open(facebookSearchUrl(), "_blank", "noopener,noreferrer");
    return;
  }
  if (action === "add-lead") {
    openDrawer("create", blankLead());
    return;
  }
  if (action === "save-one") {
    saveShops([Number(actionEl.dataset.index)]);
    return;
  }
  if (action === "save-all") {
    const indexes = (state.search.result.shops || []).map((shop, index) => shop.lead_id ? -1 : index).filter((index) => index >= 0);
    saveShops(indexes);
    return;
  }
  if (action === "open-lead") {
    const lead = findLead(actionEl.dataset.id);
    if (lead) openDrawer("edit", lead);
    else api("/api/leads/" + actionEl.dataset.id).then((fresh) => openDrawer("edit", fresh)).catch(() => {});
    return;
  }
  if (action === "wa") {
    openWhatsApp(actionEl.dataset.id);
    return;
  }
  if (action === "goto-status") {
    const status = actionEl.dataset.status;
    state.filters.status = status === "followups" ? "" : status;
    state.page = 1;
    if (status === "followups") state.filters.status = "";
    showView("leads");
    return;
  }
  if (action === "sort") {
    const column = actionEl.dataset.sort;
    if (state.filters.sort === column) {
      state.filters.direction = state.filters.direction === "asc" ? "desc" : "asc";
    } else {
      state.filters.sort = column;
      state.filters.direction = "asc";
    }
    loadLeads();
    return;
  }
  if (action === "page") {
    state.page += Number(actionEl.dataset.dir);
    if (state.page < 1) state.page = 1;
    loadLeads();
    return;
  }
  if (action === "export") {
    download(exportHref(actionEl.dataset.kind || "xlsx"));
    return;
  }
  if (action === "bulk-clear") {
    state.selected.clear();
    loadLeads();
    return;
  }
  if (action === "bulk-status") {
    const status = document.getElementById("bulk-status").value;
    api("/api/leads/bulk", {
      method: "POST",
      body: { ids: Array.from(state.selected), action: "status", status },
    }).then(() => {
      toast("Status updated");
      state.selected.clear();
      loadLeads();
    }).catch(() => {});
    return;
  }
  if (action === "bulk-delete") {
    confirmDialog({
      title: "Delete leads?",
      body: "Delete " + state.selected.size + " selected leads? This cannot be undone.",
      confirmLabel: "Delete",
    }).then((choice) => {
      if (choice !== "confirm") return;
      api("/api/leads/bulk", {
        method: "POST",
        body: { ids: Array.from(state.selected), action: "delete" },
      }).then(() => {
        toast("Leads deleted");
        state.selected.clear();
        loadLeads();
      }).catch(() => {});
    });
    return;
  }
  if (action === "paste-fill") {
    fillFromPaste();
    return;
  }
  if (action === "drawer-delete") {
    const id = Number(actionEl.dataset.id);
    confirmDialog({
      title: "Delete this lead?",
      body: "The lead will be removed from the local database.",
      confirmLabel: "Delete",
    }).then((choice) => {
      if (choice !== "confirm") return;
      api("/api/leads/" + id, { method: "DELETE" }).then(() => {
        toast("Lead deleted");
        state.selected.delete(id);
        closeDrawer();
        if (state.view === "leads") loadLeads();
        if (state.view === "dashboard") renderDashboard(document.getElementById("main"));
      }).catch(() => {});
    });
  }
}

function onChange(event) {
  const target = event.target;
  if (target.dataset.action === "status") {
    patchLead(target.dataset.id, { status: target.value }, { quiet: true })
      .then(() => {
        target.className = "status-select " + statusClass(target.value);
        toast("Status updated");
      })
      .catch(() => loadLeads());
    return;
  }
  if (target.dataset.action === "followup") {
    patchLead(target.dataset.id, { follow_up_date: target.value }, { quiet: true })
      .then(() => toast("Follow-up updated"))
      .catch(() => loadLeads());
    return;
  }
  if (target.dataset.action === "notes") {
    if (target.value === target.dataset.original) return;
    patchLead(target.dataset.id, { notes: target.value }, { quiet: true })
      .then(() => {
        target.dataset.original = target.value;
        toast("Note saved");
      })
      .catch(() => loadLeads());
    return;
  }
  if (target.dataset.action === "select-row") {
    const id = Number(target.dataset.id);
    if (target.checked) state.selected.add(id);
    else state.selected.delete(id);
    updateBulkBar();
    return;
  }
  if (target.id === "select-all") {
    state.leads.forEach((lead) => {
      if (target.checked) state.selected.add(lead.id);
      else state.selected.delete(lead.id);
    });
    document.querySelectorAll("[data-action=select-row]").forEach((box) => {
      box.checked = target.checked;
    });
    updateBulkBar();
    return;
  }
  if (target.id === "filter-city") {
    state.filters.city = target.value;
    state.page = 1;
    loadLeads();
    return;
  }
  if (target.id === "filter-status") {
    state.filters.status = target.value;
    state.page = 1;
    loadLeads();
    return;
  }
  if (target.id === "filter-source") {
    state.filters.source = target.value;
    state.page = 1;
    loadLeads();
    return;
  }
  if (target.id === "filter-phone") {
    state.filters.has_phone = target.value;
    state.page = 1;
    loadLeads();
    return;
  }
  if (target.id === "filter-wa") {
    state.filters.has_whatsapp = target.value;
    state.page = 1;
    loadLeads();
  }
}

function onInput(event) {
  const target = event.target;
  if (target.id === "lead-q") {
    state.filters.q = target.value;
    state.page = 1;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadLeads, 300);
    return;
  }
  if (target.id === "result-filter") {
    state.search.local = target.value;
    paintSearchResults();
    const box = document.getElementById("result-filter");
    if (box) {
      box.focus();
      const length = box.value.length;
      box.setSelectionRange(length, length);
    }
    return;
  }
  if (target.id === "search-city") state.search.city = target.value;
  if (target.id === "search-area") state.search.area = target.value;
  if (target.id === "search-keyword") state.search.keyword = target.value;
  if (target.id === "set-template" || target.id === "set-name") paintTemplatePreview();
}

function onSubmit(event) {
  if (event.target.id === "search-form") {
    event.preventDefault();
    runSearch();
  } else if (event.target.id === "settings-form") {
    event.preventDefault();
    saveSettings();
  } else if (event.target.id === "drawer-form") {
    event.preventDefault();
    saveDrawer();
  } else if (event.target.id === "import-form") {
    event.preventDefault();
    commitImport();
  }
}

function onKey(event) {
  if (event.key !== "Escape") return;
  if (state.modalResolve) {
    closeModal("cancel");
    return;
  }
  if (state.drawer) closeDrawer();
  else closeSidebar();
}

function boot() {
  syncThemeButton();
  document.body.addEventListener("click", onClick);
  document.body.addEventListener("change", onChange);
  document.body.addEventListener("submit", onSubmit);
  document.body.addEventListener("input", onInput);
  document.body.addEventListener("change", (event) => {
    if (event.target.id === "import-file") previewFile(event.target.files && event.target.files[0]);
  });
  document.addEventListener("keydown", onKey);
  window.addEventListener("hashchange", () => {
    const view = (location.hash || "#/dashboard").replace("#/", "");
    if (view !== state.view) showView(view);
  });
  const drop = document;
  drop.addEventListener("dragover", (event) => {
    if (state.view !== "import") return;
    event.preventDefault();
  });
  drop.addEventListener("drop", (event) => {
    if (state.view !== "import") return;
    event.preventDefault();
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) previewFile(file);
  });
  api("/api/settings", { silent: true }).then((settings) => {
    state.settings = settings;
  }).catch(() => {
    state.settings = { country_code: "92", my_name: "", whatsapp_template: "" };
  }).finally(() => {
    const initial = (location.hash || "#/dashboard").replace("#/", "");
    showView(VIEWS.includes(initial) ? initial : "dashboard");
  });
}

boot();
