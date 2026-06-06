.PHONY: all scrape scrape-all portraits build pretext test

all: build

scrape:
	uv run python -m pipeline.crawl --marquee

scrape-all:
	uv run python -m pipeline.crawl --all

portraits:
	uv run python -m pipeline.portraits
	uv run python -m pipeline.covers

build:
	npm run build

pretext:
	uv run python -m pipeline.to_pretext
	for target in $$(uv run python -m pipeline.to_pretext --print-targets); do \
		(cd pretext && uv run pretext build --clean $$target); \
	done

test:
	uv run pytest pipeline/tests
