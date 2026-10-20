/* Stay file — one listing, resolved from the shortlist dataset and the
   enriched public table. Photos are hotlinked Wikimedia Commons URLs. */

const API = "https://lurkdmbjoapgimruktfs.supabase.co";
const KEY = "sb_publishable_6RatKlW42cuc8koN45VB2g_pWMIFX7A";
const IMAGE_API = `${API}/functions/v1/img`;
const DATA_TOP = "data/resorts.json";
const DATA_ALL = "data/resorts-all.json";

const BUDGET_LABELS = { low: "Low", medium: "Medium", high: "High", premium: "Premium" };
const state = { stay: null, meta: null, media: [], gallery: { items: [], index: 0, opener: null } };
const ACCENTS = { water: "#176d64", green: "#2f6a4e", land: "#a7462f" };
const SETTING_GROUP = {
  beach: "water", backwater: "water", lake: "water", river: "water", island: "water",
  hill: "green", mountain: "green", forest: "green", plantation: "green", cliff: "green", waterfall: "green",
  urban: "land", countryside: "land",
};
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let toastTimer = null;
let scrollTicking = false;
const els = {};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  cache();
  bindLightbox();
  bindChrome();
  const slug = new URLSearchParams(location.search).get("slug");
  if (!slug) return showError();

  try {
    const top = await fetchJSON(DATA_TOP);
    let list = top.stays || [];
    let stay = list.find((item) => item.slug === slug);
    let meta = top.meta || {};

    if (!stay) {
      const all = await fetchJSON(DATA_ALL).catch(() => null);
      if (all) {
        list = all.stays || [];
        stay = list.find((item) => item.slug === slug);
        meta = all.meta || meta;
      }
    }
    if (!stay) return showError();

    state.stay = stay;
    state.meta = meta;

    const placeId = placeIdOf(stay);
    const [enriched, media] = await Promise.all([
      placeId ? fetchEnriched(placeId, stay) : Promise.resolve(null),
      placeId ? fetchMedia(placeId) : Promise.resolve([]),
    ]);

    state.media = media.length ? media : await fallbackMedia(stay, placeId);
    render(stay, enriched, list, meta);
    setupExperience(stay, enriched, list);
    document.querySelector("[data-stay-root]").setAttribute("aria-busy", "false");
  } catch (error) {
    console.error(error);
    showError();
  }
}

function cache() {
  document.querySelectorAll("[data-field]").forEach((node) => {
    els[node.dataset.field] = node;
  });
  els.lightbox = document.getElementById("lightbox");
  els.lightboxImage = document.getElementById("lightbox-image");
  els.lightboxCaption = document.getElementById("lightbox-caption");
  els.lightboxCount = document.getElementById("lightbox-count");
  els.lightboxCredit = document.getElementById("lightbox-credit");
  els.lightboxClose = document.querySelector("[data-lightbox-close]");
  els.lightboxPrev = document.querySelector("[data-lightbox-prev]");
  els.lightboxNext = document.querySelector("[data-lightbox-next]");
  els.heroOpen = document.querySelector("[data-open-gallery]");
  els["next-link"] = document.querySelector("[data-next-link]");
  els["next-label"] = document.querySelector("[data-next-label]");
  els["next-name"] = document.querySelector("[data-next-name]");
  els["next-meta"] = document.querySelector("[data-next-meta]");
  els.hero = document.querySelector(".sd-hero");
  els.progress = document.querySelector("[data-progress] span");
  els.jump = document.querySelector("[data-jump]");
  els.jumpMeta = document.querySelector("[data-jump-meta]");
  els.toast = document.querySelector("[data-toast]");
  els["location-section"] = document.querySelector("[data-field=location-section]");
  els.map = document.querySelector("[data-field=map]");
  els.more = document.querySelector("[data-field=more]");
  els["more-title"] = document.querySelector("[data-field=more-title]");
  els["more-list"] = document.querySelector("[data-field=more-list]");
  els.dock = document.querySelector("[data-dock]");
  els["dock-name"] = document.querySelector("[data-dock-name]");
  els["dock-meta"] = document.querySelector("[data-dock-meta]");
  els["dock-actions"] = document.querySelector("[data-dock-actions]");
}

async function fetchJSON(url) {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${url} ${response.status}`);
  return response.json();
}

async function rest(path) {
  const response = await fetch(`${API}/rest/v1/${path}`, {
    headers: { apikey: KEY },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`REST ${response.status}`);
  return response.json();
}

function placeIdOf(stay) {
  if (stay.place_id) return stay.place_id;
  const match = String(stay.maps_url || "").match(/place_id[=:]([^&]+)/);
  if (!match) return "";
  return decodeURIComponent(match[1]);
}

async function fetchEnriched(placeId, stay) {
  const query = new URLSearchParams({
    select: "*",
    place_id: `eq.${placeId}`,
    limit: "1",
  });
  try {
    const rows = await rest(`resorts_enriched?${query}`);
    if (rows.length) return rows[0];
  } catch (error) {
    console.warn(error);
  }
  // Fall back to name + district when the place id has drifted.
  try {
    const query = new URLSearchParams({
      select: "*",
      name: `eq.${stay.name}`,
      district: `eq.${stay.district}`,
      limit: "1",
    });
    const rows = await rest(`resorts_enriched?${query}`);
    return rows[0] || null;
  } catch {
    return null;
  }
}

async function fetchMedia(placeId) {
  const query = new URLSearchParams({
    select: "public_url,commons_title,alt,author,license,license_url,source_page,width,height",
    place_id: `eq.${placeId}`,
    order: "score.desc",
    limit: "6",
  });
  try {
    const rows = await rest(`listing_media?${query}`);
    return rows
      .filter((row) => row.public_url)
      .map((row) => ({
        url: row.public_url,
        title: stripFile(row.commons_title || row.alt || ""),
        alt: stripHtml(row.alt || row.commons_title || "Kerala landscape"),
        author: stripHtml(row.author || "Wikimedia Commons"),
        license: row.license || "See source",
        page: row.source_page || "",
      }));
  } catch {
    return [];
  }
}

// If the harvester has not reached this listing yet, ask the image function,
// which resolves and records Commons URLs on demand.
async function fallbackMedia(stay, placeId) {
  const params = new URLSearchParams({
    key: stay.image_key || stay.slug,
    town: stay.town || "",
    district: stay.district || "",
    setting: stay.setting_label || stay.setting || "landscape",
    list: "1",
  });
  if (placeId) params.set("place_id", placeId);
  try {
    const response = await fetch(`${IMAGE_API}?${params}`, {
      headers: { apikey: KEY },
      cache: "no-store",
    });
    if (!response.ok) return [];
    const info = await response.json();
    const items = Array.isArray(info?.images) ? info.images : info?.url ? [info] : [];
    return items.filter((item) => item.url).map((item) => ({
      url: item.url,
      title: stripFile(item.title || item.alt || ""),
      alt: stripHtml(item.alt || item.title || "Kerala landscape"),
      author: stripHtml(item.author || "Wikimedia Commons"),
      license: item.license || "See source",
      page: item.page || item.source_page || "",
    }));
  } catch {
    return [];
  }
}

/* Render ---------------------------------------------------------------- */

function render(stay, enriched, list, meta) {
  const place = [stay.town, stay.district].filter(Boolean).join(", ");
  document.title = `${stay.name} — Kerala Field Notes`;
  const description = `Stay file for ${stay.name} in ${place}: evidence, terrain, amenities, and surrounding landscape.`;
  setMeta("description", description);
  setMeta("og:title", `${stay.name} — Kerala Field Notes`);
  setMeta("og:description", description);
  setMeta("og:url", window.location.href);
  setCanonical(window.location.href);

  els.rank.textContent = `No. ${String(stay.rank || 0).padStart(2, "0")} / Stay file`;
  els.place.textContent = place || "Kerala";
  els.name.textContent = stay.name;
  els.dek.textContent = makeDek(stay, enriched);
  els["hero-stats"].innerHTML = heroStats(stay);
  els["hero-actions"].innerHTML = heroActions(stay, enriched);

  renderHeroImage(stay, enriched);
  renderStrip();
  els.why.innerHTML = caseLines(stay, enriched)
    .map((line) => `<li><span>${escapeHTML(line)}</span></li>`)
    .join("");

  const prose = cleanDescription(enriched?.description);
  if (prose) {
    els.description.textContent = prose;
    els.description.hidden = false;
  }

  els.terrain.innerHTML = terrainRows(stay, enriched) + terrainTags(stay);
  els.reviews.innerHTML = reviewCard(stay, meta);
  els["reviews-caveat"].textContent = reviewCaveat(stay, meta);
  els["photo-grid"].innerHTML = photoGrid();
  els.ledger.innerHTML = ledgerRows(stay, enriched);
  els.amenities.innerHTML = amenityList(stay, enriched);
  els.cta.innerHTML = ctaButtons(stay, enriched);
  renderNext(stay, list);
}

function renderHeroImage(stay, enriched) {
  const item = state.media[0];
  const frame = els["hero-figure"].querySelector(".sd-hero-frame");
  const image = els["hero-image"];

  if (!item) {
    frame.classList.remove("is-loading");
    frame.classList.add("is-empty");
    image.removeAttribute("src");
    els["hero-credit"].textContent = "No Commons photo resolved for this stay yet.";
    return;
  }

  image.src = item.url;
  image.alt = item.alt || `Landscape near ${stay.town || stay.district || "Kerala"}`;
  image.addEventListener("load", () => frame.classList.add("is-loaded"), { once: true });
  image.addEventListener("error", () => {
    frame.classList.remove("is-loading");
    frame.classList.add("is-empty");
  }, { once: true });

  els["hero-credit"].innerHTML = item.page
    ? `<a href="${escapeAttr(item.page)}" target="_blank" rel="noopener">Photo: ${escapeHTML(shorten(item.author, 42))} · ${escapeHTML(item.license)}</a>`
    : `Photo: ${escapeHTML(shorten(item.author, 42))} · ${escapeHTML(item.license)}`;

  const open = els.heroOpen;
  open.hidden = false;
  open.setAttribute("aria-label", `Open the photo gallery for ${stay.name}`);
  open.addEventListener("click", () => openGallery(0, open));
}

function renderStrip() {
  if (state.media.length < 2) {
    els["thumb-strip"].hidden = true;
    return;
  }
  els["thumb-strip"].hidden = false;
  els["thumb-strip"].innerHTML = state.media
    .map((item, index) => `
      <button class="sd-thumb" type="button" data-thumb="${index}" data-number="${String(index + 1).padStart(2, "0")}" aria-label="Open photo ${index + 1} of ${state.media.length}">
        <img src="${escapeAttr(item.url)}" alt="${escapeAttr(item.alt)}" loading="lazy" decoding="async" width="320" height="240">
      </button>`)
    .join("");
  els["thumb-strip"].addEventListener("click", (event) => {
    const button = event.target.closest("[data-thumb]");
    if (button) openGallery(Number(button.dataset.thumb), button);
  });
}

function photoGrid() {
  if (!state.media.length) {
    return `<p class="sd-photo-empty">No Commons photo resolved for this stay yet. The harvester adds them in the background.</p>`;
  }
  return state.media
    .map((item, index) => `
      <button class="sd-photo" type="button" data-photo="${index}" aria-label="Open photo ${index + 1} of ${state.media.length}">
        <img src="${escapeAttr(item.url)}" alt="${escapeAttr(shorten(item.alt, 160))}" loading="lazy" decoding="async" width="640" height="480">
        <span class="sd-photo-credit">${escapeHTML(shorten(item.author, 26))} · ${escapeHTML(item.license)}</span>
      </button>`)
    .join("");
}

function heroStats(stay) {
  const stats = [];
  if (Number.isFinite(stay.rating)) {
    stats.push(`<div class="sd-stat"><dt>Google rating</dt><dd><span data-count="${stay.rating}" data-decimals="1">${stay.rating.toFixed(1)}</span><small>★</small></dd></div>`);
  }
  if (Number.isFinite(stay.reviews)) {
    stats.push(`<div class="sd-stat"><dt>Reviews</dt><dd><span data-count="${stay.reviews}" data-decimals="0">${formatNumber(stay.reviews)}</span></dd></div>`);
  }
  if (Number.isFinite(stay.score)) {
    stats.push(`<div class="sd-stat"><dt>Field score</dt><dd><span data-count="${stay.score}" data-decimals="2">${stay.score.toFixed(2)}</span></dd></div>`);
  }
  if (Number.isFinite(stay.elevation) && stay.elevation > 0) {
    stats.push(`<div class="sd-stat"><dt>Elevation</dt><dd><span data-count="${stay.elevation}" data-decimals="0">${formatNumber(stay.elevation)}</span><small>m</small></dd></div>`);
  }
  return stats.join("");
}

function heroActions(stay, enriched) {
  const website = safeUrl(stay.website) || safeUrl(enriched?.website);
  const maps = safeUrl(stay.maps_url) || mapsUrl(stay);
  const actions = [];
  if (website) actions.push(`<a class="sd-button sd-button--solid" href="${escapeAttr(website)}" target="_blank" rel="noopener">Visit website ↗</a>`);
  if (maps) actions.push(`<a class="sd-button" href="${escapeAttr(maps)}" target="_blank" rel="noopener">Google Maps ↗</a>`);
  if (state.media.length) actions.push(`<button class="sd-button sd-button--quiet" type="button" data-hero-photos>${state.media.length} photo${state.media.length === 1 ? "" : "s"}</button>`);
  actions.push(`<button class="sd-button sd-button--quiet" type="button" data-share>Share file</button>`);
  return actions.join("");
}

function makeDek(stay, enriched) {
  const place = [stay.town, stay.district].filter(Boolean).join(", ");
  const kind = stay.type || "Stay";
  const setting = stay.setting_label ? `${stay.setting_label.toLowerCase()} setting` : "Kerala";
  if (place) return `A ${kind.toLowerCase()} in the ${setting} around ${place}.`;
  return enriched?.environment_reason ? sentence(enriched.environment_reason) : `A ${kind.toLowerCase()} in Kerala.`;
}

function caseLines(stay, enriched) {
  const lines = (stay.why || []).filter(Boolean);
  if (!lines.length && enriched?.environment_reason) lines.push(sentence(enriched.environment_reason));
  if (!lines.length) {
    if (stay.rating) lines.push(`Rated ${stay.rating.toFixed(1)} across ${formatNumber(stay.reviews)} Google reviews.`);
    if (stay.setting_label) lines.push(`${stay.setting_label} setting in ${stay.district}.`);
  }
  return lines;
}

function terrainRows(stay, enriched) {
  const rows = [];
  if (stay.setting_label) {
    rows.push(row("Setting", stay.setting_label, ""));
  }
  const pairs = [
    ["Coast", enriched?.nearest_beach, enriched?.distance_to_coast_km ?? enriched?.distance_to_beach_km],
    ["Waterbody", enriched?.nearest_waterbody, enriched?.distance_to_waterbody_km],
    ["Lake", enriched?.nearest_lake, enriched?.distance_to_lake_km],
    ["Forest", enriched?.nearest_forest, enriched?.distance_to_forest_km],
    ["Town", enriched?.nearest_town, enriched?.distance_to_town_km],
    ["Airport", enriched?.nearest_airport, enriched?.distance_to_airport_km],
    ["Railway", enriched?.nearest_railway_station, enriched?.distance_to_railway_km],
  ];
  for (const [label, name, distance] of pairs) {
    if (!name) continue;
    rows.push(row(label, name, Number.isFinite(Number(distance)) ? `${Number(distance).toFixed(1)} km` : ""));
  }
  return rows.join("");
}

function terrainTags(stay) {
  const tags = (stay.tags || []).filter((tag) => tag !== stay.setting).slice(0, 4);
  return tags.length ? `<p class="sd-terrain-tags">Terrain reads as ${tags.join(", ")}.</p>` : "";
}

function row(label, value, note) {
  return `<div><dt>${escapeHTML(label)}</dt><dd>${escapeHTML(value || "—")}</dd><span class="sd-terrain-distance">${escapeHTML(note || "")}</span></div>`;
}

function reviewCard(stay, meta) {
  if (!Number.isFinite(stay.rating)) {
    return `<div class="sd-score"><span>Rating</span><strong>—</strong></div>
      <div class="sd-review-copy"><h3>No public rating captured</h3><p>This listing was kept for its geography, not its review count.</p></div>`;
  }
  const stars = Math.max(1, Math.round(stay.rating));
  const maps = safeUrl(stay.maps_url) || mapsUrl(stay);
  const when = meta?.generated_at ? `at scrape time (${meta.generated_at})` : "at scrape time";
  return `
    <div class="sd-score">
      <span>Google</span>
      <strong>${stay.rating.toFixed(1)}</strong>
      <span class="sd-stars" aria-hidden="true">${"★".repeat(stars)}${"☆".repeat(5 - stars)}</span>
      <span>out of 5</span>
    </div>
    <div class="sd-review-copy">
      <h3>${formatNumber(stay.reviews)} reviews</h3>
      <p>A field score of <strong>${Number(stay.score || 0).toFixed(2)}</strong> places this stay at No. ${stay.rank} in the dataset. Review text is not stored — only the aggregate.</p>
    </div>
    <div class="sd-review-link">${maps ? `<a href="${escapeAttr(maps)}" target="_blank" rel="noopener">Read on Google ↗</a>` : ""}</div>`;
}

function reviewCaveat(stay, meta) {
  if (!Number.isFinite(stay.rating)) return "Ratings come from the public Google listing captured during enrichment.";
  const when = meta?.generated_at ? ` on ${meta.generated_at}` : "";
  return `Rating and review count were read from the public Google listing${when}. Review text is not copied or stored.`;
}

function ledgerRows(stay, enriched) {
  const rows = [];
  rows.push(["Type", stay.type]);
  rows.push(["Setting", stay.setting_label]);
  rows.push(["Town", stay.town || enriched?.nearest_town]);
  rows.push(["District", stay.district]);
  if (Number.isFinite(stay.elevation)) rows.push(["Elevation", `${formatNumber(stay.elevation)} m`]);
  if (stay.affordability) rows.push(["Budget band", BUDGET_LABELS[stay.affordability] || stay.affordability]);
  const coords = Number.isFinite(stay.lat) && Number.isFinite(stay.lon)
    ? `${stay.lat.toFixed(4)}° N, ${stay.lon.toFixed(4)}° E`
    : "";
  if (coords) {
    const maps = safeUrl(stay.maps_url) || mapsUrl(stay);
    rows.push(["Coordinate", maps ? `<a href="${escapeAttr(maps)}" target="_blank" rel="noopener">${coords}</a>` : coords]);
  }
  if (enriched?.phone) rows.push(["Phone", `<a href="tel:${escapeAttr(enriched.phone.replace(/[^\d+]/g, ""))}">${escapeHTML(enriched.phone)}</a>`]);
  if (enriched?.email) rows.push(["Email", `<a href="mailto:${escapeAttr(enriched.email)}">${escapeHTML(enriched.email)}</a>`]);
  if (enriched?.address) rows.push(["Address", enriched.address]);

  return rows
    .filter(([, value]) => value)
    .map(([label, value]) => `<div><dt>${escapeHTML(label)}</dt><dd>${String(value).startsWith("<a ") ? value : escapeHTML(value)}</dd></div>`)
    .join("");
}

const AMENITY_ALIASES = {
  pool: "Swimming pool",
  "swimming pool": "Swimming pool",
  bonfire: "Bonfire",
  campfire: "Bonfire",
  wifi: "WiFi",
  "wi-fi": "WiFi",
  parking: "Parking",
  restaurant: "Restaurant",
  breakfast: "Breakfast",
  spa: "Spa",
  ac: "Air conditioning",
  "air conditioning": "Air conditioning",
};

function amenityList(stay, enriched) {
  const items = [];
  const add = (label) => {
    const clean = AMENITY_ALIASES[String(label).toLowerCase()] || label;
    const key = clean.toLowerCase();
    if (!items.some((item) => item.toLowerCase() === key)) items.push(clean);
  };

  const flags = [
    [stay.pool === "yes" || enriched?.swimming_pool === "yes", "Swimming pool"],
    [stay.campfire === "yes" || enriched?.campfire === "yes", "Bonfire"],
    [stay.food === "included" || enriched?.food_included === "included", "Meals included"],
    [enriched?.amenity_wifi === "yes", "WiFi"],
    [enriched?.amenity_parking === "yes", "Parking"],
    [enriched?.amenity_ac === "yes", "Air conditioning"],
    [enriched?.amenity_restaurant === "yes", "Restaurant"],
    [enriched?.amenity_breakfast === "yes", "Breakfast"],
    [enriched?.amenity_spa === "yes", "Spa"],
  ];
  for (const [on, label] of flags) if (on) add(label);

  for (const raw of String(enriched?.amenities || "").split(";")) {
    const label = titleCase(raw.trim());
    if (label) add(label);
  }
  for (const item of stay.best_for || []) {
    if (item) add(`Best for ${titleCase(item)}`);
  }
  return items.length
    ? items.map((item) => `<li>${escapeHTML(item)}</li>`).join("")
    : `<li>No amenities listed</li>`;
}

function ctaButtons(stay, enriched) {
  const website = safeUrl(stay.website) || safeUrl(enriched?.website);
  const maps = safeUrl(stay.maps_url) || mapsUrl(stay);
  const buttons = [];
  if (website) buttons.push(`<a class="sd-button sd-button--solid" href="${escapeAttr(website)}" target="_blank" rel="noopener">Property site ↗</a>`);
  if (maps) buttons.push(`<a class="sd-button" href="${escapeAttr(maps)}" target="_blank" rel="noopener">Directions ↗</a>`);
  if (enriched?.phone) buttons.push(`<a class="sd-button" href="tel:${escapeAttr(enriched.phone.replace(/[^\d+]/g, ""))}">Call</a>`);
  return buttons.join("");
}

function renderNext(stay, list) {
  const index = list.findIndex((item) => item.slug === stay.slug);
  if (index < 0 || list.length < 2) return;
  const next = list[(index + 1) % list.length];
  els.next.hidden = false;
  els["next-link"].href = `stay.html?slug=${encodeURIComponent(next.slug)}`;
  els["next-label"].textContent = index === list.length - 1 ? "Back to the top" : "Next stay";
  els["next-name"].textContent = next.name;
  els["next-meta"].textContent = [next.town, next.district, next.rating ? `${next.rating.toFixed(1)} ★` : ""]
    .filter(Boolean)
    .join(" / ");
}

/* Experience ------------------------------------------------------------ */

function setupExperience(stay, enriched, list) {
  const main = document.querySelector(".sd-main");
  const group = SETTING_GROUP[stay.setting] || "land";
  main.style.setProperty("--sd-accent", ACCENTS[group] || ACCENTS.land);

  if (els.jumpMeta) {
    els.jumpMeta.textContent = `No. ${String(stay.rank || 0).padStart(2, "0")} / ${stay.district || "Kerala"}`;
  }

  renderMap(stay);
  renderMore(stay, list);
  renderDock(stay, enriched);

  const reveals = main.querySelectorAll(".sd-section, .sd-aside > *, .sd-more, .sd-next");
  reveals.forEach((node, index) => {
    node.classList.add("sd-reveal");
    node.dataset.delay = String(index % 3);
  });
  observeReveals();
  observeSections();

  requestAnimationFrame(() => {
    els.hero?.classList.add("is-ready");
    animateStats();
  });

  prefetchNext();
  updateScroll();
}

function renderMap(stay) {
  if (!Number.isFinite(stay.lat) || !Number.isFinite(stay.lon)) {
    els["location-section"].hidden = true;
    const link = els.jump?.querySelector('[data-jump-link="location"]');
    if (link) link.hidden = true;
    return;
  }
  const pad = 0.02;
  const bbox = [stay.lon - pad, stay.lat - pad, stay.lon + pad, stay.lat + pad]
    .map((value) => value.toFixed(5))
    .join("%2C");
  const src = `https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${stay.lat}%2C${stay.lon}`;
  const maps = safeUrl(stay.maps_url) || mapsUrl(stay);
  els["location-section"].hidden = false;
  els.map.innerHTML = `
    <iframe title="OpenStreetMap location for ${escapeAttr(stay.name)}" src="${escapeAttr(src)}" loading="lazy" referrerpolicy="no-referrer"></iframe>
    ${maps ? `<a class="sd-map-open" href="${escapeAttr(maps)}" target="_blank" rel="noopener">Open in Maps ↗</a>` : ""}`;
}

function renderDock(stay, enriched) {
  const website = safeUrl(stay.website) || safeUrl(enriched?.website);
  const phone = enriched?.phone;
  if (!website && !phone) return;

  els.dock.hidden = false;
  document.body.classList.add("has-dock");
  els["dock-name"].textContent = stay.name;
  els["dock-meta"].textContent = [stay.rating ? `${stay.rating.toFixed(1)} ★` : "", stay.town, stay.district]
    .filter(Boolean)
    .join(" · ");

  const actions = [];
  if (website) actions.push(`<a href="${escapeAttr(website)}" target="_blank" rel="noopener">Website</a>`);
  if (phone) actions.push(`<a href="tel:${escapeAttr(phone.replace(/[^\d+]/g, ""))}">Call</a>`);
  els["dock-actions"].innerHTML = actions.join("");
}

function renderMore(stay, list) {
  const same = list
    .filter((item) => item.slug !== stay.slug && item.district === stay.district)
    .slice(0, 4);
  if (!same.length) {
    els.more.hidden = true;
    return;
  }
  els.more.hidden = false;
  els["more-title"].textContent = `More in ${stay.district}`;
  els["more-list"].innerHTML = same
    .map((item) => `
      <a class="sd-more-row" href="stay.html?slug=${encodeURIComponent(item.slug)}">
        <span class="sd-more-rank">No. ${String(item.rank).padStart(2, "0")}</span>
        <span class="sd-more-name">${escapeHTML(item.name)}</span>
        <span class="sd-more-meta">${escapeHTML(item.town || item.setting_label || "")}${item.rating ? ` / ${item.rating.toFixed(1)} ★` : ""}</span>
        <span class="sd-more-arrow" aria-hidden="true">→</span>
      </a>`)
    .join("");
}

function observeReveals() {
  const nodes = document.querySelectorAll(".sd-reveal");
  if (reduceMotion || !("IntersectionObserver" in window)) {
    nodes.forEach((node) => node.classList.add("is-inview"));
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.classList.add("is-inview");
      observer.unobserve(entry.target);
    }
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.06 });
  nodes.forEach((node) => observer.observe(node));
}

function observeSections() {
  const links = [...document.querySelectorAll("[data-jump-link]")];
  const sections = links
    .map((link) => document.getElementById(link.dataset.jumpLink))
    .filter(Boolean);
  if (!links.length || !sections.length || !("IntersectionObserver" in window)) return;

  const setActive = (id) => {
    links.forEach((link) => {
      if (link.dataset.jumpLink === id) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
  };

  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) setActive(entry.target.id);
    }
  }, { rootMargin: "-32% 0px -58% 0px", threshold: 0 });
  sections.forEach((section) => observer.observe(section));
}

function bindChrome() {
  window.addEventListener("scroll", () => {
    if (scrollTicking) return;
    scrollTicking = true;
    requestAnimationFrame(() => {
      updateScroll();
      scrollTicking = false;
    });
  }, { passive: true });
  window.addEventListener("resize", updateScroll, { passive: true });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-share]")) {
      event.preventDefault();
      shareStay();
    }
  });
}

function updateScroll() {
  const doc = document.documentElement;
  const max = doc.scrollHeight - window.innerHeight;
  if (els.progress) {
    const ratio = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
    els.progress.style.transform = `scaleX(${ratio})`;
  }

  if (els.dock && !els.dock.hidden) {
    const nearFoot = doc.scrollHeight - (window.scrollY + window.innerHeight) < 460;
    const pastHero = (els.hero?.offsetHeight || 640) * 0.72 < window.scrollY;
    els.dock.classList.toggle("is-visible", window.innerWidth <= 760 && pastHero && !nearFoot);
  }

  if (reduceMotion || window.innerWidth <= 1020) return;
  const frame = els["hero-figure"]?.querySelector(".sd-hero-frame");
  const image = els["hero-image"];
  if (!frame || !image || !image.src) return;
  if (frame.classList.contains("is-empty")) return;
  const rect = frame.getBoundingClientRect();
  if (rect.bottom < 0 || rect.top > window.innerHeight) return;
  const shift = Math.max(-26, Math.min(26, -rect.top * 0.055));
  image.style.transform = `translate3d(0, ${shift.toFixed(1)}px, 0) scale(1.055)`;
}

function animateStats() {
  if (reduceMotion) return;
  document.querySelectorAll("[data-count]").forEach((node) => {
    const target = Number(node.dataset.count);
    const decimals = Number(node.dataset.decimals) || 0;
    if (!Number.isFinite(target)) return;
    const duration = target > 1000 ? 1100 : 850;
    const start = performance.now();
    const format = (value) => value.toLocaleString("en-IN", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    const tick = (now) => {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = format(target * eased);
      if (progress < 1) requestAnimationFrame(tick);
      else node.textContent = format(target);
    };
    node.textContent = format(0);
    requestAnimationFrame(tick);
  });
}

function shareStay() {
  const url = window.location.href;
  const title = document.title;
  if (navigator.share && window.matchMedia("(pointer: coarse)").matches) {
    navigator.share({ title, url }).catch(() => {});
    return;
  }
  const done = () => showToast("Link copied — send the file");
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(url).then(done).catch(() => copyFallback(url, done));
  } else {
    copyFallback(url, done);
  }
}

function copyFallback(text, done) {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try { document.execCommand("copy"); done(); } catch { showToast("Copy the address bar link"); }
  area.remove();
}

function showToast(message) {
  if (!els.toast) return;
  clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.hidden = false;
  requestAnimationFrame(() => els.toast.classList.add("is-visible"));
  toastTimer = setTimeout(() => {
    els.toast.classList.remove("is-visible");
    setTimeout(() => { els.toast.hidden = true; }, 420);
  }, 2200);
}

function prefetchNext() {
  const link = els["next-link"];
  if (!link?.href) return;
  const prefetch = document.createElement("link");
  prefetch.rel = "prefetch";
  prefetch.as = "document";
  prefetch.href = link.href;
  document.head.appendChild(prefetch);
}

/* Gallery --------------------------------------------------------------- */

function bindLightbox() {
  if (!els.lightbox) return;
  const figure = els.lightbox.querySelector(".lightbox-figure");
  els.lightboxClose.addEventListener("click", closeGallery);
  els.lightboxPrev.addEventListener("click", () => step(-1));
  els.lightboxNext.addEventListener("click", () => step(1));
  els.lightbox.addEventListener("click", (event) => {
    if (event.target === els.lightbox) closeGallery();
  });
  document.addEventListener("click", (event) => {
    const photo = event.target.closest("[data-photo]");
    if (photo) openGallery(Number(photo.dataset.photo), photo);
    const hero = event.target.closest("[data-hero-photos]");
    if (hero) openGallery(0, hero);
  });
  document.addEventListener("keydown", (event) => {
    if (els.lightbox.hidden) return;
    if (event.key === "Escape") { event.preventDefault(); closeGallery(); }
    if (event.key === "ArrowLeft") { event.preventDefault(); step(-1); }
    if (event.key === "ArrowRight") { event.preventDefault(); step(1); }
    if (event.key === "Tab") trapFocus(event);
  });

  // Pointer swipe. Vertical scrolling stays native.
  let drag = null;
  figure.addEventListener("pointerdown", (event) => {
    if (event.target.closest("a, button")) return;
    event.preventDefault();
    drag = { x: event.clientX, y: event.clientY, id: event.pointerId };
    figure.setPointerCapture?.(event.pointerId);
    figure.classList.add("is-dragging");
  });
  figure.addEventListener("pointerup", (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    drag = null;
    figure.classList.remove("is-dragging");
    if (Math.abs(dx) > 42 && Math.abs(dx) > Math.abs(dy) * 1.4) step(dx < 0 ? 1 : -1);
  });
  figure.addEventListener("pointercancel", () => {
    drag = null;
    figure.classList.remove("is-dragging");
  });
  figure.addEventListener("dragstart", (event) => event.preventDefault());
}

function trapFocus(event) {
  const focusables = [
    els.lightboxClose,
    els.lightboxPrev,
    els.lightboxNext,
    els.lightboxCredit,
  ].filter((node) => node && !node.hidden && node.getClientRects().length);
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function openGallery(index, opener) {
  if (!state.media.length) return;
  state.gallery.items = state.media;
  state.gallery.index = index;
  state.gallery.opener = opener || document.body;
  els.lightbox.hidden = false;
  els.lightbox.setAttribute("aria-hidden", "false");
  document.body.classList.add("lightbox-open");
  paintGallery();
  els.lightboxClose.focus();
}

function paintGallery() {
  const { items, index } = state.gallery;
  const item = items[index];
  if (!item) return;

  els.lightboxImage.classList.add("is-swapping");
  els.lightboxImage.src = item.url;
  els.lightboxImage.alt = item.alt || "Kerala landscape";
  if (els.lightboxImage.decode) {
    els.lightboxImage.decode().catch(() => {}).finally(() => {
      els.lightboxImage.classList.remove("is-swapping");
    });
  } else {
    els.lightboxImage.classList.remove("is-swapping");
  }

  els.lightboxCaption.textContent = item.title || "Kerala landscape";
  els.lightboxCount.textContent = `${index + 1} / ${items.length}`;
  els.lightboxCredit.hidden = !item.page;
  els.lightboxCredit.href = item.page || "#";
  els.lightboxCredit.textContent = `Photo: ${shorten(item.author, 48)} · ${item.license}`;
  els.lightboxPrev.hidden = items.length < 2;
  els.lightboxNext.hidden = items.length < 2;

  document.querySelectorAll("[data-thumb]").forEach((thumb) => {
    const current = Number(thumb.dataset.thumb) === index;
    if (current) thumb.setAttribute("aria-current", "true");
    else thumb.removeAttribute("aria-current");
  });

  // Warm the adjacent photos so stepping feels instant.
  [-1, 1].forEach((offset) => {
    const next = items[(index + offset + items.length) % items.length];
    if (next?.url) {
      const image = new Image();
      image.decoding = "async";
      image.src = next.url;
    }
  });
}

function step(delta) {
  const { items } = state.gallery;
  if (!items.length) return;
  state.gallery.index = (state.gallery.index + delta + items.length) % items.length;
  paintGallery();
}

function closeGallery() {
  if (els.lightbox.hidden) return;
  els.lightbox.hidden = true;
  els.lightbox.setAttribute("aria-hidden", "true");
  document.body.classList.remove("lightbox-open");
  els.lightboxImage.removeAttribute("src");
  const opener = state.gallery.opener;
  if (opener && document.contains(opener)) opener.focus();
}

/* Utilities ------------------------------------------------------------- */

function showError() {
  els.error.hidden = false;
  if (els.hero) els.hero.hidden = true;
  if (els["thumb-strip"]) els["thumb-strip"].hidden = true;
  if (els.jump) els.jump.hidden = true;
  const layout = document.querySelector(".sd-layout");
  if (layout) layout.hidden = true;
  if (els.next) els.next.hidden = true;
  if (els.more) els.more.hidden = true;
  document.querySelector("[data-stay-root]").setAttribute("aria-busy", "false");
  document.title = "Stay not found — Kerala Field Notes";
}

function setCanonical(url) {
  let node = document.querySelector('link[rel="canonical"]');
  if (!node) {
    node = document.createElement("link");
    node.rel = "canonical";
    document.head.appendChild(node);
  }
  node.href = url;
}

function setMeta(name, value) {
  let node = document.querySelector(`meta[name="${name}"]`) || document.querySelector(`meta[property="${name}"]`);
  if (!node) {
    node = document.createElement("meta");
    node.setAttribute(name.startsWith("og:") ? "property" : "name", name);
    document.head.appendChild(node);
  }
  node.setAttribute("content", value);
}

function mapsUrl(stay) {
  if (!Number.isFinite(stay.lat) || !Number.isFinite(stay.lon)) return "";
  return `https://www.google.com/maps/search/?api=1&query=${stay.lat},${stay.lon}`;
}

function cleanDescription(value) {
  if (!value) return "";
  return stripHtml(value).replace(/\s+/g, " ").trim();
}

function sentence(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

function titleCase(value) {
  return String(value || "")
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function stripHtml(value) {
  if (!value) return "";
  return String(value)
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function stripFile(title) {
  return (title || "").replace(/^File:/, "").replace(/\.[a-z0-9]+$/i, "").replace(/_/g, " ");
}

function safeUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value, location.href);
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
