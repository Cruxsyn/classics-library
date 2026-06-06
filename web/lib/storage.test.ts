import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReadingStore } from './storage';

function createStorage(throwOnWrite = false): Storage {
  const data = new Map<string, string>();

  return {
    get length() {
      return data.size;
    },
    clear() {
      data.clear();
    },
    getItem(key: string) {
      return data.get(String(key)) ?? null;
    },
    key(index: number) {
      return Array.from(data.keys())[index] ?? null;
    },
    removeItem(key: string) {
      data.delete(String(key));
    },
    setItem(key: string, value: string) {
      if (throwOnWrite) {
        throw new DOMException('localStorage unavailable', 'QuotaExceededError');
      }

      data.set(String(key), String(value));
    },
  };
}

describe('reading storage', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
  });

  it('persists progress, status, bookmarks, and same-page subscriber updates', async () => {
    vi.stubGlobal('localStorage', createStorage());
    const storage = await import('./storage');
    const snapshots: ReadingStore[] = [];
    const unsubscribe = storage.subscribe((snapshot) => snapshots.push(snapshot));

    storage.setProgress('Homer/iliad', 0.42, {
      chapter: 'iliad-bk-1.html',
      anchor: 'p-11',
      offset: 256,
    });
    storage.addBookmark('Homer/iliad', {
      id: 'bk-1',
      chapter: 'iliad-bk-1.html',
      anchor: 'p-11',
      excerpt: 'Sing, O goddess, the anger',
      note: 'Opening invocation.',
      createdAt: 123,
    });

    unsubscribe();

    expect(storage.getStatus('Homer/iliad')).toBe('reading');
    expect(storage.get('Homer/iliad')?.progress).toBeCloseTo(0.42);
    expect(storage.listBookmarks('Homer/iliad')).toHaveLength(1);
    expect(snapshots.at(-1)?.['Homer/iliad']?.bookmarks[0]?.id).toBe('bk-1');
    expect(JSON.parse(storage.exportJson()).__version).toBe(1);
  });

  it('falls back to in-memory storage when localStorage rejects writes', async () => {
    vi.stubGlobal('localStorage', createStorage(true));
    const storage = await import('./storage');

    storage.setStatus('Plato/republic', 'finished');

    expect(storage.isStorageFallbackActive()).toBe(true);
    expect(storage.getStatus('Plato/republic')).toBe('finished');
  });
});
