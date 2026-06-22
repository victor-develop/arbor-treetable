/** Tailwind config for the Arbor thin shell.
 *
 * Task mandate: Tailwind. The existing hand-written styles use an ``arbor-*``
 * prefix and CSS variables so they coexist with Tailwind utilities (no class
 * collisions). Tailwind scans the React source for utility classes.
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Surface the same palette the arbor-* CSS vars use, so utilities and
        // the bespoke styles stay visually consistent.
        accent: 'var(--arbor-accent)',
        suggest: 'var(--arbor-suggest)',
        pending: 'var(--arbor-pending)',
        saved: 'var(--arbor-saved)',
      },
    },
  },
  plugins: [],
};
