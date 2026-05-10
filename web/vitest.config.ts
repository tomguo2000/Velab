import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
    plugins: [react()],
    test: {
        environment: 'jsdom',
        globals: true,
        setupFiles: ['./vitest.setup.ts'],
        css: true,
        testTimeout: 10000,
        hookTimeout: 10000,
        coverage: {
            provider: 'v8',
            reporter: ['text', 'json', 'html'],
            thresholds: {
                branches: 70,
                functions: 70,
                lines: 80,
                statements: 80,
            },
            exclude: [
                'node_modules/',
                '.next/',
                'out/',
                'build/',
                'src/app/page.tsx',
                '**/*.config.*',
                '**/*.d.ts',
                '**/types/**',
                'src/__tests__/**',
            ],
        },
    },
    resolve: {
        alias: {
            '@': path.resolve(__dirname, './src'),
        },
    },
});
