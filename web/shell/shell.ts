import catalogJson from '../../catalog/catalog.json';
import authorsJson from '../../catalog/authors.json';
import bookCoversJson from '../../catalog/book_covers.json';
import {
  getAll,
  subscribe,
  type Bookmark,
  type ReadingStatus,
  type ReadingStore,
  type WorkReadingState,
} from '../lib/storage';

declare global {
  interface ImportMeta {
    glob(
      pattern: string,
      options: { eager: true; query: string; import: 'default' },
    ): Record<string, string>;
  }
}

type Theme = 'light' | 'dark' | 'sepia';
type GroupMode = 'author' | 'all' | 'language';

type Work = {
  id: string;
  title: string;
  author: string;
  authorKey: string;
  translator: string | null;
  language: string;
  written: string | null;
  shape: string;
  sectionCount: number;
  wordCount: number;
  cover: string | null;
  readerUrl: string;
  tags: string[];
};

type Catalog = {
  version: number;
  generatedAt: string;
  works: Work[];
};

type AuthorFallback = {
  type: string;
  initial: string;
  color: string;
};

type BookCover = {
  cover: string;
  source: string;
  genre: string | null;
  license: string | null;
  licenseUrl: string | null;
  artist: string | null;
  credit: string | null;
  commonsFilePage: string | null;
  attributionRequired: boolean;
  wikipediaTitle: string | null;
};

type AuthorAsset = {
  key: string;
  name: string;
  language: string;
  dates: string | null;
  workCount: number;
  portrait: string | null;
  cover: string | null;
  coverType: string | null;
  commonsFilePage: string | null;
  license: string | null;
  licenseUrl: string | null;
  artist: string | null;
  credit: string | null;
  attributionRequired: boolean;
  fallback: AuthorFallback | null;
  blurb: string | null;
};

type PagefindResult = {
  id: string;
  score?: number;
  data: () => Promise<PagefindResultData>;
};

type PagefindSearchResponse = {
  results: PagefindResult[];
};

type PagefindApi = {
  init?: () => Promise<void> | void;
  search: (query: string) => Promise<PagefindSearchResponse>;
};

type PagefindSubResult = {
  url?: string;
  title?: string;
  excerpt?: string;
};

type PagefindResultData = {
  url: string;
  title?: string;
  excerpt?: string;
  meta?: Record<string, string>;
  sub_results?: PagefindSubResult[];
};

const THEME_KEY = 'library:theme:v1';
const THEMES: Theme[] = ['light', 'dark', 'sepia'];
const THEME_LABELS: Record<Theme, string> = {
  light: 'Light',
  dark: 'Dark',
  sepia: 'Sepia',
};

const catalog = catalogJson as Catalog;
const authorAssets = authorsJson as AuthorAsset[];
const bookCovers = bookCoversJson as Record<string, BookCover>;
const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' });
const works = [...catalog.works].sort((a, b) => collator.compare(a.title, b.title));
const authorByKey = new Map(authorAssets.map((author) => [author.key, author]));
const coverModules = import.meta.glob('../../assets/covers/*.{webp,svg}', {
  eager: true,
  query: '?url',
  import: 'default',
});
const coverUrlByFilename = new Map(
  Object.entries(coverModules).map(([path, url]) => [path.split('/').pop() ?? path, url]),
);
const bookCoverModules = import.meta.glob('../../assets/book-covers/**/*.{webp,svg}', {
  eager: true,
  query: '?url',
  import: 'default',
});
const bookCoverUrlByPath = new Map(
  Object.entries(bookCoverModules).map(([path, url]) => [
    path.split('/assets/book-covers/').pop() ?? path,
    url,
  ]),
);

const searchInput = byId<HTMLInputElement>('library-search');
const themeToggle = byId<HTMLButtonElement>('theme-toggle');
const themeToggleLabel = byId<HTMLSpanElement>('theme-toggle-label');
const bookmarksLink = byId<HTMLAnchorElement>('bookmarks-link');
const authorFilter = byId<HTMLSelectElement>('author-filter');
const languageFilter = byId<HTMLSelectElement>('language-filter');
const searchResults = byId<HTMLElement>('search-results');
const continueRail = byId<HTMLElement>('continue-reading');
const continueList = byId<HTMLElement>('continue-list');
const bookmarkView = byId<HTMLElement>('bookmark-view');
const bookmarkList = byId<HTMLElement>('bookmark-list');
const libraryContent = byId<HTMLElement>('library-content');
const libraryGrid = byId<HTMLElement>('library-grid');
const viewButtons: Record<GroupMode, HTMLButtonElement> = {
  author: byId<HTMLButtonElement>('view-author'),
  all: byId<HTMLButtonElement>('view-all'),
  language: byId<HTMLButtonElement>('view-language'),
};

let readingSnapshot: ReadingStore = getAll();
let groupMode: GroupMode = 'author';
let selectedAuthor = '';
let selectedLanguage = '';
let showBookmarks = window.location.hash === '#bookmarks';
let searchQuery = '';
let searchTimer: number | undefined;
let searchSequence = 0;
let pagefindPromise: Promise<PagefindApi> | null = null;

function byId<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (node === null) {
    throw new Error(`Missing #${id}`);
  }

  return node as T;
}

function make<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className !== undefined) {
    node.className = className;
  }

  if (text !== undefined) {
    node.textContent = text;
  }

  return node;
}

function isTheme(value: string | undefined): value is Theme {
  return value === 'light' || value === 'dark' || value === 'sepia';
}

function getEffectiveTheme(): Theme {
  const explicit = document.documentElement.dataset.theme;
  if (isTheme(explicit)) {
    return explicit;
  }

  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme: Theme, persist: boolean): void {
  document.documentElement.dataset.theme = theme;
  if (persist) {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // localStorage may be unavailable in hardened browser contexts.
    }
  }

  updateThemeButton();
}

function updateThemeButton(): void {
  const theme = getEffectiveTheme();
  themeToggleLabel.textContent = THEME_LABELS[theme];
  themeToggle.setAttribute('aria-label', `Change color theme; current theme is ${THEME_LABELS[theme]}`);
}

function cycleTheme(): void {
  const currentTheme = getEffectiveTheme();
  const index = THEMES.indexOf(currentTheme);
  const nextTheme = THEMES[(index + 1) % THEMES.length];
  applyTheme(nextTheme, true);
}

function formatCount(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function clampProgress(progress: number): number {
  if (!Number.isFinite(progress)) {
    return 0;
  }

  return Math.min(1, Math.max(0, progress));
}

function progressPercent(progress: number): number {
  return Math.round(clampProgress(progress) * 100);
}

function stateFor(work: Work): WorkReadingState | null {
  return readingSnapshot[work.id] ?? null;
}

function statusFor(work: Work): ReadingStatus {
  return stateFor(work)?.status ?? 'unread';
}

function authorFor(work: Work): AuthorAsset {
  return (
    authorByKey.get(work.authorKey) ?? {
      key: work.authorKey,
      name: work.author,
      language: work.language,
      dates: null,
      workCount: 1,
      portrait: null,
      cover: null,
      coverType: null,
      commonsFilePage: null,
      license: null,
      licenseUrl: null,
      artist: null,
      credit: null,
      attributionRequired: false,
      fallback: null,
      blurb: null,
    }
  );
}

function coverUrlFor(author: AuthorAsset): string | null {
  if (author.cover === null) {
    return null;
  }

  const filename = author.cover.split('/').pop();
  if (filename === undefined || filename === '') {
    return author.cover;
  }

  return coverUrlByFilename.get(filename) ?? author.cover;
}

function bookCoverFor(work: Work): BookCover | null {
  return bookCovers[work.id] ?? null;
}

function bookCoverUrlFor(work: Work): string | null {
  const entry = bookCovers[work.id];
  if (entry !== undefined) {
    const key = entry.cover.split('/assets/book-covers/').pop();
    if (key !== undefined && key !== '') {
      const url = bookCoverUrlByPath.get(key);
      if (url !== undefined) {
        return url;
      }
    }
  }

  return coverUrlFor(authorFor(work));
}

function initialFor(author: AuthorAsset): string {
  return author.fallback?.initial ?? (author.name.trim().charAt(0).toUpperCase() || '•');
}

type LinkLocation = { chapter: string; anchor: string };

function locationHref(work: Work, location: LinkLocation | null): string {
  if (location === null) {
    return work.readerUrl;
  }

  const chapter = location.chapter.trim();
  const anchor = location.anchor.trim().replace(/^#/, '');
  const path = chapter === '' ? work.readerUrl : new URL(chapter, new URL(work.readerUrl, window.location.origin)).pathname;
  return anchor === '' ? path : `${path}#${anchor}`;
}

function locationLabel(location: LinkLocation | null): string {
  if (location === null || location.chapter.trim() === '') {
    return 'Saved location';
  }

  return location.chapter
    .replace(/\.html$/i, '')
    .replace(/[-_]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function filteredWorks(): Work[] {
  return works.filter((work) => {
    if (selectedAuthor !== '' && work.authorKey !== selectedAuthor) {
      return false;
    }

    return selectedLanguage === '' || work.language === selectedLanguage;
  });
}

function groupedBy<T extends string>(items: Work[], keyFor: (work: Work) => T): Map<T, Work[]> {
  const groups = new Map<T, Work[]>();
  for (const work of items) {
    const key = keyFor(work);
    const group = groups.get(key);
    if (group === undefined) {
      groups.set(key, [work]);
    } else {
      group.push(work);
    }
  }

  return groups;
}

function setActiveMode(mode: GroupMode): void {
  groupMode = mode;
  for (const [buttonMode, button] of Object.entries(viewButtons) as [GroupMode, HTMLButtonElement][]) {
    const active = buttonMode === mode;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  }

  showBookmarks = false;
  clearHashIfNeeded();
  renderApp();
}

function populateFilters(): void {
  const currentAuthors = [...new Set(works.map((work) => work.authorKey))].sort((a, b) =>
    collator.compare(authorByKey.get(a)?.name ?? a, authorByKey.get(b)?.name ?? b),
  );
  const languages = [...new Set(works.map((work) => work.language))].sort(collator.compare);

  authorFilter.replaceChildren(option('', 'All authors'));
  for (const authorKey of currentAuthors) {
    const author = authorByKey.get(authorKey);
    authorFilter.append(option(authorKey, author?.name ?? authorKey));
  }

  languageFilter.replaceChildren(option('', 'All languages'));
  for (const language of languages) {
    languageFilter.append(option(language, language));
  }
}

function option(value: string, text: string): HTMLOptionElement {
  const node = document.createElement('option');
  node.value = value;
  node.textContent = text;
  return node;
}

function renderApp(): void {
  renderContinueRail();
  renderBookmarks();
  renderShelf();
  updateVisibility();
}

function updateVisibility(): void {
  const hasSearch = searchQuery !== '';
  const readingCount = continueList.childElementCount;
  searchResults.hidden = !hasSearch;
  continueRail.hidden = showBookmarks || hasSearch || readingCount === 0;
  bookmarkView.hidden = !showBookmarks || hasSearch;
  libraryContent.hidden = showBookmarks || hasSearch;
  bookmarksLink.setAttribute('aria-current', showBookmarks ? 'page' : 'false');
}

function renderContinueRail(): void {
  const readingWorks = works
    .map((work) => ({ work, state: stateFor(work) }))
    .filter((entry): entry is { work: Work; state: WorkReadingState } => entry.state?.status === 'reading')
    .sort((a, b) => b.state.updatedAt - a.state.updatedAt);

  continueList.replaceChildren();
  for (const { work, state } of readingWorks) {
    const card = make('article', 'continue-card');
    card.append(createWorkCoverThumb(work, 'continue-card__cover'));

    const body = make('div', 'continue-card__body');
    const heading = make('h3', undefined, work.title);
    const meta = make(
      'p',
      'continue-card__meta',
      `${progressPercent(state.progress)}% · ${locationLabel(state.location)}`,
    );
    const progress = make('div', 'continue-progress');
    progress.setAttribute('aria-label', `${progressPercent(state.progress)} percent read`);
    const progressFill = make('span');
    progressFill.style.width = `${progressPercent(state.progress)}%`;
    progress.append(progressFill);
    const resume = make('a', 'button-link', 'Resume');
    resume.href = locationHref(work, state.location);

    body.append(heading, meta, progress, resume);
    card.append(body);
    continueList.append(card);
  }
}

function renderBookmarks(): void {
  const rows: Array<{ work: Work; bookmark: Bookmark }> = [];
  for (const work of works) {
    const bookmarks = stateFor(work)?.bookmarks ?? [];
    for (const bookmark of bookmarks) {
      rows.push({ work, bookmark });
    }
  }

  rows.sort((a, b) => b.bookmark.createdAt - a.bookmark.createdAt);
  bookmarkList.replaceChildren();

  if (rows.length === 0) {
    const empty = make('div', 'empty-state');
    empty.append(
      make('h3', undefined, 'No bookmarks yet'),
      make('p', undefined, 'Saved passages from the reader will appear here.'),
    );
    bookmarkList.append(empty);
    return;
  }

  const list = make('ol', 'bookmark-items');
  for (const { work, bookmark } of rows) {
    const item = make('li', 'bookmark-item');
    const link = make('a', undefined, work.title);
    link.href = locationHref(work, bookmark);
    const meta = make('p', undefined, `${work.author} · ${locationLabel(bookmark)}`);
    const excerpt = make('blockquote', undefined, bookmark.excerpt || 'Bookmarked location');
    item.append(link, meta, excerpt);
    list.append(item);
  }

  bookmarkList.append(list);
}

function renderShelf(): void {
  const visibleWorks = filteredWorks();
  libraryGrid.className = 'shelf-root';
  libraryGrid.setAttribute('aria-busy', 'false');
  libraryGrid.replaceChildren();

  if (visibleWorks.length === 0) {
    const empty = make('div', 'empty-state');
    empty.append(
      make('h3', undefined, 'No works match these filters'),
      make('p', undefined, 'Clear one of the filters to return to the full shelf.'),
    );
    libraryGrid.append(empty);
    return;
  }

  if (groupMode === 'all') {
    const grid = make('div', 'shelf-grid');
    for (const work of visibleWorks) {
      grid.append(createWorkCell(work));
    }
    libraryGrid.append(grid);
    return;
  }

  if (groupMode === 'language') {
    const groups = [...groupedBy(visibleWorks, (work) => work.language).entries()].sort(([a], [b]) =>
      collator.compare(a, b),
    );
    for (const [language, groupWorks] of groups) {
      groupWorks.sort((a, b) => collator.compare(a.title, b.title));
      libraryGrid.append(createShelfSection(createLanguageSummary(language, groupWorks.length), groupWorks));
    }
    return;
  }

  const groups = [...groupedBy(visibleWorks, (work) => work.authorKey).entries()].sort(([a], [b]) =>
    collator.compare(authorByKey.get(a)?.name ?? a, authorByKey.get(b)?.name ?? b),
  );
  for (const [authorKey, groupWorks] of groups) {
    groupWorks.sort((a, b) => collator.compare(a.title, b.title));
    const author = authorByKey.get(authorKey) ?? authorFor(groupWorks[0]);
    libraryGrid.append(createShelfSection(createAuthorSummary(author, groupWorks.length), groupWorks, author));
  }
}

function createShelfSection(summary: HTMLElement, sectionWorks: Work[], author?: AuthorAsset): HTMLDetailsElement {
  const section = make('details', 'shelf-section');
  section.open = true;
  section.append(summary);

  if (author?.attributionRequired) {
    section.append(createAttribution(author));
  }

  const grid = make('div', 'shelf-grid');
  for (const work of sectionWorks) {
    grid.append(createWorkCell(work));
  }
  section.append(grid);
  return section;
}

function createAuthorSummary(author: AuthorAsset, count: number): HTMLElement {
  const summary = make('summary', 'author-heading');
  summary.append(createCoverThumb(author, 'author-heading__cover'));

  const text = make('span', 'author-heading__text');
  text.append(make('strong', undefined, author.name));
  const meta = make('span', undefined, `${formatCount(count, 'work', 'works')} · ${author.language}`);
  text.append(meta);
  summary.append(text);

  if (author.attributionRequired) {
    const badge = make('span', 'attribution-badge', 'ⓘ credit required');
    badge.title = attributionText(author);
    summary.append(badge);
  }

  return summary;
}

function createLanguageSummary(language: string, count: number): HTMLElement {
  const summary = make('summary', 'language-heading');
  summary.append(make('strong', undefined, language), make('span', undefined, formatCount(count, 'work', 'works')));
  return summary;
}

function createCoverThumb(author: AuthorAsset, className: string): HTMLElement {
  const cover = make('span', className);
  const coverUrl = coverUrlFor(author);
  if (coverUrl !== null) {
    const image = make('img');
    image.src = coverUrl;
    image.alt = '';
    image.loading = 'lazy';
    image.decoding = 'async';
    cover.append(image);
    return cover;
  }

  cover.classList.add('is-monogram');
  cover.style.setProperty('--monogram-bg', author.fallback?.color ?? 'var(--accent)');
  cover.textContent = initialFor(author);
  return cover;
}

function createWorkCoverThumb(work: Work, className: string): HTMLElement {
  const cover = make('span', className);
  const coverUrl = bookCoverUrlFor(work);
  if (coverUrl !== null) {
    const image = make('img');
    image.src = coverUrl;
    image.alt = '';
    image.loading = 'lazy';
    image.decoding = 'async';
    cover.append(image);
    return cover;
  }

  const author = authorFor(work);
  cover.classList.add('is-monogram');
  cover.style.setProperty('--monogram-bg', author.fallback?.color ?? 'var(--accent)');
  cover.textContent = initialFor(author);
  return cover;
}

function createWorkCard(work: Work): HTMLAnchorElement {
  const state = stateFor(work);
  const status = statusFor(work);
  const author = authorFor(work);
  const card = make('a', `book-card book-card--${status}`);
  card.href = work.readerUrl;
  card.dataset.workId = work.id;
  card.setAttribute('aria-label', `${work.title} by ${work.author}; ${statusLabel(status)}`);

  const cover = make('span', 'book-card__cover');
  const coverUrl = bookCoverUrlFor(work);
  if (coverUrl !== null) {
    const image = make('img');
    image.src = coverUrl;
    image.alt = '';
    image.loading = 'lazy';
    image.decoding = 'async';
    cover.append(image);
  } else {
    cover.classList.add('is-monogram');
    cover.style.setProperty('--monogram-bg', author.fallback?.color ?? 'var(--accent)');
    cover.textContent = initialFor(author);
  }

  const scrim = make('span', 'book-card__scrim');
  const title = make('span', 'book-card__title', work.title);
  const authorName = make('span', 'book-card__author', work.author);
  const rule = make('span', 'book-card__rule');
  const statusNode = make('span', `status-label status-label--${status}`, `${statusIcon(status)} ${statusLabel(status)}`);
  const action = make('span', 'book-card__action', status === 'reading' ? 'Resume' : 'Read');
  scrim.append(rule, title, authorName, statusNode, action);
  cover.append(scrim);
  card.append(cover);

  if (status === 'reading' && state !== null) {
    const progress = make('span', 'book-card__progress');
    progress.style.width = `${progressPercent(state.progress)}%`;
    card.append(progress);
  }

  if (status === 'finished') {
    card.append(make('span', 'book-card__check', '✓'));
  }

  return card;
}

function createWorkCell(work: Work): HTMLElement {
  const card = createWorkCard(work);
  const cover = bookCoverFor(work);
  if (cover === null || !cover.attributionRequired) {
    return card;
  }

  const cell = make('figure', 'book-cell');
  cell.append(card, createBookCredit(cover));
  return cell;
}

function createBookCredit(cover: BookCover): HTMLElement {
  const node = make('figcaption', 'book-credit');
  node.append(document.createTextNode('Art: '));
  node.append(document.createTextNode(cover.artist ?? cover.credit ?? 'Wikimedia Commons'));
  if (cover.license !== null) {
    node.append(document.createTextNode(' · '));
    if (cover.licenseUrl !== null) {
      const license = make('a', undefined, cover.license);
      license.href = cover.licenseUrl;
      license.rel = 'noopener noreferrer';
      node.append(license);
    } else {
      node.append(document.createTextNode(cover.license));
    }
  }

  if (cover.commonsFilePage !== null) {
    node.append(document.createTextNode(' · '));
    const source = make('a', undefined, 'source');
    source.href = cover.commonsFilePage;
    source.rel = 'noopener noreferrer';
    node.append(source);
  }

  return node;
}

function statusIcon(status: ReadingStatus): string {
  if (status === 'finished') {
    return '✓';
  }

  if (status === 'reading') {
    return '↗';
  }

  return '○';
}

function statusLabel(status: ReadingStatus): string {
  if (status === 'finished') {
    return 'Finished';
  }

  if (status === 'reading') {
    return 'Reading';
  }

  return 'Unread';
}

function attributionText(author: AuthorAsset): string {
  const credit = author.artist ?? author.credit ?? 'Image source';
  const license = author.license ?? 'license';
  return `${credit} · ${license}`;
}

function createAttribution(author: AuthorAsset): HTMLElement {
  const node = make('p', 'author-credit');
  node.append(document.createTextNode('Image credit: '));
  node.append(document.createTextNode(author.artist ?? author.credit ?? 'Wikimedia Commons'));
  if (author.license !== null) {
    node.append(document.createTextNode(' · '));
    if (author.licenseUrl !== null) {
      const license = make('a', undefined, author.license);
      license.href = author.licenseUrl;
      license.rel = 'noopener noreferrer';
      node.append(license);
    } else {
      node.append(document.createTextNode(author.license));
    }
  }

  if (author.commonsFilePage !== null) {
    node.append(document.createTextNode(' · '));
    const source = make('a', undefined, 'source');
    source.href = author.commonsFilePage;
    source.rel = 'noopener noreferrer';
    node.append(source);
  }

  return node;
}

function showBookmarkView(show: boolean): void {
  showBookmarks = show;
  if (show) {
    history.replaceState(null, '', '#bookmarks');
  } else {
    clearHashIfNeeded();
  }

  renderApp();
}

function clearHashIfNeeded(): void {
  if (window.location.hash === '#bookmarks') {
    history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
  }
}

function scheduleSearch(): void {
  searchQuery = searchInput.value.trim();
  window.clearTimeout(searchTimer);
  searchSequence += 1;
  const sequence = searchSequence;

  if (searchQuery === '') {
    searchResults.replaceChildren();
    renderApp();
    return;
  }

  showBookmarks = false;
  clearHashIfNeeded();
  renderSearchLoading(searchQuery);
  updateVisibility();
  searchTimer = window.setTimeout(() => {
    void runSearch(searchQuery, sequence);
  }, 150);
}

function renderSearchLoading(query: string): void {
  searchResults.replaceChildren();
  const panel = make('div', 'search-panel');
  panel.append(make('p', 'eyebrow', 'Search results'), make('h2', undefined, `Searching “${query}”`));
  const loading = make('p', 'search-status', 'Searching the archive…');
  loading.setAttribute('role', 'status');
  panel.append(loading);
  searchResults.append(panel);
}

async function loadPagefind(): Promise<PagefindApi> {
  const pagefindPath = '/pagefind/pagefind.js';
  pagefindPromise ??= import(/* @vite-ignore */ pagefindPath).then(async (module) => {
    const api = module as PagefindApi;
    if (typeof api.init === 'function') {
      await api.init();
    }

    return api;
  });

  return pagefindPromise;
}

async function runSearch(query: string, sequence: number): Promise<void> {
  try {
    const pagefind = await loadPagefind();
    const response = await pagefind.search(query);
    if (sequence !== searchSequence) {
      return;
    }

    const resultData = await Promise.all(response.results.slice(0, 40).map((result) => result.data()));
    if (sequence !== searchSequence) {
      return;
    }

    renderSearchResults(query, resultData, response.results.length);
  } catch (error) {
    if (sequence !== searchSequence) {
      return;
    }

    renderSearchFailure(error);
  }
}

function renderSearchResults(query: string, results: PagefindResultData[], total: number): void {
  searchResults.replaceChildren();
  const panel = make('div', 'search-panel');
  panel.append(make('p', 'eyebrow', 'Search results'), make('h2', undefined, `Results for “${query}”`));

  if (results.length === 0) {
    panel.append(make('p', 'search-status', 'No matching passages found.'));
    searchResults.append(panel);
    return;
  }

  panel.append(make('p', 'search-status', `${formatCount(total, 'result', 'results')} found; showing the first ${results.length}.`));
  const list = make('ol', 'search-result-list');
  for (const result of results) {
    list.append(createSearchResult(result));
  }

  panel.append(list);
  searchResults.append(panel);
}

function renderSearchFailure(error: unknown): void {
  searchResults.replaceChildren();
  const panel = make('div', 'search-panel');
  panel.append(
    make('p', 'eyebrow', 'Search unavailable'),
    make('h2', undefined, 'The Pagefind index could not be loaded'),
    make(
      'p',
      'search-status',
      error instanceof Error ? error.message : 'Run npx pagefind --site output after building the archive.',
    ),
  );
  searchResults.append(panel);
}

function createSearchResult(result: PagefindResultData): HTMLLIElement {
  const subResult = result.sub_results?.[0];
  const href = subResult?.url ?? result.url;
  const work = workForUrl(href);
  const item = make('li', 'search-result');
  const link = make('a');
  link.href = href;
  link.append(make('span', 'search-result__title', work?.title ?? result.meta?.title ?? result.title ?? 'Untitled result'));
  link.append(make('span', 'search-result__meta', `${work?.author ?? 'Classics Archive'} · ${subResult?.title ?? 'Matched passage'}`));
  const excerpt = make('span', 'search-result__excerpt');
  appendSanitizedExcerpt(excerpt, subResult?.excerpt ?? result.excerpt ?? 'Open this result to read the matching passage.');
  link.append(excerpt);
  item.append(link);
  return item;
}

function workForUrl(url: string): Work | null {
  const path = new URL(url, window.location.origin).pathname;
  for (const work of works) {
    if (path.startsWith(work.readerUrl)) {
      return work;
    }
  }

  return null;
}

function appendSanitizedExcerpt(target: HTMLElement, excerpt: string): void {
  const template = document.createElement('template');
  template.innerHTML = excerpt;

  const appendNode = (source: ChildNode, destination: HTMLElement): void => {
    if (source.nodeType === Node.TEXT_NODE) {
      destination.append(document.createTextNode(source.textContent ?? ''));
      return;
    }

    if (source.nodeType !== Node.ELEMENT_NODE) {
      return;
    }

    const element = source as Element;
    if (element.tagName.toLowerCase() === 'mark') {
      const mark = make('mark');
      for (const child of element.childNodes) {
        appendNode(child, mark);
      }
      destination.append(mark);
      return;
    }

    for (const child of element.childNodes) {
      appendNode(child, destination);
    }
  };

  for (const child of template.content.childNodes) {
    appendNode(child, target);
  }
}

function bindEvents(): void {
  themeToggle.addEventListener('click', cycleTheme);
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', updateThemeButton);
  searchInput.addEventListener('input', scheduleSearch);
  bookmarksLink.addEventListener('click', (event) => {
    event.preventDefault();
    searchInput.value = '';
    searchQuery = '';
    showBookmarkView(!showBookmarks);
  });
  window.addEventListener('hashchange', () => {
    showBookmarks = window.location.hash === '#bookmarks';
    renderApp();
  });

  authorFilter.addEventListener('change', () => {
    selectedAuthor = authorFilter.value;
    showBookmarks = false;
    clearHashIfNeeded();
    renderApp();
  });
  languageFilter.addEventListener('change', () => {
    selectedLanguage = languageFilter.value;
    showBookmarks = false;
    clearHashIfNeeded();
    renderApp();
  });
  viewButtons.author.addEventListener('click', () => setActiveMode('author'));
  viewButtons.all.addEventListener('click', () => setActiveMode('all'));
  viewButtons.language.addEventListener('click', () => setActiveMode('language'));

  subscribe((snapshot) => {
    readingSnapshot = snapshot;
    renderApp();
  });
}

populateFilters();
updateThemeButton();
bindEvents();
requestAnimationFrame(renderApp);
