# Virtual Library

An elegant, static virtual library for public-domain classics from the Internet Classics Archive. The pipeline normalizes the classics.mit.edu texts, renders each work as a PreTeXt book, wraps every reader page with the shared TypeScript reader runtime, and ships a single-origin bookshelf shell with Pagefind search and local reading progress.

## Architecture

In words: `pipeline.crawl` fetches and normalizes selected works into `data/normalized/` and `catalog/`; `pipeline.portraits` plus `pipeline.covers` enrich `catalog/authors.json` with Wikimedia portrait metadata and local cover art; `pipeline.to_pretext` emits PreTeXt source and targets; PreTeXt renders each book under `output/<Author>/<work>/`; Vite builds the shelf shell into `output/` without deleting the books; `pipeline.inject` self-hosts reader CSS/fonts/runtime, marks PreTeXt prose as the Pagefind body, and strips unused upstream MathJax/Runestone/Google-font references; Pagefind indexes the finished static site.

The production artifact is the plain static directory `output/`.

## Build

Install dependencies once:

```sh
uv sync
npm ci
```

Build the current 18-work marquee library:

```sh
make build:all
```

Fast rebuild after changing normalized text or reader assets:

```sh
make build:changed
```

CI uses a smaller smoke build:

```sh
make build:ci
```

The full 439-work corpus is intentionally not part of the default build. The scale-up path for the later B7 pass is:

```sh
uv run python -m pipeline.build --all
```

## Test

Python and web unit/build checks:

```sh
make test
npm run typecheck
```

End-to-end, accessibility, and visual-regression checks:

```sh
npx playwright install --with-deps chromium
npx playwright test
```

Visual snapshots are committed under `tests-e2e/`. Browser font rasterization can vary by platform; update baselines deliberately with:

```sh
npx playwright test --update-snapshots --grep @visual
```

The Playwright tests wait for network idle and decoded images before screenshots so cover art does not race the baseline capture.

## Deploy

Cloudflare Pages is configured by `wrangler.toml`; no deployment is performed by the build.

```sh
make deploy
```

That runs `npx wrangler pages deploy output`. Any static host can serve the same `output/` directory.

## Data and licensing

Classics text comes from classics.mit.edu / the Internet Classics Archive and is public-domain source material. Author portraits and cover derivatives are resolved from Wikimedia projects when available; attribution, artist, license, Commons, and fallback metadata are preserved in `catalog/authors.json`. Generated cover assets live in `assets/covers/` and local reader fonts live in `assets/fonts/`.

## Privacy

There is no backend. Reading progress, bookmarks, and theme settings stay in browser `localStorage` on the same origin:

- `library:reading:v1`
- `library:theme:v1`
- `library:reader-settings:v1`
