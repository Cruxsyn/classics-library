export const READING_STORAGE_KEY = 'library:reading:v1' as const;

export type ReadingProgress = {
  workId: string;
  href: string;
  label: string;
  percent: number;
  updatedAt: string;
};

export function readReadingProgress(): ReadingProgress[] {
  return [];
}

export function writeReadingProgress(_progress: readonly ReadingProgress[]): void {
  // Persistence behavior lands with the reader shell.
}
