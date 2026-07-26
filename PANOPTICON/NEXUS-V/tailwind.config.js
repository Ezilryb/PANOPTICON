/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        panopticon: {
          bg: "#0f1419",
          panel: "#1a2332",
          accent: "#3b82f6",
          ok: "#22c55e",
          warn: "#f59e0b",
          err: "#ef4444",
        },
      },
    },
  },
  plugins: [],
};
