/*
 * img — Kerala Field Notes location image resolver (Supabase Edge Function).
 *
 * GET /functions/v1/img?key=<key>&town=<town>&district=<district>&setting=<setting>
 *
 *   1. Look up location_images by image_key. If present, return the stored
 *      public Storage URL and credit. No Commons call.
 *   2. Otherwise query Wikimedia Commons, score candidates, download the best
 *      thumbnail, upload it to the public `locations` bucket, upsert the row,
 *      and return the public URL.
 *
 * No external imports. Talks to PostgREST and Storage over fetch.
 * Responses use Cache-Control: no-store, so nothing is cached on the user end.
 */

const COMMONS_API = "https://commons.wikimedia.org/w/api.php";
const USER_AGENT =
  "KeralaFieldNotes/1.0 (https://github.com/nabeelmusthafawork/kerala-resorts-scraper)";
const BUCKET = "locations";
const EXT_BY_MIME: Record<string, string> = {
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
};

const POSITIVE: Record<string, number> = {
  beach: 6, backwater: 6, lake: 5, river: 4, hill: 5, hills: 5, mountain: 5, mountains: 5,
  forest: 5, tea: 5, plantation: 4, paddy: 3, field: 2, sunset: 4, sunrise: 4, mist: 4,
  valley: 5, view: 4, viewpoint: 4, landscape: 5, nature: 3, canal: 3, houseboat: 5,
  boat: 2, coast: 5, sea: 4, waterfall: 5, reservoir: 2, garden: 2, resort: 2,
  kerala: 1, cliff: 4, rolling: 3, panoramic: 4, scenery: 5, scenic: 5, ridge: 4,
};

const NEGATIVE: Record<string, number> = {
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

const ALLOWED_ORIGINS = [
  "https://nabeelmusthafawork.github.io",
  "https://raw.githack.com",
  "http://127.0.0.1:8765",
  "http://localhost:8765",
];

Deno.serve(async (req: Request) => {
  const origin = req.headers.get("Origin") ?? "";
  const cors = corsHeaders(origin);

  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (req.method !== "GET" && req.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405, cors);
  }

  const url = new URL(req.url);
  if (url.pathname.endsWith("/health")) return json({ ok: true, bucket: BUCKET }, 200, cors);

  const key = sanitizeKey(url.searchParams.get("key"));
  if (!key) return json({ error: "missing_key" }, 400, cors);

  const base = Deno.env.get("SUPABASE_URL");
  const service =
    Deno.env.get("SERVICE_KEY") ??
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ??
    Deno.env.get("SUPABASE_SECRET_KEY");
  if (!base || !service) return json({ error: "function_env_missing" }, 500, cors);

  const stay = {
    town: clean(url.searchParams.get("town")),
    district: clean(url.searchParams.get("district")),
    setting: clean(url.searchParams.get("setting")) || "landscape",
  };

  try {
    const existing = await findStored(base, service, key);
    if (existing) return json({ ...existing, stored: true, source: "storage" }, 200, noStore(cors));

    const pick = await pickCandidate(stay, key);
    if (!pick) return json({ error: "no_candidate" }, 404, cors);

    const asset = await downloadBest(pick.thumburl);
    if (!asset) return json({ error: "download_failed" }, 502, cors);

    const ext = EXT_BY_MIME[asset.mime];
    if (!ext) return json({ error: "unsupported_type", mime: asset.mime }, 415, cors);

    const path = `${key}.${ext}`;
    const uploaded = await upload(base, service, path, asset.bytes, asset.mime);
    if (!uploaded) return json({ error: "upload_failed" }, 502, cors);

    const publicUrl = `${base}/storage/v1/object/public/${BUCKET}/${path}?v=${Date.now()}`;
    const row = {
      image_key: key,
      storage_path: path,
      public_url: publicUrl,
      ext,
      title: pick.meta.title,
      author: pick.meta.author,
      license: pick.meta.license,
      license_url: pick.meta.license_url,
      source_page: pick.meta.page,
      alt: pick.meta.alt,
      width: pick.width ?? null,
      height: pick.height ?? null,
    };
    await upsert(base, service, row);

    return json(
      { url: publicUrl, ...pick.meta, stored: true, source: "commons" },
      200,
      noStore(cors),
    );
  } catch (error) {
    return json(
      { error: "function_error", detail: String((error as Error)?.message ?? error) },
      502,
      cors,
    );
  }
});

function corsHeaders(origin: string) {
  const ok = origin && ALLOWED_ORIGINS.includes(origin);
  return {
    "Access-Control-Allow-Origin": ok ? origin : ALLOWED_ORIGINS[0],
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, apikey",
    "Vary": "Origin",
  };
}

const noStore = (cors: Record<string, string>) => ({ ...cors, "Cache-Control": "no-store" });

function json(body: unknown, status: number, headers: Record<string, string>) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}

function sanitizeKey(value: string | null) {
  const key = String(value ?? "").trim().toLowerCase();
  return /^[a-z0-9][a-z0-9-]{0,119}$/.test(key) ? key : null;
}

const clean = (value: string | null) =>
  String(value ?? "").replace(/[<>"\\]/g, "").trim().slice(0, 80);

// New-style keys (sb_secret_...) are sent in apikey only; a JWT also goes in
// Authorization. Sending a new-style key as a bearer token is rejected.
function authHeaders(key: string) {
  const headers: Record<string, string> = { apikey: key };
  if (key.startsWith("eyJ")) headers.Authorization = `Bearer ${key}`;
  return headers;
}

async function rest(base: string, service: string, path: string, init: RequestInit = {}) {
  return fetch(`${base}/rest/v1/${path}`, {
    ...init,
    headers: {
      ...authHeaders(service),
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
}

async function findStored(base: string, service: string, key: string) {
  const res = await rest(
    base,
    service,
    `location_images?image_key=eq.${encodeURIComponent(key)}&select=*&limit=1`,
  );
  if (!res.ok) return null;
  const rows = await res.json();
  const row = Array.isArray(rows) && rows[0];
  if (!row?.public_url) return null;
  return {
    url: row.public_url,
    page: row.source_page ?? "",
    title: row.title ?? "",
    author: row.author ?? "Wikimedia Commons",
    license: row.license ?? "See source",
    license_url: row.license_url ?? "",
    alt: row.alt ?? "",
  };
}

async function upsert(base: string, service: string, row: Record<string, unknown>) {
  await rest(base, service, "location_images", {
    method: "POST",
    headers: { Prefer: "resolution=merge-duplicates,return=minimal" },
    body: JSON.stringify([row]),
  });
}

async function upload(
  base: string,
  service: string,
  path: string,
  bytes: Uint8Array,
  mime: string,
) {
  const res = await fetch(`${base}/storage/v1/object/${BUCKET}/${path}`, {
    method: "POST",
    headers: {
      ...authHeaders(service),
      "Content-Type": mime,
      "x-upsert": "true",
    },
    body: bytes,
  });
  if (!res.ok) {
    console.error("upload_failed", res.status, (await res.text()).slice(0, 300));
  }
  return res.ok;
}

async function pickCandidate(
  stay: { town: string; district: string; setting: string },
  key: string,
) {
  const town = stay.town || stay.district || "Kerala";
  const setting = stay.setting || "landscape";
  const queries = [
    `${town} Kerala ${setting}`,
    `${stay.district || town} Kerala ${setting}`,
    `Kerala ${setting} landscape`,
  ];

  let pool: Candidate[] = [];
  for (const query of queries) {
    const params = new URLSearchParams({
      action: "query", format: "json", origin: "*", generator: "search",
      gsrsearch: query, gsrnamespace: "6", gsrlimit: "20",
      prop: "imageinfo", iiprop: "url|size|mime|extmetadata", iiurlwidth: "1000",
    });
    const payload = await commons(params);
    const pages = Object.values(payload?.query?.pages ?? {}) as CommonsPage[];
    const candidates = pages
      .map((page) => score(page, stay))
      .filter((c): c is Candidate => Boolean(c))
      .sort((a, b) => b.score - a.score);
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
    width: best.width,
    height: best.height,
    meta: {
      page: best.page, title: best.title, author: best.author,
      license: best.license, license_url: best.license_url, alt: best.alt,
    },
  };
}

type CommonsPage = {
  title?: string;
  imageinfo?: Array<{
    thumburl?: string;
    descriptionurl?: string;
    mime?: string;
    width?: number;
    height?: number;
    extmetadata?: Record<string, { value?: string }>;
  }>;
};

type Candidate = {
  score: number;
  thumburl: string;
  page: string;
  title: string;
  author: string;
  license: string;
  license_url: string;
  alt: string;
  width: number;
  height: number;
};

function score(
  page: CommonsPage,
  stay: { town: string; district: string },
): Candidate | null {
  const info = page?.imageinfo?.[0];
  if (!info?.thumburl) return null;
  if (!info.mime || !EXT_BY_MIME[info.mime]) return null;
  const width = Number(info.width) || 0;
  const height = Number(info.height) || 0;
  if (width < 800 || height < 500 || width / height < 1.08) return null;

  const meta = info.extmetadata ?? {};
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
    page: info.descriptionurl ?? "",
    title: page.title ?? "",
    author: stripHtml(meta.Artist?.value) || "Wikimedia Commons",
    license: stripHtml(meta.LicenseShortName?.value) || "See source",
    license_url: stripHtml(meta.LicenseUrl?.value) || "",
    alt: stripHtml(meta.ImageDescription?.value) || stripFile(page.title),
    width,
    height,
  };
}

async function commons(params: URLSearchParams, attempt = 0): Promise<any> {
  try {
    const res = await fetch(`${COMMONS_API}?${params}`, {
      headers: { "User-Agent": USER_AGENT, Accept: "application/json" },
    });
    const payload = await res.json();
    const code = payload?.error?.code;
    if (code && attempt < 2 && /too-busy|ratelimit|timeout|internal/i.test(code)) {
      await sleep(500 * (attempt + 1));
      return commons(params, attempt + 1);
    }
    return payload;
  } catch {
    if (attempt < 2) {
      await sleep(500 * (attempt + 1));
      return commons(params, attempt + 1);
    }
    return null;
  }
}

async function downloadBest(thumburl: string) {
  if (!thumburl) return null;
  const res = await fetch(thumburl, { headers: { "User-Agent": USER_AGENT } });
  if (!res.ok) return null;
  const mime = (res.headers.get("content-type") ?? "").split(";")[0].trim();
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (!EXT_BY_MIME[mime] || bytes.length < 1024) return null;
  return { mime, bytes };
}

function stripHtml(value?: string) {
  if (!value) return "";
  return String(value)
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#0?39;/g, "'").replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ").trim();
}

const stripFile = (title?: string) =>
  (title ?? "").replace(/^File:/, "").replace(/\.[a-z0-9]+$/i, "").replace(/_/g, " ");

function hash(value: string) {
  let h = 0;
  for (let i = 0; i < value.length; i += 1) h = (h * 31 + value.charCodeAt(i)) >>> 0;
  return h;
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
