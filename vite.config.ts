import { rolldown } from 'rolldown';
import type { Plugin } from 'vite';
import { defineConfig } from 'vite';

const fromProject = (path: string): string => new URL(path, import.meta.url).pathname;

function readerRuntimePlugin(): Plugin {
  return {
    name: 'reader-runtime-iife',
    apply: 'build',
    async closeBundle() {
      const bundle = await rolldown({
        input: fromProject('web/reader/reader-runtime.ts'),
      });
      try {
        await bundle.write({
          file: fromProject('output/reader-runtime.js'),
          format: 'iife',
          name: 'ReaderRuntime',
          exports: 'none',
          sourcemap: false,
          minify: true,
        });
      } finally {
        await bundle.close();
      }
    },
  };
}


export default defineConfig({
  plugins: [readerRuntimePlugin()],
  root: fromProject('web/shell/'),
  publicDir: false,
  build: {
    outDir: fromProject('output/'),
    emptyOutDir: false,
    rollupOptions: {
      input: {
        shell: fromProject('web/shell/index.html'),
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
