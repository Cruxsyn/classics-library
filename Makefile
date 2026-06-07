.PHONY: all scrape scrape-all portraits build build\:all build\:changed build\:ci pretext inject dedup-static pagefind test test\:e2e deploy

all: build\:all

scrape:
	uv run python -m pipeline.crawl --marquee

scrape-all:
	uv run python -m pipeline.crawl --all

portraits:
	uv run python -m pipeline.portraits
	uv run python -m pipeline.covers

build:
	npm run build

build\:all:
	uv run python -m pipeline.build

build\:changed:
	uv run python -m pipeline.build --changed-only

build\:ci:
	uv run python -m pipeline.build --limit 4

inject:
	uv run python -m pipeline.inject
	uv run python -m pipeline.dedup_static

dedup-static:
	uv run python -m pipeline.dedup_static

pagefind:
	npx pagefind --site output

pretext:
	uv run python -m pipeline.to_pretext
	for target in $$(uv run python -m pipeline.to_pretext --print-targets); do \
		(cd pretext && uv run pretext build --clean $$target); \
	done

test:
	uv run pytest pipeline/tests
	npm run test

playwright:
	npx playwright test

test\:e2e:
	npx playwright test

# Publish output/ to GitHub Pages (gh-pages branch). Build output/ first, e.g. `make build:all`.
deploy:
	bash scripts/deploy-pages.sh
