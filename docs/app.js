/* Kerala Field Notes — data, filters, terrain field, live location images. */

const DATA_URL = "data/resorts.json";
const COMMONS_API = "https://commons.wikimedia.org/w/api.php";
// Set this to the deployed Worker origin (for example
// "https://kfn-images.<account>.workers.dev"). Empty means call Commons directly.
const WORKER_URL = "";
const IMAGE_LIMIT = 3;
const BUDGET_LABELS = { low: "Low", medium: "Medium", high: "High", premium: "Premium" };
const BUDGET_ORDER = { low: 0, medium: 1, high: 2, premium: 3 };
const SETTING_COLORS = {
  beach: "water",
  backwater: "water",
  lake: "water",
  river: "water",
  island: "water",
  hill: "green",
  mountain: "green",
  forest: "green",
  plantation: "green",
  cliff: "green",
  waterfall: "green",
  urban: "land",
  countryside: "land",
};

const state = {
  q: "",
  settings: new Set(),
  district: "",
  types: new Set(),
  budgets: new Set(),
  must: new Set(),
  minRating: 0,
  sort: "rank",
  limit: 12,
};

let DATA = null;
let FILTERED = [];
let field = null;
let lastFieldList = [];
let searchTimer = null;
let imageObserver = null;
let activeImages = 0;
const imageQueue = [];
const imageInflight = new Map();

const el = {};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  cacheElements();
  bindStaticEvents();
  try {
    const response = await fetch(DATA_URL, { cache: "no-cache" });
    if (!response.ok) throw new Error(`Dataset ${response.status}`);
    DATA = await response.json();
  } catch (error) {
    console.error(error);
    el.resultCount.textContent = "The shortlist could not load. Try a refresh.";
    return;
  }

  applyMeta();
  renderFilterOptions();
  readURL();
  syncControls();
  buildField();
  applyFilters({ animate: false });
}

function cacheElements() {
  el.header = document.querySelector("[data-header]");
  el.search = document.getElementById("search");
  el.sort = document.getElementById("sort");
  el.district = document.getElementById("district-filter");
  el.rating = document.getElementById("rating-filter");
  el.settingChips = document.getElementById("setting-chips");
  el.typeChips = document.getElementById("type-chips");
  el.budgetChips = document.getElementById("budget-chips");
  el.filters = document.getElementById("filters");
  el.filterToggle = document.getElementById("filter-toggle");
  el.filterCount = document.getElementById("filter-count");
  el.filterDone = document.getElementById("filter-done");
  el.clear = document.getElementById("clear-filters");
  el.results = document.getElementById("results");
  el.resultCount = document.getElementById("result-count");
  el.activeFilters = document.getElementById("active-filters");
  el.empty = document.getElementById("empty");
  el.loadMore = document.getElementById("load-more");
  el.resultsNote = document.getElementById("results-note");
  el.fieldSvg = document.getElementById("terrain-field");
  el.fieldTip = document.getElementById("field-tip");
  el.fieldCount = document.getElementById("field-count");
  el.mustInputs = Array.from(document.querySelectorAll("[data-must]"));
  el.heroCredit = document.querySelector("[data-hero-credit]");
}

function bindStaticEvents() {
  window.addEventListener("scroll", () => {
    el.header.classList.toggle("is-scrolled", window.scrollY > 28);
  }, { passive: true });

  el.search.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      state.q = el.search.value.trim();
      state.limit = 12;
      applyFilters();
    }, 160);
  });

  el.sort.addEventListener("change", () => {
    state.sort = el.sort.value;
    state.limit = 12;
    applyFilters({ keepFocus: true });
  });

  el.district.addEventListener("change", () => {
    state.district = el.district.value;
    state.limit = 12;
    applyFilters();
  });

  el.rating.addEventListener("change", () => {
    state.minRating = Number(el.rating.value) || 0;
    state.limit = 12;
    applyFilters();
  });

  el.mustInputs.forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) state.must.add(input.value);
      else state.must.delete(input.value);
      state.limit = 12;
      applyFilters();
    });
  });

  el.settingChips.addEventListener("click", onChipClick(state.settings));
  el.typeChips.addEventListener("click", onChipClick(state.types));
  el.budgetChips.addEventListener("click", onChipClick(state.budgets));

  el.clear.addEventListener("click", resetFilters);
  document.querySelectorAll("[data-reset]").forEach((button) => {
    button.addEventListener("click", resetFilters);
  });

  el.loadMore.addEventListener("click", () => {
    state.limit += 12;
    renderResults();
  });

  el.filterToggle.addEventListener("click", () => {
    const open = !el.filters.classList.contains("is-open");
    setFiltersOpen(open);
  });

  el.filterDone.addEventListener("click", () => {
    setFiltersOpen(false);
    el.filterToggle.focus();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && el.filters.classList.contains("is-open")) {
      setFiltersOpen(false);
      el.filterToggle.focus();
    }
  });

  el.activeFilters.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-remove]");
    if (!button) return;
    removeFilter(button.dataset.remove, button.dataset.value || "");
  });

  el.results.addEventListener("click", (event) => {
    const reset = event.target.closest("[data-reset]");
    if (reset) resetFilters();
  });

  window.addEventListener("popstate", () => {
    readURL();
    syncControls();
    applyFilters({ animate: false });
  });
}

function onChipClick(set) {
  return (event) => {
    const button = event.target.closest("button[data-value]");
    if (!button) return;
    const value = button.dataset.value;
    if (set.has(value)) set.delete(value);
    else set.add(value);
    button.setAttribute("aria-pressed", set.has(value) ? "true" : "false");
    state.limit = 12;
    applyFilters();
  };
}

function setFiltersOpen(open) {
  el.filters.classList.toggle("is-open", open);
  el.filterToggle.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    const first = el.filters.querySelector("button, select, input");
    if (first) first.focus({ preventScroll: true });
  }
}

function applyMeta() {
  const meta = DATA.meta || {};
  document.querySelectorAll("[data-meta]").forEach((node) => {
    const key = node.dataset.meta;
    if (meta[key] !== undefined && meta[key] !== null) {
      node.textContent = formatNumber(meta[key]);
    }
  });

  if (meta.hero && el.heroCredit) {
    const hero = meta.hero;
    el.heroCredit.href = hero.page || el.heroCredit.href;
    el.heroCredit.textContent = `Photo: ${hero.author || "Wikimedia Commons"} · ${hero.license || "See source"}`;
  }
}

function renderFilterOptions() {
  const meta = DATA.meta || {};
  const stayDistricts = new Map();
  for (const stay of DATA.stays) {
    if (!stay.district) continue;
    stayDistricts.set(stay.district, (stayDistricts.get(stay.district) || 0) + 1);
  }

  for (const setting of meta.settings || []) {
    el.settingChips.append(makeChip(setting.key, setting.label, setting.count));
  }
  for (const type of meta.types || []) {
    if (type.key === "Other" && !type.count) continue;
    el.typeChips.append(makeChip(type.key, type.key, type.count));
  }
  for (const budget of meta.affordability || []) {
    el.budgetChips.append(makeChip(budget.key, BUDGET_LABELS[budget.key] || budget.key, budget.count));
  }

  const districts = Array.from(stayDistricts.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  for (const [district, count] of districts) {
    const option = document.createElement("option");
    option.value = district;
    option.textContent = `${district} · ${count}`;
    el.district.append(option);
  }
}

function makeChip(value, label, count) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "chip";
  button.dataset.value = value;
  button.setAttribute("aria-pressed", "false");
  button.append(document.createTextNode(label));
  if (count !== undefined) {
    const small = document.createElement("small");
    small.textContent = count;
    button.append(small);
  }
  return button;
}

function readURL() {
  const params = new URLSearchParams(window.location.search);
  state.q = params.get("q") || "";
  replaceSet(state.settings, parseList(params.get("setting")));
  state.district = params.get("district") || "";
  replaceSet(state.types, parseList(params.get("type")));
  replaceSet(state.budgets, parseList(params.get("budget")));
  replaceSet(state.must, parseList(params.get("must")));
  state.minRating = Number(params.get("rating")) || 0;
  state.sort = params.get("sort") || "rank";
  state.limit = 12;
  if (!["rank", "rating", "reviews", "budget", "elevation"].includes(state.sort)) state.sort = "rank";
  if (!["", "4.5", "4.7", "4.8", "4.9"].includes(String(state.minRating))) state.minRating = 0;
  if (state.district && !Array.from(el.district.options).some((option) => option.value === state.district)) {
    state.district = "";
  }
  el.search.value = state.q;
  el.district.value = state.district;
  el.rating.value = String(state.minRating || 0);
  el.sort.value = state.sort;
}

function parseList(value) {
  return (value || "").split(",").map((item) => item.trim()).filter(Boolean);
}

function replaceSet(target, values) {
  target.clear();
  values.forEach((value) => target.add(value));
}

function writeURL() {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.settings.size) params.set("setting", Array.from(state.settings).join(","));
  if (state.district) params.set("district", state.district);
  if (state.types.size) params.set("type", Array.from(state.types).join(","));
  if (state.budgets.size) params.set("budget", Array.from(state.budgets).join(","));
  if (state.must.size) params.set("must", Array.from(state.must).join(","));
  if (state.minRating) params.set("rating", String(state.minRating));
  if (state.sort && state.sort !== "rank") params.set("sort", state.sort);
  const query = params.toString();
  const url = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.replaceState(null, "", url);
}

function syncControls() {
  applyPressed(el.settingChips, state.settings);
  applyPressed(el.typeChips, state.types);
  applyPressed(el.budgetChips, state.budgets);
  for (const input of el.mustInputs) {
    input.checked = state.must.has(input.value);
  }
  el.search.value = state.q;
  el.district.value = state.district;
  el.rating.value = String(state.minRating || 0);
  el.sort.value = state.sort;
  updateFilterCount();
}

function applyPressed(container, values) {
  container.querySelectorAll("button[data-value]").forEach((button) => {
    button.setAttribute("aria-pressed", values.has(button.dataset.value) ? "true" : "false");
  });
}

function updateFilterCount() {
  let count = 0;
  if (state.q) count += 1;
  if (state.settings.size) count += 1;
  if (state.district) count += 1;
  if (state.types.size) count += 1;
  if (state.budgets.size) count += 1;
  if (state.must.size) count += 1;
  if (state.minRating) count += 1;
  el.filterCount.textContent = count;
  el.filterCount.hidden = count === 0;
  el.filterToggle.setAttribute("aria-label", count ? `Filters, ${count} active` : "Filters");
}

function applyFilters({ animate = true, keepFocus = false } = {}) {
  if (!DATA) return;
  FILTERED = filterAndSort();
  if (animate) {
    el.results.style.opacity = "0.45";
    window.requestAnimationFrame(() => renderResults());
  } else {
    renderResults();
  }
  writeURL();
  updateFilterCount();
  renderActiveFilters();
  updateFieldDim();
  if (!keepFocus && document.activeElement === el.sort) el.sort.blur();
}

function filterAndSort() {
  const needle = state.q.toLowerCase();
  const tokens = needle.split(/\s+/).filter(Boolean);
  let list = DATA.stays.filter((stay) => {
    if (tokens.length) {
      const haystack = [
        stay.name,
        stay.town,
        stay.district,
        stay.type,
        stay.setting,
        stay.setting_label,
        ...(stay.tags || []),
        ...(stay.best_for || []),
        ...(stay.why || []),
      ].join(" ").toLowerCase();
      if (!tokens.every((token) => haystack.includes(token))) return false;
    }
    if (state.settings.size && !state.settings.has(stay.setting)) return false;
    if (state.district && stay.district !== state.district) return false;
    if (state.types.size && !state.types.has(stay.type)) return false;
    if (state.budgets.size && !state.budgets.has(stay.affordability)) return false;
    if (state.must.has("pool") && stay.pool !== "yes") return false;
    if (state.must.has("campfire") && stay.campfire !== "yes") return false;
    if (state.must.has("food") && stay.food !== "included") return false;
    if (state.minRating && stay.rating < state.minRating) return false;
    return true;
  });

  list.sort((a, b) => {
    switch (state.sort) {
      case "rating":
        return b.rating - a.rating || b.reviews - a.reviews;
      case "reviews":
        return b.reviews - a.reviews || b.rating - a.rating;
      case "budget":
        return (BUDGET_ORDER[a.affordability] ?? 9) - (BUDGET_ORDER[b.affordability] ?? 9) || a.rank - b.rank;
      case "elevation":
        return b.elevation - a.elevation || a.rank - b.rank;
      default:
        return a.rank - b.rank;
    }
  });

  return list;
}

function renderResults() {
  const shown = FILTERED.slice(0, state.limit);
  if (!shown.length) {
    el.results.innerHTML = "";
    el.empty.hidden = false;
    el.loadMore.hidden = true;
    el.resultsNote.textContent = "No stays match the current filters.";
    el.resultCount.textContent = "0 stays match";
    el.results.style.opacity = "1";
    el.fieldCount.textContent = "0";
    return;
  }

  el.empty.hidden = true;
  el.results.innerHTML = shown.map(cardTemplate).join("");
  el.results.style.opacity = "1";
  const plural = FILTERED.length === 1 ? "stay" : "stays";
  el.resultCount.textContent = `${formatNumber(FILTERED.length)} ${plural} match${FILTERED.length === 1 ? "es" : ""} the brief`;
  el.loadMore.hidden = shown.length >= FILTERED.length;
  el.resultsNote.textContent = shown.length >= FILTERED.length
    ? `All ${formatNumber(FILTERED.length)} shown`
    : `Showing ${formatNumber(shown.length)} of ${formatNumber(FILTERED.length)} · New images resolve as you scroll`;

  observeImages();
}

function cardTemplate(stay, index) {
  const target = safeUrl(stay.website) || safeUrl(stay.maps_url);
  const title = escapeHTML(stay.name);
  const alt = escapeHTML(stay.image_alt || `${stay.setting_label} landscape near ${stay.town}, Kerala`);
  const website = safeUrl(stay.website);
  const maps = safeUrl(stay.maps_url);
  const badges = buildBadges(stay);
  const why = (stay.why || []).slice(0, 3).map((line) => `<li>${escapeHTML(line)}</li>`).join("");
  const imageMarkup = stay.image
    ? `<img src="${escapeAttr(stay.image)}" alt="${alt}" width="1200" height="900" loading="lazy" decoding="async" data-static-image>`
    : `<img alt="" width="1000" height="750" loading="lazy" decoding="async" data-live-image data-state="loading" data-key="${escapeAttr(stay.image_key)}">`;
  const creditMarkup = stay.image && stay.image_page
    ? `<a class="stay-credit" href="${escapeAttr(stay.image_page)}" target="_blank" rel="noopener" title="Photo: ${escapeAttr(stay.image_author || "Wikimedia Commons")}">Photo: ${escapeHTML(stay.image_author || "Wikimedia Commons")} · ${escapeHTML(stay.image_license || "See source")}</a>`
    : `<a class="stay-credit" href="#" target="_blank" rel="noopener" hidden>Photo: Wikimedia Commons</a>`;

  return `
    <article class="stay-card" data-slug="${escapeAttr(stay.slug)}" style="--i:${index % 12}">
      <figure class="stay-media${stay.image ? "" : " is-loading"}">
        ${imageMarkup}
        <a class="stay-media-hit" href="${escapeAttr(target)}" target="_blank" rel="noopener" aria-label="${title}, open location"></a>
        <span class="stay-rank">No. ${String(stay.rank).padStart(2, "0")}</span>
        ${creditMarkup}
      </figure>
      <div class="stay-body">
        <div class="stay-kicker"><span>${escapeHTML(stay.type)}</span><span>${escapeHTML(stay.town)}, ${escapeHTML(stay.district)}</span></div>
        <h3 class="stay-name"><a href="${escapeAttr(target)}" target="_blank" rel="noopener">${title}</a></h3>
        <div class="stay-rating">
          <strong>${stay.rating.toFixed(1)}</strong><span class="star" aria-hidden="true">★</span>
          <span>${formatNumber(stay.reviews)} reviews</span>
        </div>
        <ul class="stay-why">${why}</ul>
        <div class="stay-badges">${badges}</div>
        <div class="stay-actions">
          ${website ? `<a href="${escapeAttr(website)}" target="_blank" rel="noopener">Website ↗</a>` : `<span></span>`}
          ${maps ? `<a class="map-link" href="${escapeAttr(maps)}" target="_blank" rel="noopener">Map ↗</a>` : ""}
        </div>
      </div>
    </article>
  `;
}

function buildBadges(stay) {
  const badges = [];
  if (stay.pool === "yes") badges.push("Pool");
  if (stay.campfire === "yes") badges.push("Bonfire");
  if (stay.food === "included") badges.push("Meals included");
  const bestFor = (stay.best_for || []).find(Boolean);
  if (bestFor && badges.length < 3) badges.push(bestFor);
  const budget = BUDGET_LABELS[stay.affordability] || stay.affordability;
  const budgetMarkup = `<span class="badge badge--budget">${escapeHTML(budget)}</span>`;
  return badges.map((badge) => `<span class="badge">${escapeHTML(badge)}</span>`).join("") + budgetMarkup;
}

/* Live location photography. The browser asks Wikimedia only when a card enters
   the viewport. The resolved URL and attribution are cached locally, so the
   same search is instant next time. A serverless cache worker can consume the
   same payload later and commit the file to docs/assets/locations/. */
function observeImages() {
  const images = el.results.querySelectorAll("img[data-live-image]");
  images.forEach((img) => {
    if (img.dataset.observed) return;
    img.dataset.observed = "1";
    img.addEventListener("load", () => {
      img.dataset.state = "loaded";
      const media = img.closest(".stay-media");
      if (media) media.classList.remove("is-loading", "is-empty");
    });
    img.addEventListener("error", () => {
      img.dataset.state = "error";
      const media = img.closest(".stay-media");
      if (media) media.classList.add("is-empty");
    });
    const staticImage = img.dataset.staticImage;
    if (staticImage) {
      img.dataset.state = "loaded";
      return;
    }
    if ("IntersectionObserver" in window) {
      if (!imageObserver) {
        imageObserver = new IntersectionObserver((entries) => {
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            imageObserver.unobserve(entry.target);
            enqueueImage(entry.target);
          });
        }, { rootMargin: "600px 0px" });
      }
      imageObserver.observe(img);
    } else {
      enqueueImage(img);
    }
  });
}

function enqueueImage(img) {
  const key = img.dataset.key;
  if (!key || img.dataset.queued) return;
  img.dataset.queued = "1";
  if (imageInflight.has(key)) {
    imageInflight.get(key).then((info) => {
      if (info) paintImage(img, info);
      else markEmpty(img);
    });
    return;
  }
  const task = () => resolveImage(key, img.closest(".stay-card")).then((info) => {
    imageInflight.delete(key);
    if (info) paintImage(img, info);
    else markEmpty(img);
  });
  imageInflight.set(key, new Promise((resolve) => {
    imageQueue.push(() => task().then(resolve));
    drainImageQueue();
  }));
}

function drainImageQueue() {
  while (activeImages < IMAGE_LIMIT && imageQueue.length) {
    const task = imageQueue.shift();
    activeImages += 1;
    Promise.resolve()
      .then(task)
      .finally(() => {
        activeImages -= 1;
        drainImageQueue();
      });
  }
}

async function resolveImage(key, card) {
  const stay = DATA.stays.find((item) => item.image_key === key);
  if (!stay) return null;

  const stored = await resolveFromWorker(key, stay);
  if (stored) return stored;

  return resolveFromCommons(key, stay);
}

// The Worker fetches live from Commons, commits the image into the repository,
// and returns the stored URL. Until WORKER_URL is set, the site falls back to
// calling Commons directly.
async function resolveFromWorker(key, stay) {
  if (!WORKER_URL) return null;
  const params = new URLSearchParams({
    key,
    town: stay.town || "",
    district: stay.district || "",
    setting: stay.setting_label || stay.setting || "landscape",
  });
  try {
    const res = await fetch(`${WORKER_URL}/img?${params.toString()}`, { cache: "no-store" });
    if (!res.ok) return null;
    const info = await res.json();
    if (!info?.url) return null;
    return info;
  } catch {
    return null;
  }
}

async function resolveFromCommons(key, stay) {
  const settingLabel = stay.setting_label || stay.setting || "landscape";
  const town = stay.town || stay.district || "Kerala";
  const queries = [
    `${town} Kerala ${settingLabel}`,
    `${stay.district || town} Kerala ${settingLabel}`,
    `Kerala ${settingLabel} landscape`,
  ];

  let pool = [];
  for (const query of queries) {
    const params = new URLSearchParams({
      action: "query",
      format: "json",
      origin: "*",
      generator: "search",
      gsrsearch: query,
      gsrnamespace: "6",
      gsrlimit: "20",
      prop: "imageinfo",
      iiprop: "url|size|mime|extmetadata",
      iiurlwidth: "1000",
    });

    const payload = await commonsQuery(params);
    const pages = Object.values(payload?.query?.pages || {});
    const candidates = pages
      .map((page) => scoreCandidate(page, stay))
      .filter(Boolean)
      .sort((a, b) => b.score - a.score);
    pool = pool.concat(candidates.slice(0, 6));
    if (candidates.length && candidates[0].score >= 9) break;
  }

  if (!pool.length) return null;
  pool.sort((a, b) => b.score - a.score);
  const topScore = pool[0].score;
  const diverse = pool.filter((candidate) => candidate.score >= topScore - 1.2).slice(0, 4);
  const best = diverse[hashString(key) % diverse.length];

  const info = {
    url: best.thumburl,
    page: best.page,
    title: best.title,
    author: best.author,
    license: best.license,
    license_url: best.license_url,
    alt: best.alt,
  };
  return info;
}

async function commonsQuery(params, attempt = 0) {
  try {
    const response = await fetch(`${COMMONS_API}?${params}`);
    if (!response.ok) return null;
    const payload = await response.json();
    const code = payload?.error?.code || "";
    if (attempt < 2 && /too-busy|ratelimit|timeout|internal/i.test(code)) {
      await sleep(500 * (attempt + 1));
      return commonsQuery(params, attempt + 1);
    }
    return payload;
  } catch (error) {
    if (attempt < 2) {
      await sleep(500 * (attempt + 1));
      return commonsQuery(params, attempt + 1);
    }
    console.warn("image lookup failed", error);
    return null;
  }
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function hashString(value) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash;
}

function scoreCandidate(page, stay) {
  const info = page?.imageinfo?.[0];
  if (!info || !info.thumburl) return null;
  if (!["image/jpeg", "image/png", "image/webp"].includes(info.mime)) return null;
  const width = Number(info.width) || 0;
  const height = Number(info.height) || 0;
  if (width < 800 || height < 500 || width / height < 1.08) return null;
  const meta = info.extmetadata || {};
  const text = [
    page.title,
    stripHtml(meta.ImageDescription?.value),
    stripHtml(meta.ObjectName?.value),
    stripHtml(meta.Categories?.value),
  ].join(" ").toLowerCase();

  const positive = {
    beach: 6, backwater: 6, lake: 5, river: 4, hill: 5, hills: 5, mountain: 5, mountains: 5,
    forest: 5, tea: 5, plantation: 4, paddy: 3, field: 2, sunset: 4, sunrise: 4, mist: 4,
    valley: 5, view: 4, viewpoint: 4, landscape: 5, nature: 3, canal: 3, houseboat: 5,
    boat: 2, coast: 5, sea: 4, waterfall: 5, reservoir: 2, garden: 2, resort: 2,
    kerala: 1, cliff: 4, rolling: 3, panoramic: 4, scenery: 5, scenic: 5, ridge: 4,
  };
  const negative = {
    map: 16, diagram: 16, logo: 16, plan: 12, chart: 16, poster: 14, sign: 10,
    "license plate": 20, border: 6, statue: 12, idol: 12, portrait: 16, painting: 16,
    survey: 20, pillar: 18, microwave: 20, antenna: 18, tower: 12, mast: 12,
    people: 10, person: 8, man: 6, woman: 8, crowd: 10, selfie: 14, ritual: 14,
    theyyam: 14, festival: 10, performance: 12, fishing: 12, fire: 8,
    "water tower": 14, road: 8, flight: 14, jellyfish: 16, egret: 14, cormorant: 14,
    dragonfly: 16, damselfly: 16, insect: 12, butterfly: 16, moth: 16, bird: 8,
    flower: 10, plant: 6, leaf: 6, fruit: 12, spice: 14, nutmeg: 16, pepper: 12,
    cardamom: 16, turmeric: 16, beverage: 18, church: 12, temple: 12, mosque: 12,
    dam: 8, penstock: 20, tunnel: 16, powerstation: 22, "power station": 22,
    power: 10, construction: 12, bridge: 8, tamilnadu: 18, "tamil nadu": 8,
    karnataka: 14, "new chums": 22, "new zealand": 22, australia: 22, "sri lanka": 18,
    maldives: 18, food: 10, menu: 12, room: 6, interior: 8, bathroom: 10, bed: 8,
    building: 6, house: 4, home: 4, hotel: 2, "census": 20, pdf: 20, lawn: 4,
  };

  let score = 0;
  for (const [word, weight] of Object.entries(positive)) {
    if (text.includes(word)) score += weight;
  }
  for (const [word, weight] of Object.entries(negative)) {
    if (text.includes(word)) score -= weight;
  }
  if (stay.town && text.includes(stay.town.toLowerCase())) score += 5;
  if (stay.district && text.includes(stay.district.toLowerCase())) score += 3;
  score += Math.min(width, 3200) / 1000;
  score += Math.min(width / height, 2);
  if (score < 6) return null;

  return {
    score,
    thumburl: info.thumburl,
    page: info.descriptionurl || "",
    title: page.title || "",
    author: stripHtml(meta.Artist?.value) || "Wikimedia Commons",
    license: stripHtml(meta.LicenseShortName?.value) || "See source",
    license_url: stripHtml(meta.LicenseUrl?.value) || "",
    alt: stripHtml(meta.ImageDescription?.value) || stripFile(page.title),
  };
}

function paintImage(img, info) {
  if (!info?.url) return markEmpty(img);
  img.src = info.url;
  img.alt = info.alt || "Kerala landscape";
  img.dataset.state = "loaded";
  const media = img.closest(".stay-media");
  if (media) media.classList.remove("is-loading", "is-empty");
  const card = img.closest(".stay-card");
  if (card) showCredit(card, info);
}

function showCredit(card, info) {
  const credit = card.querySelector(".stay-credit");
  if (!credit) return;
  credit.hidden = false;
  credit.href = info.page || "#";
  credit.textContent = `Photo: ${shorten(info.author || "Wikimedia Commons", 42)} · ${info.license || "See source"}`;
  credit.title = `${info.title || "Wikimedia Commons"} — ${info.author || "Unknown"}`;
}

function markEmpty(img) {
  img.dataset.state = "error";
  const media = img.closest(".stay-media");
  if (media) media.classList.add("is-empty");
}

function stripHtml(value) {
  if (!value) return "";
  return String(value)
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#039;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function stripFile(title) {
  return (title || "").replace(/^File:/, "").replace(/\.[a-z0-9]+$/i, "").replace(/_/g, " ");
}

/* Terrain field */

function buildField() {
  const svg = el.fieldSvg;
  const NS = "http://www.w3.org/2000/svg";
  svg.replaceChildren();
  const W = 1200;
  const H = 460;
  const plot = { left: 88, right: 1152, top: 38, bottom: 404 };
  const lons = DATA.stays.map((stay) => stay.lon);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const maxElev = Math.max(1800, ...DATA.stays.map((stay) => stay.elevation || 0));
  const scaleX = (lon) => plot.left + ((lon - minLon) / (maxLon - minLon || 1)) * (plot.right - plot.left);
  const scaleY = (elevation) => plot.bottom - (Math.min(elevation || 0, maxElev) / maxElev) * (plot.bottom - plot.top);

  const make = (name, attrs = {}, text = "") => {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    if (text) node.textContent = text;
    return node;
  };

  // Decorative contour bands: a coast line, then ridge lines.
  const contours = [
    "M88 350 C 210 300, 300 360, 420 314 S 680 240, 820 280 S 1040 250, 1152 214",
    "M88 316 C 230 268, 330 320, 455 276 S 700 210, 850 242 S 1050 218, 1152 180",
    "M88 278 C 250 236, 350 280, 490 238 S 730 174, 890 206 S 1070 184, 1152 148",
  ];
  for (const d of contours) svg.append(make("path", { d, class: "field-contour" }));
  svg.append(make("path", { d: "M88 396 C 170 388, 225 373, 290 382 S 410 369, 500 360", class: "field-coast" }));

  for (const elevation of [0, 500, 1000, 1500, 2000]) {
    const y = scaleY(elevation);
    svg.append(make("line", { x1: plot.left, x2: plot.right, y1: y, y2: y, class: "field-grid-line" }));
    svg.append(make("text", { x: plot.left - 12, y: y + 3, "text-anchor": "end", class: "field-grid-label" }, `${formatNumber(elevation)} m`));
  }

  for (const lon of [74.8, 75.6, 76.4, 77.2]) {
    if (lon < minLon - 0.1 || lon > maxLon + 0.1) continue;
    const x = scaleX(lon);
    svg.append(make("line", { x1: x, x2: x, y1: plot.top, y2: plot.bottom, class: "field-grid-line" }));
    svg.append(make("text", { x, y: plot.bottom + 22, "text-anchor": "middle", class: "field-grid-label" }, `${lon.toFixed(1)}° E`));
  }

  svg.append(make("text", { x: plot.left, y: 20, class: "field-grid-label" }, "ELEVATION / METRES"));
  svg.append(make("text", { x: plot.right, y: H - 5, "text-anchor": "end", class: "field-grid-label" }, "WEST COAST  →  WESTERN GHATS"));

  const dots = new Map();
  DATA.stays.forEach((stay, index) => {
    const category = SETTING_COLORS[stay.setting] || "land";
    const jitterX = ((index * 37) % 13 - 6) * 1.05;
    const jitterY = ((index * 53) % 11 - 5) * 0.9;
    const x = Math.min(plot.right - 4, Math.max(plot.left + 4, scaleX(stay.lon) + jitterX));
    const y = Math.min(plot.bottom - 4, Math.max(plot.top + 4, scaleY(stay.elevation) + jitterY));
    const dot = make("circle", {
      cx: x,
      cy: y,
      r: 5.2,
      class: `field-dot field-dot--${category}`,
      "data-slug": stay.slug,
    });
    dot.append(make("title", {}, `${stay.name} — ${stay.town}, ${stay.district} · ${formatNumber(stay.elevation)} m`));
    svg.append(dot);
    dots.set(stay.slug, dot);
  });

  field = { dots };
  svg.addEventListener("pointerover", (event) => {
    const dot = event.target.closest(".field-dot");
    if (!dot || dot.classList.contains("is-dim")) return;
    showFieldTip(dot, event);
  });
  svg.addEventListener("pointermove", (event) => {
    const dot = event.target.closest(".field-dot");
    if (!dot || dot.classList.contains("is-dim")) return;
    showFieldTip(dot, event);
  });
  svg.addEventListener("pointerout", (event) => {
    if (!event.target.closest(".field-dot")) return;
    el.fieldTip.hidden = true;
  });
  svg.addEventListener("click", (event) => {
    const dot = event.target.closest(".field-dot");
    if (!dot || dot.classList.contains("is-dim")) return;
    revealStay(dot.dataset.slug);
  });
}

function showFieldTip(dot, event) {
  const stay = DATA.stays.find((item) => item.slug === dot.dataset.slug);
  if (!stay) return;
  el.fieldTip.innerHTML = `<b>${escapeHTML(stay.name)}</b><span>${escapeHTML(stay.town)}, ${escapeHTML(stay.district)} · ${formatNumber(stay.elevation)} m</span>`;
  el.fieldTip.hidden = false;
  const wrap = el.fieldSvg.parentElement.getBoundingClientRect();
  const tip = el.fieldTip.getBoundingClientRect();
  const x = Math.min(Math.max(event.clientX - wrap.left, tip.width / 2 + 8), wrap.width - tip.width / 2 - 8);
  el.fieldTip.style.left = `${x}px`;
  el.fieldTip.style.top = `${event.clientY - wrap.top}px`;
}

function updateFieldDim() {
  if (!field) return;
  const visible = new Set(FILTERED.map((stay) => stay.slug));
  field.dots.forEach((dot, slug) => {
    dot.classList.toggle("is-dim", !visible.has(slug));
  });
  if (el.fieldCount) el.fieldCount.textContent = formatNumber(FILTERED.length);
  lastFieldList = FILTERED;
}

function revealStay(slug) {
  const index = FILTERED.findIndex((stay) => stay.slug === slug);
  if (index < 0) return;
  state.limit = Math.max(state.limit, index + 1);
  renderResults();
  window.requestAnimationFrame(() => {
    const card = el.results.querySelector(`[data-slug="${cssEscape(slug)}"]`);
    if (card) {
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.add("is-target");
      window.setTimeout(() => card.classList.remove("is-target"), 1600);
    }
  });
}

/* Active filters */

function renderActiveFilters() {
  const chips = [];
  if (state.q) chips.push(activeChip(`Search: ${state.q}`, "q"));
  state.settings.forEach((value) => chips.push(activeChip(labelForSetting(value), "setting", value)));
  if (state.district) chips.push(activeChip(state.district, "district", state.district));
  state.types.forEach((value) => chips.push(activeChip(value, "type", value)));
  state.budgets.forEach((value) => chips.push(activeChip(BUDGET_LABELS[value] || value, "budget", value)));
  state.must.forEach((value) => chips.push(activeChip(mustLabel(value), "must", value)));
  if (state.minRating) chips.push(activeChip(`${state.minRating}+ rating`, "rating", String(state.minRating)));
  el.activeFilters.innerHTML = chips.join("");
}

function activeChip(label, type, value = "") {
  return `<button class="active-chip" type="button" data-remove="${type}" data-value="${escapeAttr(value)}">${escapeHTML(label)} <span aria-hidden="true">×</span></button>`;
}

function labelForSetting(value) {
  const setting = (DATA.meta.settings || []).find((item) => item.key === value);
  return setting?.label || value;
}

function mustLabel(value) {
  return { pool: "Pool", campfire: "Bonfire", food: "Meals included" }[value] || value;
}

function removeFilter(type, value) {
  if (type === "q") {
    state.q = "";
    el.search.value = "";
  } else if (type === "setting") state.settings.delete(value);
  else if (type === "district") state.district = "";
  else if (type === "type") state.types.delete(value);
  else if (type === "budget") state.budgets.delete(value);
  else if (type === "must") state.must.delete(value);
  else if (type === "rating") state.minRating = 0;
  state.limit = 12;
  syncControls();
  applyFilters();
}

function resetFilters() {
  state.q = "";
  state.settings.clear();
  state.district = "";
  state.types.clear();
  state.budgets.clear();
  state.must.clear();
  state.minRating = 0;
  state.sort = "rank";
  state.limit = 12;
  syncControls();
  setFiltersOpen(false);
  applyFilters();
  el.search.focus({ preventScroll: true });
}

/* Utilities */

function safeUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value, window.location.href);
    if (!["http:", "https:"].includes(url.protocol)) return "";
    return url.href;
  } catch {
    return "";
  }
}

function escapeHTML(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeAttr(value) {
  return escapeHTML(value);
}

function shorten(value, length) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("en-IN") : String(value ?? "");
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(value);
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}
