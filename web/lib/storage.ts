export const READING_STORAGE_KEY = 'library:reading:v1' as const;
export const READING_STORAGE_VERSION = 1 as const;

const VERSION_FIELD = '__version';
const PROBE_KEY = `${READING_STORAGE_KEY}:probe`;
const VALID_STATUSES = new Set<ReadingStatus>(['unread', 'reading', 'finished']);

export type ReadingStatus = 'unread' | 'reading' | 'finished';

export type ReadingLocation = {
  chapter: string;
  anchor: string;
  offset: number;
};

export type Bookmark = {
  id: string;
  chapter: string;
  anchor: string;
  excerpt: string;
  note: string;
  createdAt: number;
};

export type BookmarkInput = Omit<Bookmark, 'id' | 'createdAt'> & Partial<Pick<Bookmark, 'id' | 'createdAt'>>;

export type WorkReadingState = {
  status: ReadingStatus;
  progress: number;
  location: ReadingLocation | null;
  updatedAt: number;
  bookmarks: Bookmark[];
};

export type ReadingStore = Record<string, WorkReadingState>;
export type ReadingStorageSubscriber = (snapshot: ReadingStore) => void;

let memoryStore: ReadingStore = {};
let storageAvailable: boolean | undefined;
let quietFallback = false;

const subscribers = new Set<ReadingStorageSubscriber>();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function clampProgress(value: unknown): number {
  const progress = typeof value === 'number' ? value : Number(value);

  if (!Number.isFinite(progress) || progress <= 0) {
    return 0;
  }

  if (progress >= 1) {
    return 1;
  }

  return progress;
}

function normalizeTimestamp(value: unknown, fallback: number): number {
  const timestamp = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(timestamp) && timestamp >= 0 ? timestamp : fallback;
}

function normalizeText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function normalizeLocation(value: unknown): ReadingLocation | null {
  if (!isRecord(value)) {
    return null;
  }

  const offset = typeof value.offset === 'number' ? value.offset : Number(value.offset);

  return {
    chapter: normalizeText(value.chapter),
    anchor: normalizeText(value.anchor),
    offset: Number.isFinite(offset) ? offset : 0,
  };
}

function normalizeBookmark(value: unknown, fallbackCreatedAt: number): Bookmark | null {
  if (!isRecord(value)) {
    return null;
  }

  const id = normalizeText(value.id);

  if (id.length === 0) {
    return null;
  }

  return {
    id,
    chapter: normalizeText(value.chapter),
    anchor: normalizeText(value.anchor),
    excerpt: normalizeText(value.excerpt),
    note: normalizeText(value.note),
    createdAt: normalizeTimestamp(value.createdAt, fallbackCreatedAt),
  };
}

function normalizeBookmarks(value: unknown, fallbackCreatedAt: number): Bookmark[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const bookmarks: Bookmark[] = [];

  for (const item of value) {
    const bookmark = normalizeBookmark(item, fallbackCreatedAt);

    if (bookmark !== null) {
      bookmarks.push(bookmark);
    }
  }

  return bookmarks;
}

function normalizeState(value: unknown): WorkReadingState | null {
  if (!isRecord(value)) {
    return null;
  }

  const now = Date.now();
  const status = typeof value.status === 'string' && VALID_STATUSES.has(value.status as ReadingStatus)
    ? (value.status as ReadingStatus)
    : 'unread';
  const updatedAt = normalizeTimestamp(value.updatedAt, now);

  return {
    status,
    progress: clampProgress(value.progress),
    location: normalizeLocation(value.location),
    updatedAt,
    bookmarks: normalizeBookmarks(value.bookmarks, updatedAt),
  };
}

function sourceFromPayload(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    return {};
  }

  if (isRecord(value.items)) {
    return value.items;
  }

  return value;
}

function normalizePayload(value: unknown): ReadingStore {
  const source = sourceFromPayload(value);
  const snapshot: ReadingStore = {};

  for (const [id, state] of Object.entries(source)) {
    if (id === VERSION_FIELD || id === 'version' || id === 'items') {
      continue;
    }

    const normalizedState = normalizeState(state);

    if (normalizedState !== null) {
      snapshot[id] = normalizedState;
    }
  }

  return snapshot;
}

function withVersion(snapshot: ReadingStore): Record<string, unknown> {
  return { [VERSION_FIELD]: READING_STORAGE_VERSION, ...snapshot };
}

function cloneLocation(location: ReadingLocation | null): ReadingLocation | null {
  return location === null
    ? null
    : {
        chapter: location.chapter,
        anchor: location.anchor,
        offset: location.offset,
      };
}

function cloneBookmark(bookmark: Bookmark): Bookmark {
  return {
    id: bookmark.id,
    chapter: bookmark.chapter,
    anchor: bookmark.anchor,
    excerpt: bookmark.excerpt,
    note: bookmark.note,
    createdAt: bookmark.createdAt,
  };
}

function cloneState(state: WorkReadingState): WorkReadingState {
  return {
    status: state.status,
    progress: state.progress,
    location: cloneLocation(state.location),
    updatedAt: state.updatedAt,
    bookmarks: state.bookmarks.map(cloneBookmark),
  };
}

function cloneSnapshot(snapshot: ReadingStore): ReadingStore {
  const clone: ReadingStore = {};

  for (const [id, state] of Object.entries(snapshot)) {
    clone[id] = cloneState(state);
  }

  return clone;
}

function getStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    quietFallback = true;
    storageAvailable = false;
    return null;
  }
}

function canUseLocalStorage(): boolean {
  if (storageAvailable !== undefined) {
    return storageAvailable;
  }

  const storage = getStorage();

  if (storage === null) {
    storageAvailable = false;
    return false;
  }

  try {
    storage.setItem(PROBE_KEY, PROBE_KEY);
    storage.removeItem(PROBE_KEY);
    storageAvailable = true;
    return true;
  } catch {
    quietFallback = true;
    storageAvailable = false;
    return false;
  }
}

function readSnapshot(): ReadingStore {
  if (!canUseLocalStorage()) {
    return cloneSnapshot(memoryStore);
  }

  try {
    const raw = getStorage()?.getItem(READING_STORAGE_KEY);

    if (raw === null || raw === undefined || raw === '') {
      return {};
    }

    const snapshot = normalizePayload(JSON.parse(raw));
    memoryStore = cloneSnapshot(snapshot);
    return snapshot;
  } catch {
    quietFallback = true;
    storageAvailable = false;
    return cloneSnapshot(memoryStore);
  }
}

function writeSnapshot(snapshot: ReadingStore, notify = true): void {
  const normalized = normalizePayload(snapshot);
  memoryStore = cloneSnapshot(normalized);

  if (canUseLocalStorage()) {
    try {
      getStorage()?.setItem(READING_STORAGE_KEY, JSON.stringify(withVersion(normalized)));
    } catch {
      quietFallback = true;
      storageAvailable = false;
    }
  }

  if (notify) {
    notifySubscribers(memoryStore);
  }
}

function notifySubscribers(snapshot: ReadingStore): void {
  if (subscribers.size === 0) {
    return;
  }

  const publicStore = cloneSnapshot(snapshot);

  for (const subscriber of subscribers) {
    subscriber(publicStore);
  }
}

function emptyState(now: number): WorkReadingState {
  return {
    status: 'unread',
    progress: 0,
    location: null,
    updatedAt: now,
    bookmarks: [],
  };
}

function nextStatusForProgress(progress: number, currentStatus: ReadingStatus): ReadingStatus {
  if (progress >= 1) {
    return 'finished';
  }

  if (progress > 0) {
    return 'reading';
  }

  return currentStatus;
}

function createBookmarkId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }

  return `bm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function isStorageFallbackActive(): boolean {
  return quietFallback;
}

export function getAll(): ReadingStore {
  return cloneSnapshot(readSnapshot());
}

export function get(id: string): WorkReadingState | null {
  const state = readSnapshot()[id];
  return state === undefined ? null : cloneState(state);
}

export function setStatus(id: string, status: ReadingStatus): void {
  if (!VALID_STATUSES.has(status)) {
    throw new TypeError(`Invalid reading status: ${status}`);
  }

  const now = Date.now();
  const snapshot = readSnapshot();
  const current = snapshot[id] ?? emptyState(now);

  snapshot[id] = {
    ...current,
    status,
    updatedAt: now,
  };

  writeSnapshot(snapshot);
}

export function setProgress(id: string, progress: number, location: ReadingLocation | null): void {
  const now = Date.now();
  const snapshot = readSnapshot();
  const current = snapshot[id] ?? emptyState(now);
  const normalizedProgress = clampProgress(progress);

  snapshot[id] = {
    ...current,
    progress: normalizedProgress,
    status: nextStatusForProgress(normalizedProgress, current.status),
    location: cloneLocation(location),
    updatedAt: now,
  };

  writeSnapshot(snapshot);
}

export function getStatus(id: string): ReadingStatus {
  return get(id)?.status ?? 'unread';
}

export function addBookmark(id: string, bookmark: BookmarkInput): Bookmark {
  const now = Date.now();
  const snapshot = readSnapshot();
  const current = snapshot[id] ?? emptyState(now);
  const savedBookmark: Bookmark = {
    id: bookmark.id ?? createBookmarkId(),
    chapter: bookmark.chapter,
    anchor: bookmark.anchor,
    excerpt: bookmark.excerpt,
    note: bookmark.note,
    createdAt: bookmark.createdAt ?? now,
  };

  snapshot[id] = {
    ...current,
    bookmarks: [...current.bookmarks.filter((item) => item.id !== savedBookmark.id), savedBookmark],
    updatedAt: now,
  };

  writeSnapshot(snapshot);

  return cloneBookmark(savedBookmark);
}

export function removeBookmark(id: string, bookmarkId: string): void {
  const snapshot = readSnapshot();
  const current = snapshot[id];

  if (current === undefined) {
    return;
  }

  snapshot[id] = {
    ...current,
    bookmarks: current.bookmarks.filter((bookmark) => bookmark.id !== bookmarkId),
    updatedAt: Date.now(),
  };

  writeSnapshot(snapshot);
}

export function listBookmarks(id: string): Bookmark[] {
  return get(id)?.bookmarks ?? [];
}

export function exportJson(): string {
  return JSON.stringify(withVersion(readSnapshot()), null, 2);
}

export function importJson(str: string): ReadingStore {
  let parsed: unknown;

  try {
    parsed = JSON.parse(str);
  } catch (error) {
    throw new TypeError('Invalid reading-state JSON.', { cause: error });
  }

  const snapshot = normalizePayload(parsed);
  writeSnapshot(snapshot);
  return cloneSnapshot(snapshot);
}

export function migrate(): ReadingStore {
  const snapshot = readSnapshot();
  writeSnapshot(snapshot, false);
  return cloneSnapshot(snapshot);
}

export function subscribe(callback: ReadingStorageSubscriber): () => void {
  subscribers.add(callback);

  const storageHandler = (event: StorageEvent): void => {
    if (event.key !== READING_STORAGE_KEY) {
      return;
    }

    if (event.newValue === null) {
      memoryStore = {};
      callback({});
      return;
    }

    try {
      memoryStore = normalizePayload(JSON.parse(event.newValue));
      callback(cloneSnapshot(memoryStore));
    } catch {
      quietFallback = true;
      callback(cloneSnapshot(memoryStore));
    }
  };

  if (typeof window !== 'undefined') {
    window.addEventListener('storage', storageHandler);
  }

  return () => {
    subscribers.delete(callback);

    if (typeof window !== 'undefined') {
      window.removeEventListener('storage', storageHandler);
    }
  };
}
