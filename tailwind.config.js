/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/web/templates/**/*.html"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        background: "#0f1418",
        surface: "#1b2024",
        "surface-variant": "#252b30",
        primary: "#38bdf8",
        secondary: "#10b981",
        error: "#f43f5e",
        warning: "#f59e0b",
        "on-surface": "#dee3e8",
        "on-surface-variant": "#bdc8d1",
        outline: "#3e484f",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [require("@tailwindcss/forms")],
};
