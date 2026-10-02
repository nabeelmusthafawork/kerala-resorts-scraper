import argparse
import os
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__
from .backend import Blocked, make_backend, polite_sleep
from .details import fetch_details
from .export import coverage_report, export_csv
from .models import Place
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="resorts", description="Google Maps resort scraper (Scrapling)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--config", help="path to districts.yaml")
        p.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT, help="checkpoint directory")
        p.add_argument("-o", "--out", default=DEFAULT_OUT, help="output CSV path")
        p.add_argument(
            "--mode", choices=["auto", "http", "scrapling", "urllib", "browser"], default="auto"
        )

    p_run = sub.add_parser("run", help="scrape resorts for a district/locality")
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

    p_em = sub.add_parser("emails", help="harvest public emails from place websites")
    common(p_em)
    p_em.add_argument("--email-workers", type=int, default=12, help="concurrent site crawls")
    p_em.add_argument("--email-delay", type=float, default=0.0, help="delay between requests per site")
    p_em.add_argument("--shard", default="", help="i/n to split work across processes")
    p_em.set_defaults(func=cmd_emails_cmd)

    p_ph = sub.add_parser("phones", help="hunt phone numbers for places that lack one")
    common(p_ph)
    p_ph.add_argument("--workers", type=int, default=8, help="concurrent detail lookups")
    p_ph.add_argument("--delay", type=float, default=0.4, help="base delay between requests (s)")
    p_ph.add_argument("--detail-tries", type=int, default=3, help="max attempts per place")
    p_ph.add_argument("--shard", default="", help="i/n to split work across processes")
    p_ph.add_argument("--clean", action="store_true", help="write a filtered CSV when done")
    p_ph.set_defaults(func=cmd_phones)

    p_st = sub.add_parser("status", help="show checkpoint status")
    common(p_st)
    p_st.set_defaults(func=cmd_status)

    p_ex = sub.add_parser("export", help="export CSV from checkpoints")
    common(p_ex)
    p_ex.add_argument("--clean", action="store_true", help="drop out-of-state, non-lodging and duplicate rows")
    p_ex.set_defaults(func=export_report)

    p_cl = sub.add_parser("clean", help="alias for 'export --clean'")
    common(p_cl)
    p_cl.add_argument("--clean", action="store_true", default=True)
    p_cl.set_defaults(func=export_report)

    p_dr = sub.add_parser("doctor", help="environment check")
    p_dr.add_argument("--probe", action="store_true", help="run a live search probe")
    p_dr.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
