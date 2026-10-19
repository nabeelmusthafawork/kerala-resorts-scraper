# kfn-images Worker

Live image resolver for Kerala Field Notes. It fetches a photo from Wikimedia
Commons, commits it into `docs/assets/locations/` in this repository, and
returns the stored URL. The repository is the only store. There is no database.

## What it does per request

1. `GET /img?key=<key>&town=<town>&district=<district>&setting=<setting>`
2. If `docs/assets/locations/<key>.<ext>` exists in the repo, return its raw URL
   and the stored credit, and stop. No Commons call.
3. Otherwise query Commons, score candidates, download the best thumbnail,
   commit `<key>.<ext>` plus a `<key>.json` credit sidecar, and return the URL.
4. Responses carry `Cache-Control: no-store`, so nothing is cached on the user end.

## One-time setup

You need a free Cloudflare account (email only, no card) and the GitHub token
described in the project notes.

```bash
cd worker
npx wrangler login                 # opens the browser, no token to paste
npx wrangler secret put GITHUB_TOKEN   # paste the fine-grained token here
npx wrangler deploy
```

`wrangler secret put GITHUB_TOKEN` reads from stdin. The token is stored
encrypted as a Worker secret and never appears in this repository.

After deploy, wrangler prints the Worker URL:

```
https://kfn-images.<account>.workers.dev
```

Copy that origin. Then set `WORKER_URL` in `docs/app.js` to it, and there is
nothing else to change.

## Optional KV index

Not required. GitHub handles about 5,000 authenticated requests per hour, which
is far above expected traffic. Add Cloudflare KV only if the existence checks
start to rate-limit.

## Endpoints

| Path      | Purpose                                        |
| --------- | ---------------------------------------------- |
| `/img`    | Resolve, store, and return one location image  |
| `/health` | `{ ok: true, repo, branch }` liveness check    |

## Rollback

Delete the Worker in the Cloudflare dashboard. The committed images stay in the
repository; the site keeps working because the resolver falls back to Commons
when `WORKER_URL` is empty. To undo a bad image, delete its file and its
`<key>.json` sidecar and the next request re-resolves it.
