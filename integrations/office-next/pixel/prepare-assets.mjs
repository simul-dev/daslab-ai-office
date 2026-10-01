import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { build } from 'esbuild';
import { vendorRoot } from './vendor-path.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const pin = JSON.parse(fs.readFileSync(path.join(here, 'upstream.json'), 'utf8'));
const actual = execFileSync('git', ['-C', vendorRoot, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
if (actual !== pin.commit) throw new Error(`Pixel Agents checkout differs from the reviewed pin: ${actual}`);
const require = createRequire(import.meta.url);
const out = path.join(here, '.asset-loader.cjs');
await build({
  stdin: { contents: "export * from './core/src/assets/loader.ts'; export * from './core/src/assets/build.ts';", resolveDir: vendorRoot, loader: 'ts' },
  platform: 'node', format: 'cjs', bundle: true, outfile: out,
  alias: { pngjs: require.resolve('pngjs') }, logLevel: 'warning',
});
const decoder = require(out);
const assetsDir = path.join(vendorRoot, 'webview-ui', 'public', 'assets');
const catalog = decoder.buildFurnitureCatalog(assetsDir);
const layout = JSON.parse(fs.readFileSync(path.join(assetsDir, 'default-layout-1.json'), 'utf8'));
// Pet templates are not required to represent staff or tasks; only reviewed staff assets are loaded.
layout.pets = [];
const assets = {
  upstream: pin,
  characters: decoder.decodeAllCharacters(assetsDir),
  floors: decoder.decodeAllFloors(assetsDir),
  walls: decoder.decodeAllWalls(assetsDir),
  carpets: decoder.decodeAllCarpets(assetsDir),
  furniture: { catalog, sprites: decoder.decodeAllFurniture(assetsDir, catalog) },
  layout,
};
if (!assets.characters.length || !catalog.length) throw new Error('Pixel Agents assets are missing');
const publicDir = path.join(here, 'public');
fs.mkdirSync(publicDir, { recursive: true });
fs.writeFileSync(path.join(publicDir, 'pixel-assets.json'), JSON.stringify(assets));
fs.copyFileSync(path.join(vendorRoot, 'LICENSE'), path.join(publicDir, 'PIXEL-AGENTS-LICENSE.txt'));
fs.copyFileSync(path.join(here, 'THIRD-PARTY-NOTICES.txt'), path.join(publicDir, 'THIRD-PARTY-NOTICES.txt'));
fs.unlinkSync(out);
console.log(`Prepared upstream Pixel Agents ${pin.commit.slice(0, 12)}: ${assets.characters.length} characters, ${catalog.length} furniture sprites`);
