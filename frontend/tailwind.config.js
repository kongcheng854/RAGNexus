/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: "#12645a",
        border: "#dce4e0",
        muted: "#64766f",
      },
      fontFamily: { sans: ["Inter", "Noto Sans SC", "Microsoft YaHei", "sans-serif"] },
    },
  },
  plugins: [],
};
