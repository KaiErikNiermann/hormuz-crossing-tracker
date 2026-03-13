import * as esbuild from "esbuild";

const version = Date.now().toString(36);

await esbuild.build({
  entryPoints: ["src/app.ts"],
  bundle: true,
  outfile: "dist/app.js",
  format: "iife",
  sourcemap: true,
  external: ["maplibre-gl"],
  target: "es2020",
  define: {
    __BUILD_VERSION__: JSON.stringify(version),
  },
});

console.log(`Built with version: ${version}`);
