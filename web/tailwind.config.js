/** @type {import('tailwindcss').Config} */
// Colours are CSS variables (src/styles/global.css) so light and dark mode swap in one place.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
export default {
  content: ['./src/**/*.{astro,html,js,ts}'],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        surface: v('surface'),
        panel: v('panel'),
        sunken: v('sunken'),
        line: v('line'),
        ink: v('ink'),
        muted: v('muted'),
        faint: v('faint'),
        brand: v('brand'),
        'brand-soft': v('brand-soft'),
        'brand-ink': v('brand-ink'),
        good: v('good'),
        bad: v('bad'),
        warn: v('warn'),
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'Segoe UI', 'sans-serif'],
        urdu: ['"Noto Nastaliq Urdu"', 'serif'],
      },
      borderRadius: { xl: '0.875rem' },
    },
  },
  plugins: [],
};
