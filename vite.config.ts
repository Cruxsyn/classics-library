import { defineConfig } from 'vite';

const fromProject = (path: string): string => new URL(path, import.meta.url).pathname;

export default defineConfig({
  root: fromProject('web/shell/'),
  publicDir: false,
  build: {
    outDir: fromProject('output/'),
    emptyOutDir: false,
    rollupOptions: {
      input: {
        shell: fromProject('web/shell/index.html'),
        'reader-runtime': fromProject('web/reader/reader-runtime.ts'),
      },
      output: {
        entryFileNames: (chunkInfo) =>
          chunkInfo.name === 'reader-runtime'
            ? 'reader-runtime.js'
            : 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
});
