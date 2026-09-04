/**
 * Дизайн-токены сняты с доски Miro (доска «Базовый СРМ»).
 *
 * Правило проекта: в разметке используются ТОЛЬКО эти токены.
 * Произвольных значений вида bg-[#123456] или p-[13px] быть не должно —
 * иначе через неделю макет расползётся, как расползся на самой доске.
 *
 * Вся сетка кратна 4px. Стандартная шкала Tailwind это уже обеспечивает.
 */

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Основа: почти чёрный с холодным уклоном в синий — как на макете
        bg: '#0B0D12',
        surface: {
          DEFAULT: '#141821', // карточки, панели, строки списка
          raised: '#1B202B', // поля ввода, наведение, вложенные блоки
          sunken: '#0E1117', // подложка чата, тело таблицы
        },
        line: {
          DEFAULT: '#232937', // разделители списков
          strong: '#2E3646', // границы полей и кнопок
        },
        ink: {
          DEFAULT: '#E8EBF2', // основной текст
          muted: '#A0A8B8', // подписи, превью сообщений
          faint: '#6B7385', // время, служебные подписи
          inverse: '#0B0D12',
        },
        accent: {
          DEFAULT: '#2F6BE6', // первичная кнопка, активная вкладка
          hover: '#3B79F5',
          text: '#6E9BFF', // текст ссылок и исходящих сообщений
          soft: '#16233D', // фон бейджа и выбранной строки
          line: '#284272',
        },
        bubble: {
          in: '#1A1E27', // входящее сообщение
          out: '#173059', // исходящее сообщение
        },
        success: { DEFAULT: '#34C77B', soft: '#12261D' },
        warning: { DEFAULT: '#E0A03C', soft: '#2A2216' },
        danger: { DEFAULT: '#E05A52', soft: '#2B1917' },
        violet: { DEFAULT: '#7A6BE0', text: '#A79BF0', soft: '#221F3A' },
      },
      borderRadius: {
        sm: '6px',
        DEFAULT: '8px', // поля ввода, кнопки
        md: '10px',
        lg: '12px', // карточки
        xl: '16px', // нижние панели, крупные блоки
        '2xl': '20px',
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"SF Pro Text"',
          '"Segoe UI"',
          'system-ui',
          'Roboto',
          '"Helvetica Neue"',
          'Arial',
          'sans-serif',
        ],
        num: [
          'ui-monospace',
          'SFMono-Regular',
          '"SF Mono"',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      fontSize: {
        // Плотная шкала: CRM читают, а не разглядывают
        micro: ['11px', { lineHeight: '14px', letterSpacing: '0.02em' }],
        label: ['12px', { lineHeight: '16px', letterSpacing: '0.04em' }],
        xs: ['13px', { lineHeight: '18px' }],
        sm: ['14px', { lineHeight: '20px' }],
        base: ['15px', { lineHeight: '22px' }],
        lg: ['17px', { lineHeight: '24px', letterSpacing: '-0.005em' }],
        xl: ['20px', { lineHeight: '26px', letterSpacing: '-0.01em' }],
        '2xl': ['24px', { lineHeight: '30px', letterSpacing: '-0.015em' }],
        '3xl': ['30px', { lineHeight: '36px', letterSpacing: '-0.02em' }],
        '4xl': ['36px', { lineHeight: '42px', letterSpacing: '-0.022em' }],
      },
      boxShadow: {
        card: '0 1px 2px rgba(0,0,0,.4)',
        raised: '0 4px 16px -6px rgba(0,0,0,.6)',
        sheet: '0 -8px 40px -12px rgba(0,0,0,.8)',
        popover: '0 8px 32px -8px rgba(0,0,0,.7)',
      },
      keyframes: {
        'sheet-up': {
          from: { transform: 'translateY(100%)' },
          to: { transform: 'translateY(0)' },
        },
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
      },
      animation: {
        'sheet-up': 'sheet-up .24s cubic-bezier(.32,.72,0,1)',
        'fade-in': 'fade-in .16s ease-out',
        shimmer: 'shimmer 1.6s infinite',
      },
      screens: {
        // Граница между мобильной и десктопной раскладкой.
        //
        // 860, а не 1024: на ноутбуке окно почти никогда не развёрнуто во весь
        // экран, а при увеличенном масштабировании экрана логическая ширина
        // макбука падает ниже 1024. С порогом 1024 пользователь на ноутбуке
        // видел мобильную версию. При 860 две колонки ещё помещаются
        // (список 340 + рабочая область 520), а планшет в портрете (768)
        // корректно остаётся на мобильной раскладке.
        desk: '860px',
      },
    },
  },
  plugins: [],
}
