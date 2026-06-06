import {
  addBookmark,
  get,
  listBookmarks,
  removeBookmark,
  setProgress,
  type Bookmark,
  type ReadingLocation,
  type WorkReadingState,
} from '../lib/storage';

type Theme = 'light' | 'dark' | 'sepia';
type JustifyMode = 'justify' | 'ragged';
type ParagraphStyle = 'indent' | 'block';
type ReaderFont = 'source' | 'spectral' | 'garamond';

type ReaderSettings = {
  sizeIndex: number;
  lineHeightIndex: number;
  justify: JustifyMode;
  paragraphStyle: ParagraphStyle;
  font: ReaderFont;
};

type TocEntry = {
  href: string;
  path: string;
  hash: string;
  id: string;
  label: string;
};

type ReaderElements = {
  progress: HTMLElement;
  progressFill: HTMLElement;
  chrome: HTMLElement;
  tocButton: HTMLButtonElement;
  bookmarkButton: HTMLButtonElement;
  settingsButton: HTMLButtonElement;
  themeButton: HTMLButtonElement;
  themeLabel: HTMLElement;
  overlay: HTMLElement;
  drawer: HTMLElement;
  drawerClose: HTMLButtonElement;
  tocList: HTMLElement;
  bookmarkList: HTMLElement;
  settingsPanel: HTMLElement;
  settingsClose: HTMLButtonElement;
  helpPanel: HTMLElement;
  helpClose: HTMLButtonElement;
  live: HTMLElement;
};

const RUNTIME_MARKER = 'data-reader-runtime';
const THEME_KEY = 'library:theme:v1';
const SETTINGS_KEY = 'library:reader-settings:v1';
const THEMES: readonly Theme[] = ['light', 'dark', 'sepia'];
const THEME_LABELS: Record<Theme, string> = {
  light: 'Light',
  dark: 'Dark',
  sepia: 'Sepia',
};
const SIZE_STEPS = [17, 18, 19, 21, 22] as const;
const LINE_HEIGHT_STEPS = [1.55, 1.7, 1.85] as const;
const LINE_HEIGHT_LABELS = ['Tight', 'Book', 'Open'] as const;
const DEFAULT_SETTINGS: ReaderSettings = {
  sizeIndex: 2,
  lineHeightIndex: 1,
  justify: 'justify',
  paragraphStyle: 'indent',
  font: 'source',
};
const BOOKMARK_ANNOUNCE_MS = 2200;
const SAVE_DEBOUNCE_MS = 1000;

function isTheme(value: string | null | undefined): value is Theme {
  return value === 'light' || value === 'dark' || value === 'sepia';
}

function isReaderFont(value: unknown): value is ReaderFont {
  return value === 'source' || value === 'spectral' || value === 'garamond';
}

function isJustifyMode(value: unknown): value is JustifyMode {
  return value === 'justify' || value === 'ragged';
}

function isParagraphStyle(value: unknown): value is ParagraphStyle {
  return value === 'indent' || value === 'block';
}

function clampIndex(value: unknown, length: number, fallback: number): number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value < length
    ? value
    : fallback;
}

function safe<T>(fn: () => T, fallback: T): T {
  try {
    return fn();
  } catch {
    return fallback;
  }
}

function normalizePath(pathname: string): string {
  const normalized = pathname.replace(/\/index\.html$/, '/');
  return normalized.endsWith('/') ? normalized : normalized;
}

function samePath(a: string, b: string): boolean {
  return normalizePath(a) === normalizePath(b);
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

function makeButton(className: string, label: string, ariaLabel?: string): HTMLButtonElement {
  const button = make('button', className);
  button.type = 'button';
  button.textContent = label;
  if (ariaLabel !== undefined) {
    button.setAttribute('aria-label', ariaLabel);
  }
  return button;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readSettings(): ReaderSettings {
  const raw = safe(() => localStorage.getItem(SETTINGS_KEY), null);
  if (raw === null) {
    return { ...DEFAULT_SETTINGS };
  }

  const parsed = safe<unknown>(() => JSON.parse(raw), null);
  if (!isRecord(parsed)) {
    return { ...DEFAULT_SETTINGS };
  }

  return {
    sizeIndex: clampIndex(parsed.sizeIndex, SIZE_STEPS.length, DEFAULT_SETTINGS.sizeIndex),
    lineHeightIndex: clampIndex(
      parsed.lineHeightIndex,
      LINE_HEIGHT_STEPS.length,
      DEFAULT_SETTINGS.lineHeightIndex,
    ),
    justify: isJustifyMode(parsed.justify) ? parsed.justify : DEFAULT_SETTINGS.justify,
    paragraphStyle: isParagraphStyle(parsed.paragraphStyle)
      ? parsed.paragraphStyle
      : DEFAULT_SETTINGS.paragraphStyle,
    font: isReaderFont(parsed.font) ? parsed.font : DEFAULT_SETTINGS.font,
  };
}

function writeSettings(settings: ReaderSettings): void {
  safe(() => localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)), undefined);
}

function getInitialTheme(): Theme {
  const stored = safe(() => localStorage.getItem(THEME_KEY), null);
  if (isTheme(stored)) {
    return stored;
  }

  const explicit = document.documentElement.dataset.theme;
  if (isTheme(explicit)) {
    return explicit;
  }

  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function deriveBookId(): string | null {
  const explicit = document.documentElement.dataset.readerBook ?? document.body?.dataset.readerBook;
  if (explicit !== undefined && explicit.trim() !== '') {
    return explicit.trim();
  }

  const parts = window.location.pathname.split('/').filter(Boolean).map(decodeURIComponent);
  if (parts.length >= 2 && document.body?.classList.contains('pretext')) {
    return `${parts[0]}/${parts[1]}`;
  }

  return null;
}

function getBookTitle(): string {
  const explicit = document.documentElement.dataset.readerTitle ?? document.body?.dataset.readerTitle;
  if (explicit !== undefined && explicit.trim() !== '') {
    return explicit.trim();
  }

  const meta = document.querySelector<HTMLMetaElement>('meta[property="book:title"]');
  if (meta?.content.trim()) {
    return meta.content.trim();
  }

  const masthead = document.querySelector<HTMLElement>('#ptx-masthead h1 .title, #ptx-masthead h1');
  if (masthead?.textContent?.trim()) {
    return masthead.textContent.trim();
  }

  return document.title.trim() || 'Reader';
}

function getChapterTitle(): string {
  const heading = document.querySelector<HTMLElement>('#ptx-content h2.heading, #ptx-content h1.heading');
  const title = heading?.querySelector<HTMLElement>('.title')?.textContent?.trim();
  if (title !== undefined && title !== '') {
    return title;
  }
  return document.title.trim();
}

function prepareVerseAnchors(): void {
  const poems = Array.from(document.querySelectorAll<HTMLElement>('#ptx-content article.poem'));
  for (const poem of poems) {
    const poemId = poem.id || `reader-poem-${poems.indexOf(poem) + 1}`;
    if (poem.id === '') {
      poem.id = poemId;
    }

    const lines = Array.from(poem.querySelectorAll<HTMLElement>('div.line'));
    for (const [index, line] of lines.entries()) {
      const lineNumber = index + 1;
      if (line.id === '') {
        line.id = `${poemId}-line-${lineNumber}`;
      }
      if (lineNumber % 5 === 0) {
        line.dataset.readerLineNumber = String(lineNumber);
      }
    }
  }
}

function collectReadableElements(): HTMLElement[] {
  prepareVerseAnchors();
  const nodes = Array.from(
    document.querySelectorAll<HTMLElement>('#ptx-content div.para[id], #ptx-content div.line[id]'),
  );
  if (nodes.length > 0) {
    return nodes;
  }
  return Array.from(document.querySelectorAll<HTMLElement>('#ptx-content article.poem[id]'));
}

function collectTocEntries(): TocEntry[] {
  const seen = new Set<string>();
  const links = Array.from(document.querySelectorAll<HTMLAnchorElement>('#ptx-toc a[href]'));
  const entries: TocEntry[] = [];

  for (const link of links) {
    const label = link.textContent?.replace(/\s+/g, ' ').trim() ?? '';
    if (label === '') {
      continue;
    }

    const url = new URL(link.getAttribute('href') ?? '', window.location.href);
    const key = `${url.pathname}${url.hash}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);

    const fileId = url.pathname.split('/').pop()?.replace(/\.html$/, '') ?? '';
    const hashId = url.hash.startsWith('#') ? decodeURIComponent(url.hash.slice(1)) : '';
    entries.push({
      href: url.href,
      path: normalizePath(url.pathname),
      hash: url.hash,
      id: hashId || fileId,
      label,
    });
  }

  return entries;
}

function currentChapterIdFor(element: HTMLElement | null): string {
  const section = element?.closest<HTMLElement>('section.chapter[id], section.section[id], section.book[id]');
  if (section?.id) {
    return section.id;
  }

  const active = document.querySelector<HTMLAnchorElement>('#ptx-toc .active a[href], #ptx-toc a.active[href]');
  if (active !== null) {
    const url = new URL(active.getAttribute('href') ?? '', window.location.href);
    return url.hash.startsWith('#') ? decodeURIComponent(url.hash.slice(1)) : url.pathname.split('/').pop()?.replace(/\.html$/, '') ?? '';
  }

  return window.location.pathname.split('/').pop()?.replace(/\.html$/, '') || 'index';
}

function currentChapterIndex(tocEntries: TocEntry[], chapterId: string): number {
  const currentPath = normalizePath(window.location.pathname);
  const direct = tocEntries.findIndex((entry) => samePath(entry.path, currentPath));
  if (direct >= 0) {
    return direct;
  }

  const byId = tocEntries.findIndex((entry) => entry.id === chapterId);
  return byId >= 0 ? byId : 0;
}

function elementIndex(elements: HTMLElement[], element: HTMLElement | null): number {
  if (element === null) {
    return 0;
  }
  const index = elements.indexOf(element);
  return index >= 0 ? index : 0;
}

function pageScrollFraction(): number {
  const root = document.documentElement;
  const max = Math.max(1, root.scrollHeight - window.innerHeight);
  return Math.min(1, Math.max(0, window.scrollY / max));
}

function progressFor(
  tocEntries: TocEntry[],
  readableElements: HTMLElement[],
  element: HTMLElement | null,
): number {
  const chapterId = currentChapterIdFor(element);
  const totalChapters = Math.max(1, tocEntries.length);
  const chapterIndex = Math.min(totalChapters - 1, Math.max(0, currentChapterIndex(tocEntries, chapterId)));
  const intra = readableElements.length > 1
    ? elementIndex(readableElements, element) / (readableElements.length - 1)
    : pageScrollFraction();

  return Math.min(1, Math.max(0, (chapterIndex + intra) / totalChapters));
}

function textExcerpt(element: HTMLElement): string {
  return (element.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 80);
}

function hrefForBookmark(bookmark: Bookmark, tocEntries: TocEntry[]): string {
  const currentPath = normalizePath(window.location.pathname);
  const matched = tocEntries.find((entry) => entry.id === bookmark.chapter)
    ?? tocEntries.find((entry) => entry.path.endsWith(`/${bookmark.chapter}.html`));

  if (matched !== undefined) {
    const url = new URL(matched.href);
    url.hash = bookmark.anchor;
    return url.href;
  }

  const currentUrl = new URL(window.location.href);
  currentUrl.pathname = currentPath;
  currentUrl.hash = bookmark.anchor;
  return currentUrl.href;
}

function isSameDocumentHref(href: string): boolean {
  const url = new URL(href, window.location.href);
  return url.origin === window.location.origin && samePath(url.pathname, window.location.pathname);
}

function reducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function scrollToAnchor(anchor: string, highlight: boolean): void {
  const target = document.getElementById(anchor);
  if (target === null) {
    return;
  }

  target.scrollIntoView({
    block: 'start',
    behavior: reducedMotion() ? 'auto' : 'smooth',
  });

  if (highlight && !reducedMotion()) {
    target.classList.remove('reader-anchor-pulse');
    window.setTimeout(() => target.classList.add('reader-anchor-pulse'), 20);
    window.setTimeout(() => target.classList.remove('reader-anchor-pulse'), 2100);
  }
}

function focusableWithin(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((node) => node.offsetParent !== null || node === document.activeElement);
}

function setAriaExpanded(button: HTMLButtonElement, expanded: boolean): void {
  button.setAttribute('aria-expanded', String(expanded));
}

function createReaderDom(bookTitle: string, chapterTitle: string): ReaderElements {
  const progress = make('div', 'reader-progress');
  progress.setAttribute('role', 'progressbar');
  progress.setAttribute('aria-label', 'Reading progress');
  progress.setAttribute('aria-valuemin', '0');
  progress.setAttribute('aria-valuemax', '100');
  progress.setAttribute('aria-valuenow', '0');
  const progressFill = make('div', 'reader-progress__fill');
  progress.append(progressFill);

  const chrome = make('header', 'reader-chrome');
  chrome.setAttribute('aria-label', 'Reader controls');
  const chromeInner = make('nav', 'reader-chrome__inner');
  chromeInner.setAttribute('aria-label', 'Reader navigation');

  const nav = make('div', 'reader-chrome__nav');
  const libraryLink = make('a', 'reader-link-button');
  libraryLink.href = '/';
  libraryLink.setAttribute('aria-label', 'Back to library');
  libraryLink.innerHTML = '<span class="reader-button__icon" aria-hidden="true">←</span><span>Library</span>';
  nav.append(libraryLink);

  const title = make('div', 'reader-title');
  title.innerHTML = `<span class="reader-title__book"></span><span class="reader-title__chapter"></span>`;
  const bookNode = title.querySelector<HTMLElement>('.reader-title__book');
  const chapterNode = title.querySelector<HTMLElement>('.reader-title__chapter');
  if (bookNode !== null) {
    bookNode.textContent = bookTitle;
  }
  if (chapterNode !== null) {
    chapterNode.textContent = chapterTitle;
  }

  const actions = make('div', 'reader-chrome__actions');
  const tocButton = makeButton('reader-button', 'TOC', 'Open table of contents');
  tocButton.setAttribute('aria-haspopup', 'dialog');
  tocButton.setAttribute('aria-controls', 'reader-toc-drawer');
  setAriaExpanded(tocButton, false);

  const bookmarkButton = makeButton('reader-button', 'Bookmark', 'Toggle bookmark at current paragraph');
  bookmarkButton.setAttribute('aria-pressed', 'false');

  const settingsButton = makeButton('reader-button', 'Aa', 'Open type settings');
  settingsButton.setAttribute('aria-haspopup', 'dialog');
  settingsButton.setAttribute('aria-controls', 'reader-settings-panel');
  setAriaExpanded(settingsButton, false);

  const themeButton = makeButton('reader-button', '', 'Change color theme');
  const themeLabel = make('span', undefined, 'Theme');
  themeButton.append(make('span', 'reader-button__icon', '◐'), themeLabel);

  actions.append(tocButton, bookmarkButton, settingsButton, themeButton);
  chromeInner.append(nav, title, actions);
  chrome.append(chromeInner);

  const overlay = make('div', 'reader-overlay');
  overlay.hidden = true;

  const drawer = make('aside', 'reader-drawer');
  drawer.id = 'reader-toc-drawer';
  drawer.setAttribute('role', 'dialog');
  drawer.setAttribute('aria-modal', 'true');
  drawer.setAttribute('aria-labelledby', 'reader-toc-title');
  drawer.setAttribute('aria-hidden', 'true');
  drawer.tabIndex = -1;

  const drawerHeader = make('div', 'reader-drawer__header');
  const drawerTitle = make('h2', undefined, 'Contents');
  drawerTitle.id = 'reader-toc-title';
  const drawerClose = makeButton('reader-button', 'Close', 'Close table of contents');
  drawerHeader.append(drawerTitle, drawerClose);

  const drawerBody = make('div', 'reader-drawer__body');
  const tocSection = make('section', 'reader-drawer__section');
  tocSection.setAttribute('aria-labelledby', 'reader-toc-list-title');
  const tocHeading = make('h3', undefined, 'Chapters');
  tocHeading.id = 'reader-toc-list-title';
  const tocList = make('ol', 'reader-toc-list');
  tocSection.append(tocHeading, tocList);

  const bookmarkSection = make('section', 'reader-drawer__section');
  bookmarkSection.setAttribute('aria-labelledby', 'reader-bookmark-list-title');
  const bookmarkHeading = make('h3', undefined, 'Bookmarks');
  bookmarkHeading.id = 'reader-bookmark-list-title';
  const bookmarkList = make('div', 'reader-bookmark-list');
  bookmarkSection.append(bookmarkHeading, bookmarkList);
  drawerBody.append(tocSection, bookmarkSection);
  drawer.append(drawerHeader, drawerBody);

  const settingsPanel = make('section', 'reader-panel');
  settingsPanel.id = 'reader-settings-panel';
  settingsPanel.setAttribute('role', 'dialog');
  settingsPanel.setAttribute('aria-labelledby', 'reader-settings-title');
  settingsPanel.setAttribute('aria-hidden', 'true');
  const settingsHeader = make('div', 'reader-panel__header');
  const settingsTitle = make('h2', undefined, 'Type settings');
  settingsTitle.id = 'reader-settings-title';
  const settingsClose = makeButton('reader-button', 'Close', 'Close type settings');
  settingsHeader.append(settingsTitle, settingsClose);
  settingsPanel.append(settingsHeader, make('div', 'reader-panel__body'));

  const helpPanel = make('section', 'reader-help');
  helpPanel.id = 'reader-help-panel';
  helpPanel.setAttribute('role', 'dialog');
  helpPanel.setAttribute('aria-labelledby', 'reader-help-title');
  helpPanel.setAttribute('aria-hidden', 'true');
  const helpHeader = make('div', 'reader-help__header');
  const helpTitle = make('h2', undefined, 'Keyboard help');
  helpTitle.id = 'reader-help-title';
  const helpClose = makeButton('reader-button', 'Close', 'Close keyboard help');
  helpHeader.append(helpTitle, helpClose);
  const helpBody = make('div', 'reader-help__body');
  helpBody.innerHTML = '<dl><dt>← / →</dt><dd>Previous or next chapter</dd><dt>J / K</dt><dd>Page down or up</dd><dt>Space</dt><dd>Page down</dd><dt>B</dt><dd>Toggle bookmark</dd><dt>T</dt><dd>Open contents</dd><dt>+ / -</dt><dd>Change text size</dd><dt>Esc</dt><dd>Close panels</dd></dl>';
  helpPanel.append(helpHeader, helpBody);

  const live = make('div', 'reader-live');
  live.setAttribute('aria-live', 'polite');
  live.setAttribute('aria-atomic', 'true');

  document.body.prepend(progress, chrome, overlay, drawer, settingsPanel, helpPanel, live);

  return {
    progress,
    progressFill,
    chrome,
    tocButton,
    bookmarkButton,
    settingsButton,
    themeButton,
    themeLabel,
    overlay,
    drawer,
    drawerClose,
    tocList,
    bookmarkList,
    settingsPanel,
    settingsClose,
    helpPanel,
    helpClose,
    live,
  };
}

function applySettings(settings: ReaderSettings): void {
  const root = document.documentElement;
  root.style.setProperty('--reader-size', `${SIZE_STEPS[settings.sizeIndex]}px`);
  root.style.setProperty('--reader-lh', String(LINE_HEIGHT_STEPS[settings.lineHeightIndex]));
  root.dataset.justify = settings.justify === 'ragged' ? 'ragged' : 'justify';
  root.dataset.paragraphStyle = settings.paragraphStyle;
  root.dataset.readerFont = settings.font;
}

function renderSettingsPanel(
  elements: ReaderElements,
  settings: ReaderSettings,
  onSettingsChange: (settings: ReaderSettings) => void,
  currentTheme: () => Theme,
  onThemeChange: (theme: Theme) => void,
): void {
  const body = elements.settingsPanel.querySelector<HTMLElement>('.reader-panel__body');
  if (body === null) {
    return;
  }
  body.innerHTML = '';

  const addSetting = (label: string, controls: HTMLElement): void => {
    const group = make('div', 'reader-setting');
    const groupLabel = make('div', 'reader-setting__label', label);
    group.append(groupLabel, controls);
    body.append(group);
  };

  const sizeRow = make('div', 'reader-step-row');
  for (const [index, size] of SIZE_STEPS.entries()) {
    const button = makeButton('reader-choice', `${size}`);
    button.setAttribute('aria-pressed', String(index === settings.sizeIndex));
    button.addEventListener('click', () => onSettingsChange({ ...settings, sizeIndex: index }));
    sizeRow.append(button);
  }
  addSetting('Size', sizeRow);

  const lineRow = make('div', 'reader-choice-row');
  for (const [index, label] of LINE_HEIGHT_LABELS.entries()) {
    const button = makeButton('reader-choice', label);
    button.setAttribute('aria-pressed', String(index === settings.lineHeightIndex));
    button.addEventListener('click', () => onSettingsChange({ ...settings, lineHeightIndex: index }));
    lineRow.append(button);
  }
  addSetting('Line height', lineRow);

  const themeRow = make('div', 'reader-choice-row');
  for (const theme of THEMES) {
    const button = makeButton('reader-choice', THEME_LABELS[theme]);
    button.setAttribute('aria-pressed', String(theme === currentTheme()));
    button.addEventListener('click', () => onThemeChange(theme));
    themeRow.append(button);
  }
  addSetting('Theme', themeRow);

  const justifyRow = make('div', 'reader-choice-row');
  const justified = makeButton('reader-choice', 'Justified');
  justified.setAttribute('aria-pressed', String(settings.justify === 'justify'));
  justified.addEventListener('click', () => onSettingsChange({ ...settings, justify: 'justify' }));
  const ragged = makeButton('reader-choice', 'Ragged');
  ragged.setAttribute('aria-pressed', String(settings.justify === 'ragged'));
  ragged.addEventListener('click', () => onSettingsChange({ ...settings, justify: 'ragged' }));
  justifyRow.append(justified, ragged);
  addSetting('Alignment', justifyRow);

  const paragraphRow = make('div', 'reader-choice-row');
  const indent = makeButton('reader-choice', 'Indent');
  indent.setAttribute('aria-pressed', String(settings.paragraphStyle === 'indent'));
  indent.addEventListener('click', () => onSettingsChange({ ...settings, paragraphStyle: 'indent' }));
  const block = makeButton('reader-choice', 'Block');
  block.setAttribute('aria-pressed', String(settings.paragraphStyle === 'block'));
  block.addEventListener('click', () => onSettingsChange({ ...settings, paragraphStyle: 'block' }));
  paragraphRow.append(indent, block);
  addSetting('Paragraphs', paragraphRow);

  const fontRow = make('div', 'reader-choice-row');
  const fontChoices: Array<[ReaderFont, string]> = [
    ['source', 'Source Serif'],
    ['spectral', 'Spectral'],
    ['garamond', 'Garamond'],
  ];
  for (const [font, label] of fontChoices) {
    const button = makeButton('reader-choice', label);
    button.setAttribute('aria-pressed', String(settings.font === font));
    button.addEventListener('click', () => onSettingsChange({ ...settings, font }));
    fontRow.append(button);
  }
  addSetting('Typeface', fontRow);
}

function visibleTopElement(visible: Set<HTMLElement>, readableElements: HTMLElement[]): HTMLElement | null {
  const candidates = visible.size > 0 ? Array.from(visible) : readableElements;
  let best: HTMLElement | null = null;
  let bestTop = Number.POSITIVE_INFINITY;

  for (const element of candidates) {
    const rect = element.getBoundingClientRect();
    if (rect.bottom < 64 || rect.top > window.innerHeight) {
      continue;
    }
    const top = Math.abs(rect.top - 88);
    if (top < bestTop) {
      bestTop = top;
      best = element;
    }
  }

  return best ?? readableElements[0] ?? null;
}

function updateProgressUi(elements: ReaderElements, progress: number): void {
  const percent = Math.round(progress * 100);
  elements.progress.style.setProperty('--reader-progress', String(progress));
  elements.progress.setAttribute('aria-valuenow', String(percent));
}

function renderToc(elements: ReaderElements, tocEntries: TocEntry[]): void {
  elements.tocList.innerHTML = '';
  const currentPath = normalizePath(window.location.pathname);

  for (const entry of tocEntries) {
    const item = make('li');
    const link = make('a');
    link.href = entry.href;
    link.textContent = entry.label;
    if (samePath(entry.path, currentPath)) {
      link.setAttribute('aria-current', 'page');
    }
    item.append(link);
    elements.tocList.append(item);
  }
}

function bookmarkForAnchor(bookmarks: Bookmark[], anchor: string): Bookmark | null {
  return bookmarks.find((bookmark) => bookmark.anchor === anchor) ?? null;
}

function markBookmarkAnchors(bookmarks: Bookmark[]): void {
  document
    .querySelectorAll<HTMLElement>('.reader-has-bookmark, .reader-has-bookmark-line')
    .forEach((node) => node.classList.remove('reader-has-bookmark', 'reader-has-bookmark-line'));

  for (const bookmark of bookmarks) {
    const target = document.getElementById(bookmark.anchor);
    if (target === null) {
      continue;
    }
    target.classList.add(target.classList.contains('line') ? 'reader-has-bookmark-line' : 'reader-has-bookmark');
  }
}

function renderBookmarks(
  elements: ReaderElements,
  bookId: string,
  tocEntries: TocEntry[],
  onNavigateCurrentPage: (anchor: string) => void,
  onBookmarksChanged: () => void,
): Bookmark[] {
  const bookmarks = safe(() => listBookmarks(bookId), [] as Bookmark[]).sort((a, b) => b.createdAt - a.createdAt);
  elements.bookmarkList.innerHTML = '';

  if (bookmarks.length === 0) {
    elements.bookmarkList.append(make('p', 'reader-empty', 'No bookmarks yet. Press B while reading to save a place.'));
    markBookmarkAnchors(bookmarks);
    return bookmarks;
  }

  for (const bookmark of bookmarks) {
    const item = make('article', 'reader-bookmark-item');
    const href = hrefForBookmark(bookmark, tocEntries);
    const link = make('a', 'reader-bookmark-link');
    link.href = href;
    const excerpt = make('span', 'reader-bookmark-excerpt', bookmark.excerpt || bookmark.anchor);
    const meta = make('span', 'reader-bookmark-meta', new Date(bookmark.createdAt).toLocaleDateString());
    link.append(excerpt, meta);
    link.addEventListener('click', (event) => {
      if (!isSameDocumentHref(href)) {
        return;
      }
      event.preventDefault();
      onNavigateCurrentPage(bookmark.anchor);
    });

    const note = make('input', 'reader-bookmark-note');
    note.type = 'text';
    note.maxLength = 160;
    note.placeholder = 'Optional note';
    note.value = bookmark.note;
    note.setAttribute('aria-label', `Note for bookmark ${bookmark.excerpt || bookmark.anchor}`);
    note.addEventListener('change', () => {
      safe(
        () => addBookmark(bookId, { ...bookmark, note: note.value.trim() }),
        bookmark,
      );
      onBookmarksChanged();
    });

    const remove = makeButton('reader-button', 'Remove', 'Remove bookmark');
    remove.addEventListener('click', () => {
      safe(() => removeBookmark(bookId, bookmark.id), undefined);
      onBookmarksChanged();
    });

    item.append(link, note, remove);
    elements.bookmarkList.append(item);
  }

  markBookmarkAnchors(bookmarks);
  return bookmarks;
}

function setupReader(): void {
  const bookId = deriveBookId();
  if (bookId === null || !document.body.classList.contains('pretext')) {
    return;
  }

  const root = document.documentElement;
  if (root.hasAttribute(RUNTIME_MARKER)) {
    return;
  }
  root.setAttribute(RUNTIME_MARKER, 'loaded');
  root.dataset.readerBook = bookId;

  const bookTitle = getBookTitle();
  const chapterTitle = getChapterTitle();
  const content = document.querySelector<HTMLElement>('main.ptx-main, #ptx-content');
  if (content !== null) {
    content.setAttribute('role', 'main');
    content.setAttribute('aria-label', bookTitle);
  }

  let settings = readSettings();
  let currentTheme = getInitialTheme();
  let readableElements = collectReadableElements();
  const tocEntries = collectTocEntries();
  const visible = new Set<HTMLElement>();
  const elements = createReaderDom(bookTitle, chapterTitle);
  let currentElement: HTMLElement | null = readableElements[0] ?? null;
  let currentLocation: ReadingLocation | null = currentElement === null
    ? null
    : {
        chapter: currentChapterIdFor(currentElement),
        anchor: currentElement.id,
        offset: Math.round(window.scrollY),
      };
  let currentProgress = 0;
  let saveTimer: number | undefined;
  let liveTimer: number | undefined;
  let lastScrollY = window.scrollY;
  let scrollRaf = 0;
  let previousFocus: HTMLElement | null = null;

  const announce = (message: string): void => {
    if (liveTimer !== undefined) {
      window.clearTimeout(liveTimer);
    }
    elements.live.textContent = message;
    liveTimer = window.setTimeout(() => {
      elements.live.textContent = '';
    }, BOOKMARK_ANNOUNCE_MS);
  };

  const getBookmarks = (): Bookmark[] => safe(() => listBookmarks(bookId), [] as Bookmark[]);

  const refreshBookmarkButton = (): void => {
    if (currentLocation === null) {
      elements.bookmarkButton.setAttribute('aria-pressed', 'false');
      return;
    }
    const bookmarks = getBookmarks();
    elements.bookmarkButton.setAttribute(
      'aria-pressed',
      String(bookmarkForAnchor(bookmarks, currentLocation.anchor) !== null),
    );
    markBookmarkAnchors(bookmarks);
  };

  const saveProgressNow = (withAnnouncement: boolean): void => {
    if (currentLocation === null) {
      return;
    }
    safe(() => setProgress(bookId, currentProgress, currentLocation), undefined);
    if (withAnnouncement) {
      announce('Progress saved');
    }
  };

  const scheduleSave = (): void => {
    if (saveTimer !== undefined) {
      window.clearTimeout(saveTimer);
    }
    saveTimer = window.setTimeout(() => saveProgressNow(false), SAVE_DEBOUNCE_MS);
  };

  const updateCurrentPosition = (): void => {
    currentElement = visibleTopElement(visible, readableElements);
    if (currentElement !== null) {
      currentLocation = {
        chapter: currentChapterIdFor(currentElement),
        anchor: currentElement.id,
        offset: Math.round(window.scrollY),
      };
    }
    currentProgress = progressFor(tocEntries, readableElements, currentElement);
    updateProgressUi(elements, currentProgress);
    refreshBookmarkButton();
    scheduleSave();
  };

  const updateSettings = (next: ReaderSettings): void => {
    settings = next;
    applySettings(settings);
    writeSettings(settings);
    renderSettingsPanel(elements, settings, updateSettings, () => currentTheme, applyThemeAndRender);
  };

  const updateThemeButton = (): void => {
    elements.themeLabel.textContent = THEME_LABELS[currentTheme];
    elements.themeButton.setAttribute(
      'aria-label',
      `Change color theme; current theme is ${THEME_LABELS[currentTheme]}`,
    );
  };

  const applyThemeAndRender = (theme: Theme): void => {
    currentTheme = theme;
    root.dataset.theme = theme;
    safe(() => localStorage.setItem(THEME_KEY, theme), undefined);
    updateThemeButton();
    renderSettingsPanel(elements, settings, updateSettings, () => currentTheme, applyThemeAndRender);
  };

  const cycleTheme = (): void => {
    const index = THEMES.indexOf(currentTheme);
    applyThemeAndRender(THEMES[(index + 1) % THEMES.length]);
  };

  const closeSettings = (): void => {
    elements.settingsPanel.classList.remove('is-open');
    elements.settingsPanel.setAttribute('aria-hidden', 'true');
    setAriaExpanded(elements.settingsButton, false);
  };

  const openSettings = (): void => {
    elements.settingsPanel.classList.add('is-open');
    elements.settingsPanel.setAttribute('aria-hidden', 'false');
    setAriaExpanded(elements.settingsButton, true);
    closeHelp();
  };

  const closeDrawer = (): void => {
    elements.drawer.classList.remove('is-open');
    elements.drawer.setAttribute('aria-hidden', 'true');
    elements.overlay.classList.remove('is-open');
    elements.overlay.hidden = true;
    setAriaExpanded(elements.tocButton, false);
    if (previousFocus !== null) {
      previousFocus.focus();
      previousFocus = null;
    }
  };

  const navigateToBookmark = (anchor: string): void => {
    closeDrawer();
    scrollToAnchor(anchor, true);
  };

  const refreshBookmarks = (withAnnouncement = false): void => {
    renderBookmarks(elements, bookId, tocEntries, navigateToBookmark, () => refreshBookmarks(true));
    refreshBookmarkButton();
    if (withAnnouncement) {
      announce('Bookmark updated');
    }
  };

  const openDrawer = (): void => {
    closeSettings();
    closeHelp();
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : elements.tocButton;
    renderToc(elements, tocEntries);
    refreshBookmarks();
    elements.overlay.hidden = false;
    elements.overlay.classList.add('is-open');
    elements.drawer.classList.add('is-open');
    elements.drawer.setAttribute('aria-hidden', 'false');
    setAriaExpanded(elements.tocButton, true);
    const focusTarget = focusableWithin(elements.drawer)[0] ?? elements.drawer;
    focusTarget.focus();
  };

  const closeHelp = (): void => {
    elements.helpPanel.classList.remove('is-open');
    elements.helpPanel.setAttribute('aria-hidden', 'true');
  };

  const openHelp = (): void => {
    closeSettings();
    elements.helpPanel.classList.add('is-open');
    elements.helpPanel.setAttribute('aria-hidden', 'false');
    elements.helpClose.focus();
  };

  const toggleBookmark = (): void => {
    if (currentElement === null || currentLocation === null) {
      return;
    }
    const anchorElement = currentElement;
    const anchorLocation = currentLocation;

    const bookmarks = getBookmarks();
    const existing = bookmarkForAnchor(bookmarks, anchorLocation.anchor);
    if (existing !== null) {
      safe(() => removeBookmark(bookId, existing.id), undefined);
      announce('Bookmark removed');
    } else {
      const saved = safe(
        () => addBookmark(bookId, {
          chapter: anchorLocation.chapter,
          anchor: anchorLocation.anchor,
          excerpt: textExcerpt(anchorElement),
          note: '',
        }),
        null as Bookmark | null,
      );
      if (saved !== null) {
        announce('Bookmark added');
      }
    }

    refreshBookmarkButton();
    if (elements.drawer.classList.contains('is-open')) {
      refreshBookmarks();
    }
  };

  const pageBy = (direction: 1 | -1): void => {
    window.scrollBy({
      top: direction * window.innerHeight * 0.82,
      behavior: reducedMotion() ? 'auto' : 'smooth',
    });
  };

  const clickNav = (selector: string): void => {
    const link = document.querySelector<HTMLElement>(selector);
    link?.click();
  };

  const changeSizeBy = (delta: 1 | -1): void => {
    const sizeIndex = Math.min(SIZE_STEPS.length - 1, Math.max(0, settings.sizeIndex + delta));
    if (sizeIndex !== settings.sizeIndex) {
      updateSettings({ ...settings, sizeIndex });
    }
  };

  const onKeydown = (event: KeyboardEvent): void => {
    const active = document.activeElement;
    const isTyping = active instanceof HTMLInputElement
      || active instanceof HTMLTextAreaElement
      || active instanceof HTMLSelectElement
      || active instanceof HTMLElement && active.isContentEditable;
    if (isTyping) {
      return;
    }

    if (elements.drawer.classList.contains('is-open') && event.key === 'Tab') {
      const focusable = focusableWithin(elements.drawer);
      if (focusable.length === 0) {
        event.preventDefault();
        elements.drawer.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }

    if (event.key === 'Escape') {
      if (elements.drawer.classList.contains('is-open')) {
        event.preventDefault();
        closeDrawer();
      } else if (elements.settingsPanel.classList.contains('is-open')) {
        event.preventDefault();
        closeSettings();
      } else if (elements.helpPanel.classList.contains('is-open')) {
        event.preventDefault();
        closeHelp();
      }
      return;
    }

    switch (event.key) {
      case 'ArrowLeft':
        event.preventDefault();
        clickNav('.previous-button');
        break;
      case 'ArrowRight':
        event.preventDefault();
        clickNav('.next-button');
        break;
      case 'j':
      case 'J':
      case ' ':
        event.preventDefault();
        pageBy(1);
        break;
      case 'k':
      case 'K':
        event.preventDefault();
        pageBy(-1);
        break;
      case 'b':
      case 'B':
        event.preventDefault();
        toggleBookmark();
        break;
      case 't':
      case 'T':
        event.preventDefault();
        openDrawer();
        break;
      case '+':
      case '=':
        event.preventDefault();
        changeSizeBy(1);
        break;
      case '-':
      case '_':
        event.preventDefault();
        changeSizeBy(-1);
        break;
      case '?':
        event.preventDefault();
        openHelp();
        break;
      default:
        break;
    }
  };

  const onScroll = (): void => {
    if (scrollRaf !== 0) {
      return;
    }
    scrollRaf = window.requestAnimationFrame(() => {
      scrollRaf = 0;
      const nextY = window.scrollY;
      const scrollingDown = nextY > lastScrollY + 12;
      const scrollingUp = nextY < lastScrollY - 8;
      if (!reducedMotion() && !elements.drawer.classList.contains('is-open') && !elements.settingsPanel.classList.contains('is-open')) {
        if (scrollingDown && nextY > 140) {
          elements.chrome.classList.add('is-hidden');
        } else if (scrollingUp || nextY < 80) {
          elements.chrome.classList.remove('is-hidden');
        }
      }
      lastScrollY = nextY;
      updateCurrentPosition();
    });
  };

  const observer = 'IntersectionObserver' in window
    ? new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            const target = entry.target;
            if (!(target instanceof HTMLElement)) {
              continue;
            }
            if (entry.isIntersecting) {
              visible.add(target);
            } else {
              visible.delete(target);
            }
          }
          updateCurrentPosition();
        },
        { root: null, rootMargin: '-15% 0px -55% 0px', threshold: [0, 0.01, 0.25] },
      )
    : null;

  applySettings(settings);
  applyThemeAndRender(currentTheme);
  renderToc(elements, tocEntries);
  refreshBookmarks();
  updateCurrentPosition();

  for (const element of readableElements) {
    observer?.observe(element);
  }

  elements.tocButton.addEventListener('click', () => openDrawer());
  elements.drawerClose.addEventListener('click', () => closeDrawer());
  elements.overlay.addEventListener('click', () => {
    closeDrawer();
    closeHelp();
  });
  elements.bookmarkButton.addEventListener('click', () => toggleBookmark());
  elements.settingsButton.addEventListener('click', () => {
    if (elements.settingsPanel.classList.contains('is-open')) {
      closeSettings();
    } else {
      openSettings();
    }
  });
  elements.settingsClose.addEventListener('click', () => closeSettings());
  elements.themeButton.addEventListener('click', () => cycleTheme());
  elements.helpClose.addEventListener('click', () => closeHelp());
  document.addEventListener('keydown', onKeydown);
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', () => {
    readableElements = collectReadableElements();
    updateCurrentPosition();
  });
  window.addEventListener('pagehide', () => saveProgressNow(false));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      saveProgressNow(false);
    }
  });
  document.addEventListener('pointerdown', () => elements.chrome.classList.remove('is-hidden'), { passive: true });

  if (window.location.hash !== '') {
    window.setTimeout(() => scrollToAnchor(decodeURIComponent(window.location.hash.slice(1)), true), 80);
  } else {
    const saved = safe<WorkReadingState | null>(() => get(bookId), null);
    const anchor = saved?.location?.anchor;
    if (anchor !== undefined && anchor !== '') {
      window.setTimeout(() => {
        scrollToAnchor(anchor, true);
        announce('Resumed from your last saved place');
      }, 180);
    }
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', setupReader, { once: true });
} else {
  setupReader();
}
