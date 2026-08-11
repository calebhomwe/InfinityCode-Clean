import path from "node:path";
import { fileURLToPath } from "node:url";

// Resolve the Tailwind config next to this file, not the process cwd — the
// dev server may be launched from any directory, and the tailwindcss plugin
// silently falls back to an empty default config when it can't find one.
const projectDir = path.dirname(fileURLToPath(import.meta.url));

export default {
  plugins: {
    tailwindcss: { config: path.join(projectDir, "tailwind.config.js") },
    autoprefixer: {},
  },
};
