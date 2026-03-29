/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        display: ['"Space Grotesk"', 'system-ui', 'sans-serif']
      },
      colors: {
        ink: '#0f1115',
        ledger: {
          50: '#f4f7fb',
          200: '#c9d7f2',
          500: '#5166f7',
          700: '#2b2f76'
        }
      }
    }
  },
  plugins: []
};
