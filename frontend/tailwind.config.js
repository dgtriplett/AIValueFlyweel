/** @type {import('tailwindcss').Config} */
// Databricks "Blueprint" palette in DARK MODE — adopted from the parent app
// (data-ai-maturity-assessment). Legacy aliases (navy/electric/status/grid)
// preserved so earlier JSX keeps working while conforming to Blueprint tokens.
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        lava: {
          800: '#801C17', 700: '#BD2B26', 600: '#FF3621',
          500: '#FF5F46', 400: '#FF9E94', 300: '#FABFBA',
          DEFAULT: '#FF3621',
        },
        oat: { light: '#F9F7F4', medium: '#EEEDE9', DEFAULT: '#EEEDE9' },
        success: { 800: '#095A35', 700: '#00875C', 600: '#00A972', 500: '#42BA91', 400: '#70C4AB', 300: '#9ED6C4', DEFAULT: '#00A972' },
        warning: { 800: '#7D5319', 700: '#BA7B23', 600: '#FFAB00', 500: '#FCBA33', 400: '#FFCC66', 300: '#FFDB96', DEFAULT: '#FFAB00' },
        info:    { 800: '#04355D', 700: '#0E538B', 600: '#2272B4', 500: '#4299E0', 400: '#8ACAFF', 300: '#BAE1FC', DEFAULT: '#2272B4' },
        maroon:  { 600: '#98102A', 500: '#AB4057' },
        navy: {
          DEFAULT: '#0B2026', 900: '#0B2026', 800: '#1B3139', 700: '#143D4A',
          600: '#2A4A56', 500: '#618794', 400: '#90A5B1', 300: '#C4CCD6',
        },
        electric: { DEFAULT: '#FF3621', 300: '#FF9E94', 400: '#FF5F46', 500: '#FF3621', 600: '#BD2B26' },
        status: { green: '#00A972', yellow: '#FFAB00', red: '#FF3621' },
        grid: { orange: '#FFAB00', red: '#FF3621' },
        phase: { 0: '#618794', 1: '#2272B4', 2: '#00A972', 3: '#FFAB00', 4: '#98102A' },
      },
      fontFamily: {
        sans: ['"DM Sans"', 'system-ui', 'sans-serif'],
        mono: ['"DM Mono"', 'monospace'],
      },
      borderRadius: { card: '8px', btn: '4px' },
      boxShadow: {
        card: '0 1px 3px rgba(0,0,0,0.3)',
        'card-hover': '0 4px 12px rgba(0,0,0,0.4)',
      },
      animation: {
        'fade-in': 'fadeIn 0.3s ease-out',
        'slide-up': 'slideUp 0.4s ease-out',
        'scale-in': 'scaleIn 0.3s ease-out',
      },
      keyframes: {
        fadeIn: { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
        slideUp: { '0%': { opacity: '0', transform: 'translateY(16px)' }, '100%': { opacity: '1', transform: 'translateY(0)' } },
        scaleIn: { '0%': { opacity: '0', transform: 'scale(0.96)' }, '100%': { opacity: '1', transform: 'scale(1)' } },
      },
    },
  },
  plugins: [],
}
