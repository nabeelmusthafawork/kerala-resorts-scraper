import argparse
import csv
import os
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__
from .backend import Blocked, make_backend, polite_sleep
from .clean import dedupe_by_phone
from .details import fetch_details
from .export import coverage_report, export_csv
from .models import CSV_FIELDS, Place
from .planner import Planner, Query
from .store import Store

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONFIG = os.path.join(PROJECT_ROOT, "config", "districts.yaml")
DEFAULT_CHECKPOINT = os.path.join(PROJECT_ROOT, "checkpoints")
DEFAULT_OUT = os.path.join(PROJECT_ROOT, "output", "resorts_report.csv")


def find_config(explicit: str | None) -> str:
    if explicit:
        return explicit
    for cand in (DEFAULT_CONFIG, os.path.join(os.getcwd(), "config", "districts.yaml")):
        if os.path.exists(cand):
            return cand
    sys.exit("config/districts.yaml not found - pass --config")


def get_browser(args):
    from .browser import BrowserBackend

    return BrowserBackend()


def do_search(args, backend, query: str) -> list[dict]:
    if args.mode == "browser":
        return backend.search(query)
    from .search import search_places

    return search_places(backend, query)


def cmd_run(args):
    if args.locality and not args.district:
        sys.exit("--locality needs --district (a locality name alone would be tagged to every district)")
    planner = Planner(find_config(args.config))
    store = Store(args.checkpoint)
    backend = get_browser(args) if args.mode == "browser" else make_backend(args.mode)
    print(f"backend: {getattr(backend, 'name', args.mode)} | places so far: {len(store.places)}")

    queue = deque(planner.initial_queries(args.district, args.locality))
    if args.requeue_caps:
        for rec in store.queries.values():
            if rec.get("status") == "cap_hit":
                seed = Query(text=rec["query"], district=rec["district"], locality=rec["locality"])
                for sub_q in planner.expansion_for(seed, rec.get("count", 60)):
                    queue.append(sub_q)
        print(f"requeued expansion queries for recorded cap hits")
    queued = {q.text for q in queue}
    blocked_streak = 0
    processed = 0

    try:
        while queue:
            if args.max_queries and processed >= args.max_queries:
                print(f"--max-queries {args.max_queries} reached")
                break
            q: Query = queue.popleft()
            if store.query_done(q.text):
                continue
            try:
                raw = do_search(args, backend, q.text)
            except Blocked as e:
                store.record_query(q.text, q.district, q.locality, 0, "blocked")
                blocked_streak += 1
                print(f"[BLOCKED] {q.text}: {e}")
                if blocked_streak >= 3:
                    print("3 consecutive blocks - stopping search phase (rerun to resume)")
                    break
                polite_sleep(args.delay * 3)
                continue

            blocked_streak = 0
            processed += 1
            places = [
                Place.from_dict({**p, "district": q.district, "search_query": q.text}) for p in raw
            ]
            store.save_places(places)

            n = len(raw)
            status = "empty" if n == 0 else ("cap_hit" if n >= 58 else "ok")
            store.record_query(q.text, q.district, q.locality, n, status)
            print(f"[{len(store.places)} places] {q.text} -> {n} ({status})")

            if status == "cap_hit":
                for sub_q in planner.expansion_for(q, n):
                    if sub_q.text not in queued and not store.query_done(sub_q.text):
                        queue.append(sub_q)
                        queued.add(sub_q.text)

            polite_sleep(args.delay)
    except KeyboardInterrupt:
        print("\ninterrupted - progress saved, rerun with same command to resume")
    finally:
        if args.mode == "browser":
            try:
                backend.close()
            except Exception:
                pass

    if not args.no_details:
        enrich(store, args)

    if not args.no_emails:
        from .emailstage import cmd_emails

        cmd_emails(args)

    n = export_csv(store.all_places(), args.out)
    print(f"\nexported {n} places -> {args.out} (raw, unfiltered)")
    print(coverage_report(store.queries))


def enrich(store: Store, args):
    todo = store.missing_details(
        max_tries=args.detail_tries, shard=getattr(args, "shard", "")
    )
    if not todo:
        print("details: nothing to enrich (for this shard)")
        return
    shard = getattr(args, "shard", "")
    print(f"details: enriching {len(todo)} places missing a phone number{f' [shard {shard}]' if shard else ''}...")
    backend = get_browser(args) if args.mode == "browser" else make_backend(args.mode)
    blocked_count = 0
    done = 0

    def work(place: Place) -> str:
        try:
            if args.mode == "browser":
                d = backend.details(place.feature_id)
            else:
                d = fetch_details(backend, place.feature_id)
        except Blocked:
            place.detail_tries += 1
            store.save_place(place)
            return "blocked"
        except Exception as e:
            place.detail_tries += 1
            store.save_place(place)
            print(f"  ! {place.name}: {type(e).__name__}: {e}")
            return "err"

        for key in ("phone", "website", "address", "rating", "review_count", "name", "description"):
            val = d.get(key)
            if val and not getattr(place, key, ""):
                setattr(place, key, val)
        if place.latitude is None and d.get("latitude") is not None:
            place.latitude = d["latitude"]
            place.longitude = d.get("longitude")
        place.detail_tries += 1
        store.save_place(place)
        return "ok"

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(work, p): p for p in todo}
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            if res == "blocked":
                blocked_count += 1
                if blocked_count >= 10:
                    print("too many blocks during details - stopping enrichment (rerun to resume)")
                    ex.shutdown(cancel_futures=True)
                    break
            if done % 25 == 0 or done == len(todo):
                print(f"  {done}/{len(todo)} done")

    if args.mode == "browser":
        try:
            backend.close()
        except Exception:
            pass


def cmd_phones(args):
    store = Store(args.checkpoint)
    enrich(store, args)
    if args.clean:
        from .clean import filter_places, format_report

        places, report = filter_places(store.all_places(), strict=True)
        print()
        print(format_report(report))
        args.out = args.out
        n = export_csv(places, args.out)
        print(f"\nexported {n} places -> {args.out}")


def cmd_emails_cmd(args):
    from .emailstage import cmd_emails

    cmd_emails(args)
    if getattr(args, "shard", "") and not _last_shard(args):
        return
    export_report(args)


def _last_shard(args) -> bool:
    """Only the final shard re-exports; the others would race on one CSV."""
    try:
        return args.shard.split("/")[0] == args.shard.split("/")[1]
    except (AttributeError, IndexError):
        return True


def cmd_status(args):
    store = Store(args.checkpoint)
    print(f"places: {len(store.places)}")
    with_email = sum(1 for p in store.places.values() if p.email)
    print(f"with email: {with_email}")
    with_phone = sum(1 for p in store.places.values() if p.phone)
    with_site = sum(1 for p in store.places.values() if p.website)
    print(f"with phone: {with_phone} | with website: {with_site}")
    print(coverage_report(store.queries))


def export_report(args):
    store = Store(args.checkpoint)
    places = store.all_places()
    if getattr(args, "clean", False):
        from .clean import filter_places, format_report

        places, report = filter_places(places, strict=True)
        print(format_report(report))
    n = export_csv(places, args.out)
    print(f"exported {n} places -> {args.out}")


def cmd_export(args):
    store = Store(args.checkpoint)
    n = export_csv(store.all_places(), args.out)
    print(f"exported {n} places -> {args.out}")


def cmd_rank(args):
    """Rank places best-first, optionally narrowed to one area."""
    from .rank import rank_places

    store = Store(args.checkpoint)
    places = store.all_places()
    if getattr(args, "clean", False):
        from .clean import filter_places

        places, _ = filter_places(places, strict=True)

    # pin and area are OR'd: a row qualifies if it carries the locality's PIN
    # code OR names one of its hamlets. Either is proof of being in the area.
    exact = [args.pin] if args.pin else []
    loose = [a.lower() for a in args.area]
    if exact or loose:
        places = [
            p
            for p in places
            if any(n in (p.address or "") for n in exact)
            or any(n in (p.address or "").lower() for n in loose)
        ]
    if not places:
        sys.exit("nothing matched those filters")

    # one business listed twice keeps one phone number; the surviving row is
    # the better-reviewed listing. See clean.dedupe_by_phone for why this
    # needs both "same number" and "adjacent".
    places, dupes = dedupe_by_phone(places, max_km=args.max_dup_km)
    if dupes:
        print(f"collapsed {dupes} duplicate listing(s) sharing a phone number")

    ordered = [p for _, p in rank_places(places, prior=args.prior)]

    # Sink whole categories to the end without disturbing the score order
    # inside them: a cottage still ranks against other cottages, it just does
    # so below everything else.
    if args.bottom_title:
        sunk = {t.strip().lower() for t in args.bottom_title}
        is_sunk = lambda p: (p.title or "").strip().lower() in sunk  # noqa: E731
        n_sunk = sum(1 for p in ordered if is_sunk(p))
        if n_sunk:
            print(f"moved {n_sunk} {'/'.join(sorted(sunk))} row(s) to the bottom")
        ordered = [p for p in ordered if not is_sunk(p)] + [p for p in ordered if is_sunk(p)]

    rows = [{"rank": i, **p.row()} for i, p in enumerate(ordered, 1)]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["rank"] + CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"ranked {len(rows)} places -> {args.out}")

    top = rows[: args.top] if args.top else rows
    print(f"\n| # | name | rating | reviews | phone |")
    print("|---|---|---|---|---|")
    for r in top:
        # a stray "|" in a business name would break the table
        name = str(r["name"]).replace("|", "/")
        print(
            f"| {r['rank']} | {name[:38]} | {r['rating'] or '-'} | "
            f"{r['review_count'] or '-'} | {r['phone'] or '-'} |"
        )


def cmd_enrich(args):
    from .enrich import main as enrich_main

    argv = []
    if args.inp:
        argv += ["--in", args.inp]
    if args.out:
        argv += ["--out", args.out]
    if args.all:
        argv.append("--all")
    if args.pilot:
        argv += ["--pilot", str(args.pilot)]
    if args.limit:
        argv += ["--limit", str(args.limit)]
    for d in args.district or []:
        argv += ["--district", d]
    argv += ["--workers", str(args.workers)]
    if args.no_crawl:
        argv.append("--no-crawl")
    if args.refresh_sites:
        argv.append("--refresh-sites")
    if args.details:
        argv.append("--details")
    return enrich_main(argv)


def cmd_doctor(args):
    import platform

    print(f"resorts-scraper {__version__}")
    print(f"python {platform.python_version()} on {platform.system()} {platform.machine()}")
    try:
        import scrapling

        ver = getattr(scrapling, "__version__", "?")
        print(f"scrapling: installed ({ver})")
    except Exception:
        print("scrapling: NOT installed -> pip install '.[fetchers]' && scrapling install")
    try:
        from .backend import ScraplingBackend

        b = ScraplingBackend()
        print(f"scrapling fetchers: OK (backend '{b.name}')")
    except Exception as e:
        print(f"scrapling fetchers: unavailable ({e}) -> urllib fallback will be used")
    try:
        from .browser import browser_status

        ok, msg = browser_status()
        print(f"browser mode: {'READY - ' if ok else 'unavailable - '}{msg}")
    except Exception as e:
        print(f"browser mode: unavailable ({e})")

    if args.probe:
        try:
            backend = make_backend("auto")
            from .search import search_places

            places = search_places(backend, "resorts in Kalpetta")
            print(f"live probe: {len(places)} places from 'resorts in Kalpetta'")
            for p in places[:3]:
                print(f"  - {p['name']} | {p.get('title','')} | {p.get('website','')}")
        except Exception as e:
            print(f"live probe: FAILED ({type(e).__name__}: {e})")


def help_text() -> str:
    """The grouped command list printed by `resorts help`."""
    return f"""resorts {__version__} - Google Maps resort scraper

usage: resorts <command> [options]

Scrape
  run        Scrape resorts for a district or locality, with phone and email
             enrichment.
  phones     Hunt phone numbers for places that still lack one.
  emails     Harvest public emails from place websites.

Data
  export     Export a CSV from the checkpoints.
  clean      Alias for 'export --clean': also drop out-of-state, non-lodging
             and duplicate rows.
  rank       Rank places best-first with the Bayesian weighted rating.
  enrich     Add setting, amenities, access and affordability to a clean CSV.

Operations
  status     Show checkpoint status (what is scraped, what is left).
  doctor     Environment and dependency check.
  help       Show this list, or the full options for one command.

Common options (most commands)
  --config PATH        districts.yaml (default: {DEFAULT_CONFIG})
  --checkpoint DIR     checkpoint directory (default: {DEFAULT_CHECKPOINT})
  -o, --out PATH       output CSV path (default: {DEFAULT_OUT})
  --mode MODE          auto | http | scrapling | urllib | browser (default: auto)

Examples
  resorts doctor
      Check the environment and dependencies before a long run.
  resorts run --district Wayanad --workers 3
      Scrape every planned Wayanad query.
  resorts run --district Idukki --locality Munnar
      Scrape one locality only.
  resorts phones --district Idukki --workers 8 --shard 1/3
      Fill missing phone numbers, split across three processes.
  resorts emails --district Wayanad --email-workers 12
      Harvest public emails from place websites.
  resorts status
      How far the checkpoints have got.
  resorts clean -o output/kerala_resorts.csv
      Export a clean CSV from the checkpoints.
  resorts rank --top 25 --prior 50
      Print the best 25 places by weighted rating.
  resorts enrich --in output/kerala_resorts.csv --out output/kerala_resorts_enriched.csv --all
      Enrich the full clean CSV with the 48 fields.
  resorts help rank
      Full option list for one command.

Notes
  * Scraping is polite by default: delays between searches, capped retries.
  * Checkpoints make every run resumable; 'status' shows what is left.
  * Every command also accepts -h/--help for its own options.
"""


def cmd_help(args) -> int | None:
    parsers = getattr(args, "parsers", {}) or {}
    topic = getattr(args, "topic", None)
    if not topic:
        print(help_text().rstrip())
        return 0
    if topic == "help":
        print(parsers["help"].format_help().rstrip() if "help" in parsers else "resorts help [command]")
        return 0
    if topic not in parsers:
        print(f"unknown command: {topic}", file=sys.stderr)
        print("run 'resorts help' for the command list", file=sys.stderr)
        return 2
    print(parsers[topic].format_help().rstrip())
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="resorts",
        description="Google Maps resort scraper (Scrapling)",
        epilog="Run 'resorts help' for the grouped command list, "
        "or 'resorts help <command>' for one command.",
    )
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    parsers: dict[str, argparse.ArgumentParser] = {}

    def add(name: str, **kwargs) -> argparse.ArgumentParser:
        parser = sub.add_parser(name, **kwargs)
        parsers[name] = parser
        return parser

    def common(p):
        p.add_argument("--config", help="path to districts.yaml")
        p.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT, help="checkpoint directory")
        p.add_argument("-o", "--out", default=DEFAULT_OUT, help="output CSV path")
        p.add_argument(
            "--mode", choices=["auto", "http", "scrapling", "urllib", "browser"], default="auto"
        )

    p_run = add("run", help="scrape resorts for a district/locality")
    common(p_run)
    p_run.add_argument("--district", help="e.g. Kozhikode or Wayanad")
    p_run.add_argument("--locality", help="single locality, e.g. Kalpetta")
    p_run.add_argument("--delay", type=float, default=2.0, help="base delay between searches (s)")
    p_run.add_argument("--workers", type=int, default=3, help="concurrent detail lookups")
    p_run.add_argument("--max-queries", type=int, default=0, help="stop after N search queries")
    p_run.add_argument("--detail-tries", type=int, default=3, help="max detail attempts per place")
    p_run.add_argument("--no-details", action="store_true", help="skip phone enrichment")
    p_run.add_argument("--requeue-caps", action="store_true",
                       help="enqueue second-wave queries for already-recorded cap hits")
    p_run.add_argument("--no-emails", action="store_true", help="skip website email harvesting")
    p_run.add_argument("--email-workers", type=int, default=12, help="concurrent site crawls")
    p_run.add_argument("--email-delay", type=float, default=0.0, help="delay between requests per site")
    p_run.set_defaults(func=cmd_run)

    p_em = add("emails", help="harvest public emails from place websites")
    common(p_em)
    p_em.add_argument("--email-workers", type=int, default=12, help="concurrent site crawls")
    p_em.add_argument("--email-delay", type=float, default=0.0, help="delay between requests per site")
    p_em.add_argument("--shard", default="", help="i/n to split work across processes")
    p_em.set_defaults(func=cmd_emails_cmd)

    p_ph = add("phones", help="hunt phone numbers for places that lack one")
    common(p_ph)
    p_ph.add_argument("--workers", type=int, default=8, help="concurrent detail lookups")
    p_ph.add_argument("--delay", type=float, default=0.4, help="base delay between requests (s)")
    p_ph.add_argument("--detail-tries", type=int, default=3, help="max attempts per place")
    p_ph.add_argument("--shard", default="", help="i/n to split work across processes")
    p_ph.add_argument("--clean", action="store_true", help="write a filtered CSV when done")
    p_ph.set_defaults(func=cmd_phones)

    p_st = add("status", help="show checkpoint status")
    common(p_st)
    p_st.set_defaults(func=cmd_status)

    p_ex = add("export", help="export CSV from checkpoints")
    common(p_ex)
    p_ex.add_argument("--clean", action="store_true", help="drop out-of-state, non-lodging and duplicate rows")
    p_ex.set_defaults(func=export_report)

    p_cl = add("clean", help="alias for 'export --clean'")
    common(p_cl)
    p_cl.add_argument("--clean", action="store_true", default=True)
    p_cl.set_defaults(func=export_report)

    p_rk = add("rank", help="rank places best-first (Bayesian weighted rating)")
    common(p_rk)
    p_rk.add_argument("--pin", default="", help="keep addresses containing this PIN code, e.g. 673604")
    p_rk.add_argument("--area", nargs="+", default=[],
                      help="keep addresses containing any of these names, e.g. Kakkadampoyil Nayadampoyil")
    p_rk.add_argument("--prior", type=float, default=50.0,
                      help="reviews a rating needs before it counts fully (default 50)")
    p_rk.add_argument("--max-dup-km", type=float, default=0.2,
                      help="collapse same-phone listings closer than this (default 0.2)")
    p_rk.add_argument("--bottom-title", nargs="+", default=[],
                      metavar="TITLE",
                      help="sink these Google categories to the bottom, e.g. --bottom-title Cottage")
    p_rk.add_argument("--top", type=int, default=25, help="rows to print, 0 for all")
    p_rk.add_argument("--clean", action="store_true", default=True,
                      help="apply the strict Kerala lodging+phone filter first")
    p_rk.set_defaults(func=cmd_rank)

    p_en = add("enrich", help="add setting, amenities, access and affordability to a clean CSV")
    p_en.add_argument("--in", dest="inp", default="output/kerala_resorts.csv", help="input clean CSV")
    p_en.add_argument("--out", default="output/kerala_resorts_enriched.csv", help="output enriched CSV")
    p_en.add_argument("--all", action="store_true", help="process every row")
    p_en.add_argument("--pilot", type=int, default=0, help="process a stratified pilot of N rows")
    p_en.add_argument("--limit", type=int, default=0, help="process the first N rows after filtering")
    p_en.add_argument("--district", action="append", default=[], help="restrict to district (repeatable)")
    p_en.add_argument("--workers", type=int, default=8, help="concurrent website crawls")
    p_en.add_argument("--no-crawl", action="store_true", help="use cached site facts only")
    p_en.add_argument("--refresh-sites", action="store_true", help="ignore cached site facts")
    p_en.add_argument(
        "--details",
        action="store_true",
        help="fetch Google Maps amenity attributes for rows without a usable own website",
    )
    p_en.set_defaults(func=cmd_enrich)

    p_dr = add("doctor", help="environment check")
    p_dr.add_argument("--probe", action="store_true", help="run a live search probe")
    p_dr.set_defaults(func=cmd_doctor)

    p_help = add("help", help="show the grouped command list, or details for one command")
    p_help.add_argument("topic", nargs="?", help="command name, for example 'rank'")
    p_help.set_defaults(func=cmd_help)

    ap.set_defaults(parsers=parsers)
    return ap, parsers


def main(argv=None):
    ap, _ = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
