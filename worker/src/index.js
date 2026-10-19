/*
 * kfn-images — Kerala Field Notes image resolver.
 *
 * Live behaviour: the browser calls /img?key=...&town=...&setting=...
 *   1. The Worker checks whether docs/assets/locations/<key>.<ext> already
 *      exists in the GitHub repository.
 *   2. If it does, the Worker returns its raw URL and stored credit.
 *   3. If it does not, the Worker queries Wikimedia Commons, scores and picks
 *      the best candidate, downloads the thumbnail, commits it to the repo,
 *      commits a <key>.json credit sidecar, and returns the raw URL.
 *
 * No database. The GitHub repository is the store. The write token lives only
 * in the Worker secret GITHUB_TOKEN and never reaches the browser.
 */

const COMMONS_API = "https://commons.wikimedia.org/w/api.php";
const USER_AGENT = "KeralaFieldNotes/1.0 (https://github.com/nabeelmusthafawork/kerala-resorts-scraper)";
const EXT_BY_MIME = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" };
const KNOWN_EXTS = ["jpg", "png", "webp"];

const POSITIVE = {
  beach: 6, backwater: 6, lake: 5, river: 4, hill: 5, hills: 5, mountain: 5, mountains: 5,
  forest: 5, tea: 5, plantation: 4, paddy: 3, field: 2, sunset: 4, sunrise: 4, mist: 4,
  valley: 5, view: 4, viewpoint: 4, landscape: 5, nature: 3, canal: 3, houseboat: 5,
  boat: 2, coast: 5, sea: 4, waterfall: 5, reservoir: 2, garden: 2, resort: 2,
  kerala: 1, cliff: 4, rolling: 3, panoramic: 4, scenery: 5, scenic: 5, ridge: 4,
};

const NEGATIVE = {
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
  building: 6, house: 4, home: 4, hotel: 2, census: 20, pdf: 20, lawn: 4,
};

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const cors = corsHeaders(origin, env);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }
    if (request.method !== "GET" && request.method !== "POST") {
      return json({ error: "method_not_allowed" }, 405, cors);
    }

    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return json({ ok: true, repo: env.GITHUB_REPO, branch: env.GITHUB_BRANCH }, 200, cors);
    }
    if (url.pathname !== "/img" && url.pathname !== "/") {
      return json({ error: "not_found" }, 404, cors);
    }

    const key = sanitizeKey(url.searchParams.get("key"));
    if (!key) return json({ error: "missing_key" }, 400, cors);
    if (!env.GITHUB_TOKEN) return json({ error: "worker_missing_token" }, 500, cors);

    const stay = {
      town: clean(url.searchParams.get("town")),
      district: clean(url.searchParams.get("district")),
      setting: clean(url.searchParams.get("setting")) || "landscape",
    };

    try {
      const existing = await findExisting(env, key);
      if (existing) {
        return json({ ...existing, stored: true, source: "repo" }, 200, noStore(cors));
      }

      const pick = await pickCandidate(stay, key);
      if (!pick) return json({ error: "no_candidate" }, 404, cors);

      const asset = await downloadBest(pick.thumburl);
      if (!asset) return json({ error: "download_failed" }, 502, cors);

      const ext = EXT_BY_MIME[asset.mime];
      if (!ext) return json({ error: "unsupported_type", mime: asset.mime }, 415, cors);

      const path = `${env.IMAGE_DIR}/${key}.${ext}`;
      const commit = await putFile(env, path, asset.bytes, asset.mime, `Add location image ${key}`);
      const sidecarPath = `${env.IMAGE_DIR}/${key}.json`;
      const credit = { ...pick.meta, ext, commit: commit?.sha || null, fetched_at: new Date().toISOString() };
      await putFile(env, sidecarPath, encodeUtf8(JSON.stringify(credit, null, 2)), "application/json", `Add credit for ${key}`);

      return json(
        { url: rawUrl(env, path, commit?.sha), ...pick.meta, stored: true, source: "commons" },
        200,
        noStore(cors),
      );
    } catch (error) {
      return json({ error: "worker_error", detail: String(error?.message || error) }, 502, cors);
    }
  },
};

function corsHeaders(origin, env) {
  const allowed = String(env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
  const ok = origin && (allowed.includes(origin) || allowed.includes("*"));
  return {
    "Access-Control-Allow-Origin": ok ? origin : allowed[0] || "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

const noStore = (cors) => ({ ...cors, "Cache-Control": "no-store" });

function json(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}

function sanitizeKey(value) {
  const key = String(value || "").trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9-]{0,119}$/.test(key)) return null;
  return key;
}

const clean = (value) => String(value || "").replace(/[<>"\\]/g, "").trim().slice(0, 80);

async function findExisting(env, key) {
  for (const ext of KNOWN_EXTS) {
    const path = `${env.IMAGE_DIR}/${key}.${ext}`;
    const res = await gh(env, "GET", path);
    if (res.ok) {
      const meta = await gh(env, "GET", `${env.IMAGE_DIR}/${key}.json`);
      let credit = {};
      if (meta.ok) {
        const payload = await meta.json();
        credit = decodeContent(payload.content) ? safeJson(atob(payload.content.replace(/\n/g, ""))) : {};
      }
      return { url: rawUrl(env, path, null), ...pickMeta(credit) };
    }
  }
  return null;
}

function pickMeta(credit) {
  return {
    page: credit.page || "",
    title: credit.title || "",
    author: credit.author || "Wikimedia Commons",
    license: credit.license || "See source",
    license_url: credit.license_url || "",
    alt: credit.alt || "",
  };
}

async function pickCandidate(stay, key) {
  const town = stay.town || stay.district || "Kerala";
  const setting = stay.setting || "landscape";
  const queries = [
    `${town} Kerala ${setting}`,
    `${stay.district || town} Kerala ${setting}`,
    `Kerala ${setting} landscape`,
  ];

  let pool = [];
  for (const query of queries) {
    const params = new URLSearchParams({
      action: "query", format: "json", origin: "*", generator: "search",
      gsrsearch: query, gsrnamespace: "6", gsrlimit: "20",
      prop: "imageinfo", iiprop: "url|size|mime|extmetadata", iiurlwidth: "1000",
    });
    const payload = await commons(params);
    const pages = Object.values(payload?.query?.pages || {});
    const candidates = pages.map((page) => score(page, stay)).filter(Boolean).sort((a, b) => b.score - a.score);
    pool = pool.concat(candidates.slice(0, 6));
    if (candidates.length && candidates[0].score >= 9) break;
  }
  if (!pool.length) return null;

  pool.sort((a, b) => b.score - a.score);
  const topScore = pool[0].score;
  const diverse = pool.filter((c) => c.score >= topScore - 1.2).slice(0, 4);
  const best = diverse[hash(key) % diverse.length];
  return {
    thumburl: best.thumburl,
    meta: {
      page: best.page, title: best.title, author: best.author,
      license: best.license, license_url: best.license_url, alt: best.alt,
    },
  };
}

function score(page, stay) {
  const info = page?.imageinfo?.[0];
  if (!info || !info.thumburl) return null;
  if (!EXT_BY_MIME[info.mime]) return null;
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

  let points = 0;
  for (const [word, weight] of Object.entries(POSITIVE)) if (text.includes(word)) points += weight;
  for (const [word, weight] of Object.entries(NEGATIVE)) if (text.includes(word)) points -= weight;
  if (stay.town && text.includes(stay.town.toLowerCase())) points += 5;
  if (stay.district && text.includes(stay.district.toLowerCase())) points += 3;
  points += Math.min(width, 3200) / 1000;
  points += Math.min(width / height, 2);
  if (points < 6) return null;

  return {
    score: points,
    thumburl: info.thumburl,
    page: info.descriptionurl || "",
    title: page.title || "",
    author: stripHtml(meta.Artist?.value) || "Wikimedia Commons",
    license: stripHtml(meta.LicenseShortName?.value) || "See source",
    license_url: stripHtml(meta.LicenseUrl?.value) || "",
    alt: stripHtml(meta.ImageDescription?.value) || stripFile(page.title),
  };
}

async function commons(params, attempt = 0) {
  const url = `${COMMONS_API}?${params.toString()}`;
  try {
    const res = await fetch(url, { headers: { "User-Agent": USER_AGENT, "Accept": "application/json" } });
    const payload = await res.json();
    const code = payload?.error?.code;
    if (code && attempt < 2 && /too-busy|ratelimit|timeout|internal/i.test(code)) {
      await sleep(500 * (attempt + 1));
      return commons(params, attempt + 1);
    }
    return payload;
  } catch (error) {
    if (attempt < 2) {
      await sleep(500 * (attempt + 1));
      return commons(params, attempt + 1);
    }
    return null;
  }
}

async function downloadBest(thumburl) {
  if (!thumburl) return null;
  const res = await fetch(thumburl, { headers: { "User-Agent": USER_AGENT } });
  if (!res.ok) return null;
  const mime = (res.headers.get("content-type") || "").split(";")[0].trim();
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (!EXT_BY_MIME[mime] || bytes.length < 1024) return null;
  return { mime, bytes };
}

async function putFile(env, path, bytes, mime, message) {
  const body = {
    message,
    content: toBase64(bytes),
    branch: env.GITHUB_BRANCH,
  };
  let res = await gh(env, "PUT", path, body);
  if (res.ok) return res.json();

  // 409/422: the file was created by a concurrent request. Re-read and return it.
  if (res.status === 409 || res.status === 422) {
    const existing = await gh(env, "GET", path);
    if (existing.ok) {
      const payload = await existing.json();
      if (payload.sha) {
        res = await gh(env, "PUT", path, { ...body, sha: payload.sha, message: `${message} (retry)` });
        if (res.ok) return res.json();
      }
    }
  }
  return null;
}

async function gh(env, method, path, body) {
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/${path}`;
  return fetch(url, {
    method,
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": USER_AGENT,
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}

function rawUrl(env, path, sha) {
  const suffix = sha ? `?v=${String(sha).slice(0, 7)}` : "";
  return `https://raw.githubusercontent.com/${env.GITHUB_REPO}/${env.GITHUB_BRANCH}/${path}${suffix}`;
}

function stripHtml(value) {
  if (!value) return "";
  return String(value)
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#0?39;/g, "'").replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ").trim();
}

const stripFile = (title) => (title || "").replace(/^File:/, "").replace(/\.[a-z0-9]+$/i, "").replace(/_/g, " ");

function hash(value) {
  let h = 0;
  for (let i = 0; i < value.length; i += 1) h = (h * 31 + value.charCodeAt(i)) >>> 0;
  return h;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const decodeContent = (content) => Boolean(content);
const safeJson = (text) => { try { return JSON.parse(text); } catch { return {}; } };

function encodeUtf8(text) {
  return new TextEncoder().encode(text);
}

function toBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}
