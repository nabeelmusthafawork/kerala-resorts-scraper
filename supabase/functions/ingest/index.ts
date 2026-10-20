/*
 * ingest — background image harvester for every listing in resorts_enriched.
 *
 * GET/POST /functions/v1/ingest?limit=5&images=2
 *
 *   1. Resets rows stuck in `running` for more than 10 minutes.
 *   2. Claims up to `limit` places from ingest_progress through the
 *      claim_listings RPC (row locks, so parallel runs never overlap).
 *   3. For each place: search Commons, score candidates, and record up to
 *      `images` new photos as URLs in `listing_media` (public_url = the
 *      Commons thumbnail). Nothing is downloaded or uploaded. Duplicates are
 *      skipped by (place_id, commons_id).
 *   4. Marks the place done, empty, or error.
 *
 * Intended caller: pg_cron, once per minute. Manual calls work too.
 * No external imports. Talks to PostgREST over fetch.
 */

const COMMONS_API = "https://commons.wikimedia.org/w/api.php";
const USER_AGENT =
  "KeralaFieldNotes/1.0 (https://github.com/nabeelmusthafawork/kerala-resorts-scraper)";
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

// "urban" and "countryside" are poor Commons search terms. Map them to
// something that returns usable scenery.
const ENV_QUERY: Record<string, string> = {
  urban: "town",
  countryside: "village",
};

Deno.serve(async (req: Request) => {
  const cors = corsHeaders(req.headers.get("Origin") ?? "");
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });

  const url = new URL(req.url);
  const limit = clampInt(url.searchParams.get("limit"), 5, 1, 25);
  const perPlace = clampInt(url.searchParams.get("images"), 2, 1, 6);
  const mode = url.searchParams.get("mode") === "topup" ? "topup" : "forward";

  const base = Deno.env.get("SUPABASE_URL");
  const service =
    Deno.env.get("SERVICE_KEY") ??
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ??
    Deno.env.get("SUPABASE_SECRET_KEY");
  if (!base || !service) return json({ error: "function_env_missing" }, 500, cors);

  const started = Date.now();
  try {
    await resetStale(base, service);

    const claimed = await rpc(base, service, mode === "topup" ? "claim_topup" : "claim_listings", { n: limit });
    const places = Array.isArray(claimed) ? claimed.slice() : [];
    // When the forward queue is empty, spend the remaining request on
    // topping up listings that still have fewer than six images.
    if (mode === "forward" && !places.length) {
      const extra = await rpc(base, service, "claim_topup", { n: limit });
      if (Array.isArray(extra)) places.push(...extra);
    }
    if (!places.length) return json({ ok: true, processed: 0, images: 0, stats: await stats(base, service) }, 200, cors);

    let stored = 0;
    const results = [];

    for (const place of places) {
      try {
        const outcome = await harvest(base, service, place, perPlace);
        stored += outcome.added;
        results.push({ place_id: place.place_id, added: outcome.added, skipped: outcome.skipped });
        await finish(base, service, place.place_id, outcome.added > 0 ? "done" : "empty", outcome.added, null);
      } catch (error) {
        results.push({ place_id: place.place_id, error: String((error as Error)?.message ?? error) });
        await finish(base, service, place.place_id, "error", 0, String((error as Error)?.message ?? error));
      }
    }

    return json(
      { ok: true, mode, processed: places.length, images: stored, ms: Date.now() - started, results, stats: await stats(base, service) },
      200,
      noStore(cors),
    );
  } catch (error) {
    return json({ error: "ingest_error", detail: String((error as Error)?.message ?? error) }, 502, cors);
  }
});

function clampInt(value: string | null, fallback: number, min: number, max: number) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

/* ---------- Supabase access ---------- */

function authHeaders(key: string) {
  const headers: Record<string, string> = { apikey: key };
  if (key.startsWith("eyJ")) headers.Authorization = `Bearer ${key}`;
  return headers;
}

async function rest(base: string, service: string, path: string, init: RequestInit = {}) {
  return fetch(`${base}/rest/v1/${path}`, {
    ...init,
    headers: { ...authHeaders(service), "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
}

async function rpc(base: string, service: string, fn: string, args: unknown) {
  const res = await rest(base, service, `rpc/${fn}`, { method: "POST", body: JSON.stringify(args) });
  if (!res.ok) throw new Error(`rpc ${fn} ${res.status}: ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

async function resetStale(base: string, service: string) {
  const cutoff = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  await rest(base, service, `ingest_progress?status=eq.running&updated_at=lt.${cutoff}`, {
    method: "PATCH",
    headers: { Prefer: "return=minimal" },
    body: JSON.stringify({ status: "error", last_error: "stale claim reset" }),
  });
}

async function finish(
  base: string,
  service: string,
  placeId: string,
  status: string,
  images: number,
  lastError: string | null,
) {
  await rest(base, service, `ingest_progress?place_id=eq.${encodeURIComponent(placeId)}`, {
    method: "PATCH",
    headers: { Prefer: "return=minimal" },
    body: JSON.stringify({ status, images_found: images, last_error: lastError, updated_at: new Date().toISOString() }),
  });
}

async function stats(base: string, service: string) {
  try {
    return await rpc(base, service, "ingest_stats", {});
  } catch {
    return null;
  }
}

/* ---------- harvest one place ---------- */

type Place = {
  place_id: string;
  listing_name?: string;
  district?: string;
  environment_primary?: string;
  nearest_town?: string;
};

async function harvest(base: string, service: string, place: Place, want: number) {
  const existing = await existingIds(base, service, place.place_id);
  const env = String(place.environment_primary || "landscape");
  const envTerm = ENV_QUERY[env] ?? env;
  const town = String(place.nearest_town || place.district || "Kerala");
  const district = String(place.district || "");

  const queries = [
    `${town} Kerala ${envTerm}`,
    district ? `${district} Kerala ${envTerm}` : `Kerala ${envTerm} landscape`,
    `${town} Kerala landscape`,
    `${town} Kerala`,
    district ? `${district} Kerala landscape` : `Kerala ${envTerm} scenery`,
    `Kerala ${envTerm} scenery`,
    `${town} Kerala ${envTerm} view`,
    district ? `${district} Kerala tourism` : "Kerala landscape",
  ];

  const seen = new Map<string, Candidate>();
  for (const query of queries) {
    if (seen.size >= want * 3) break;
    const params = new URLSearchParams({
      action: "query", format: "json", origin: "*", generator: "search",
      gsrsearch: query, gsrnamespace: "6", gsrlimit: "20",
      prop: "imageinfo", iiprop: "url|size|mime|extmetadata", iiurlwidth: "1200",
    });
    const payload = await commons(params);
    const pages = Object.values(payload?.query?.pages ?? {}) as CommonsPage[];
    for (const page of pages) {
      const candidate = score(page, place);
      if (!candidate) continue;
      if (existing.has(candidate.pageid)) continue;
      if (seen.has(candidate.pageid)) continue;
      candidate.query = query;
      seen.set(candidate.pageid, candidate);
    }
  }

  const remaining = Math.max(0, 6 - existing.size);
  const ranked = [...seen.values()]
    .sort((a, b) => b.score - a.score)
    .slice(0, Math.min(want, remaining));

  let added = 0;
  for (const candidate of ranked) {
    const ext = EXT_BY_MIME[candidate.mime];
    if (!ext) continue;

    const ok = await insertMedia(base, service, {
      place_id: place.place_id,
      listing_name: place.listing_name ?? null,
      commons_id: candidate.pageid,
      commons_title: candidate.title,
      storage_path: null,
      public_url: candidate.thumburl,
      ext,
      width: candidate.width,
      height: candidate.height,
      author: candidate.author,
      license: candidate.license,
      license_url: candidate.license_url,
      source_page: candidate.page,
      alt: candidate.alt,
      query: candidate.query,
      score: candidate.score,
      source: "commons",
    });
    if (ok) added += 1;
  }

  return { added, skipped: seen.size - ranked.length };
}

async function existingIds(base: string, service: string, placeId: string) {
  const res = await rest(
    base,
    service,
    `listing_media?place_id=eq.${encodeURIComponent(placeId)}&select=commons_id`,
  );
  if (!res.ok) return new Set<string>();
  const rows = await res.json();
  return new Set<string>((Array.isArray(rows) ? rows : []).map((r: { commons_id: string }) => r.commons_id));
}

async function insertMedia(base: string, service: string, row: Record<string, unknown>) {
  const res = await rest(base, service, "listing_media", {
    method: "POST",
    headers: { Prefer: "resolution=ignore-duplicates,return=minimal" },
    body: JSON.stringify([row]),
  });
  if (!res.ok) console.error("insert_media_failed", res.status, (await res.text()).slice(0, 200));
  return res.ok;
}

/* ---------- Commons ---------- */

type CommonsPage = {
  pageid?: number;
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
  pageid: string;
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
  mime: string;
  query: string;
};

function score(page: CommonsPage, place: Place): Candidate | null {
  const info = page?.imageinfo?.[0];
  if (!info?.thumburl) return null;
  if (!info.mime || !EXT_BY_MIME[info.mime]) return null;
  const width = Number(info.width) || 0;
  const height = Number(info.height) || 0;
  if (width < 900 || height < 600 || width / height < 1.1) return null;

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
  const town = String(place.nearest_town ?? "").toLowerCase();
  const district = String(place.district ?? "").toLowerCase();
  if (town && town.length > 3 && text.includes(town)) points += 5;
  if (district && text.includes(district)) points += 3;
  points += Math.min(width, 3200) / 1000;
  points += Math.min(width / height, 2);
  if (points < 6) return null;

  return {
    pageid: String(page.pageid ?? page.title ?? info.thumburl),
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
    mime: info.mime,
    query: "",
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
      await sleep(700 * (attempt + 1));
      return commons(params, attempt + 1);
    }
    return payload;
  } catch {
    if (attempt < 2) {
      await sleep(700 * (attempt + 1));
      return commons(params, attempt + 1);
    }
    return null;
  }
}

/* ---------- helpers ---------- */

function corsHeaders(origin: string) {
  const allowed = [
    "https://nabeelmusthafawork.github.io",
    "https://raw.githack.com",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
  ];
  const ok = origin && allowed.includes(origin);
  return {
    "Access-Control-Allow-Origin": ok ? origin : allowed[0],
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

function stripHtml(value?: string) {
  if (!value) return "";
  return String(value)
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&#0?39;/g, "'").replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ").trim();
}

const stripFile = (title?: string) =>
  (title ?? "").replace(/^File:/, "").replace(/\.[a-z0-9]+$/i, "").replace(/_/g, " ");

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
