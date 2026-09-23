import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
  // GAP-025: components are compiled by Next with the automatic JSX runtime
  // (tsconfig `jsx: "preserve"`), so vitest must use it too — esbuild's classic
  // default emits `React.createElement` and every component file, none of which
  // imports React, fails with "React is not defined".
  esbuild: { jsx: 'automatic' },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
