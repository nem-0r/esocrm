import js from '@eslint/js'
import tseslint from '@typescript-eslint/eslint-plugin'
import tsparser from '@typescript-eslint/parser'
import reactHooks from 'eslint-plugin-react-hooks'

export default [
  js.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsparser,
      parserOptions: { ecmaFeatures: { jsx: true }, sourceType: 'module' },
      globals: { window: 'readonly', document: 'readonly', navigator: 'readonly',
                 localStorage: 'readonly', setTimeout: 'readonly', clearTimeout: 'readonly',
                 setInterval: 'readonly', clearInterval: 'readonly', console: 'readonly',
                 fetch: 'readonly', WebSocket: 'readonly', FormData: 'readonly',
                 Blob: 'readonly', File: 'readonly', URL: 'readonly', AbortController: 'readonly' },
    },
    plugins: { '@typescript-eslint': tseslint, 'react-hooks': reactHooks },
    rules: {
      ...tseslint.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
      'no-undef': 'off',
    },
  },
]
