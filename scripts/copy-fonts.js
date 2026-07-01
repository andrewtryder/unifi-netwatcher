const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const dest = path.join(root, "app/web/static/fonts");
const copies = [
  {
    src: "node_modules/@fontsource/inter/files/inter-latin-400-normal.woff2",
    dest: "inter-latin-400.woff2",
  },
  {
    src: "node_modules/@fontsource/inter/files/inter-latin-500-normal.woff2",
    dest: "inter-latin-500.woff2",
  },
  {
    src: "node_modules/@fontsource/inter/files/inter-latin-600-normal.woff2",
    dest: "inter-latin-600.woff2",
  },
  {
    src: "node_modules/@fontsource/inter/files/inter-latin-700-normal.woff2",
    dest: "inter-latin-700.woff2",
  },
  {
    src: "node_modules/@fontsource/jetbrains-mono/files/jetbrains-mono-latin-400-normal.woff2",
    dest: "jetbrains-mono-latin-400.woff2",
  },
  {
    src: "node_modules/@fontsource/jetbrains-mono/files/jetbrains-mono-latin-500-normal.woff2",
    dest: "jetbrains-mono-latin-500.woff2",
  },
  {
    src: "node_modules/@fontsource/jetbrains-mono/files/jetbrains-mono-latin-700-normal.woff2",
    dest: "jetbrains-mono-latin-700.woff2",
  },
  {
    src: "node_modules/@fontsource/material-symbols-outlined/files/material-symbols-outlined-latin-400-normal.woff2",
    dest: "material-symbols-outlined.woff2",
  },
];

fs.mkdirSync(dest, { recursive: true });

for (const { src, dest: filename } of copies) {
  const from = path.join(root, src);
  const to = path.join(dest, filename);
  if (!fs.existsSync(from)) {
    console.error(`Missing font file: ${from}`);
    process.exit(1);
  }
  fs.copyFileSync(from, to);
}

console.log(`Copied ${copies.length} font files to ${dest}`);
