.PHONY: all scrape scrape-all build test

all: build

scrape:
	uv run python -m pipeline.crawl --marquee

scrape-all:
	uv run python -m pipeline.crawl --all

build:
	npm run build

test:
	uv run pytest pipeline/tests
