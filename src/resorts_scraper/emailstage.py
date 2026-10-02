"""Email harvesting stage: fill Place.email for places that have a website.

Separate from the details stage because it hits third-party hosts, not Google,
so its politeness rules and failure handling are independent. State lives in
the same checkpoints JSONL via Store.save_place.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

from .backend import force_ipv4
from .email import EmailCrawler


def cmd_emails(args):
    from .cli import find_config  # noqa: F401  (keeps import graph explicit)
    from .store import Store

    force_ipv4()
    store = Store(args.checkpoint)
    shard = getattr(args, "shard", "")
    todo = store.missing_emails(shard=shard)
    if not todo:
        print("emails: nothing to do for this shard")
        return

    print(f"emails: crawling {len(todo)} resort websites{f' [shard {shard}]' if shard else ''} for public contact addresses...")
    crawler = EmailCrawler(delay=args.email_delay)

    found = 0
    errors: dict[str, int] = {}
    done = 0

    def work(place):
        email, err = crawler.crawl(place.website)
        place.email_tries += 1
        if email:
            place.email = email
        store.save_place(place)
        return email, err

    with ThreadPoolExecutor(max_workers=args.email_workers) as ex:
        futures = {ex.submit(work, p): p for p in todo}
        for fut in as_completed(futures):
            email, err = fut.result()
            done += 1
            if email:
                found += 1
                print(f"  + {futures[fut].name[:44]:46s} {email}")
            elif err:
                errors[err] = errors.get(err, 0) + 1
            if done % 25 == 0 or done == len(todo):
                print(f"  {done}/{len(todo)} sites checked ({found} emails)")

    print(f"\nemails: {found}/{len(todo)} websites yielded an address")
    if errors:
        print("  outcomes: " + "  ".join(f"{k}={v}" for k, v in sorted(errors.items())))
