// Build with esbuild. Resolves react from local node_modules, falling back to the global/tool install (offline sandbox).
import { cpSync, mkdirSync, existsSync } from "node:fs";
let esbuild;
try { esbuild = await import("esbuild"); } catch { esbuild = await import("/opt/npm-tools/node_modules/esbuild/lib/main.js"); }  // sandbox fallback
const { build } = esbuild;
const G = "/home/claude/.npm-global/lib/node_modules";
const nodePaths = [existsSync("node_modules") ? "node_modules" : null, G].filter(Boolean);
mkdirSync("dist", { recursive: true });
await build({
  entryPoints: ["src/main.tsx"], bundle: true, minify: true, sourcemap: true, outfile: "dist/app.js",
  jsx: "automatic", target: "es2020", nodePaths, define: { "process.env.NODE_ENV": '"production"' }, logLevel: "info",
});
await build({ entryPoints: ["src/styles.css"], bundle: true, minify: true, outfile: "dist/app.css" });
cpSync("public/index.html", "dist/index.html");
