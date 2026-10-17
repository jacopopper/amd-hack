// Compress a Gaussian-splat PLY into the .ksplat the room viewer streams.
//   node scripts/compress_splats.mjs <room-polished.ply> frontend/public/capture-data/room.ksplat
// Uses the viewer dependencies from docs/capture-reconstruction.md (model-cache/capture-viewer).
// Level 2 stores 16-bit positions and 8-bit harmonics; degree-1 harmonics and dropping splats
// below alpha 5 (which the viewer already skipped) left the review views visually unchanged.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const [input, output] = process.argv.slice(2);
if (!input || !output) throw Error('usage: compress_splats.mjs <input.ply> <output.ksplat>');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const library = path.join(root, 'model-cache/capture-viewer/node_modules/@mkkellogg/gaussian-splats-3d/build/gaussian-splats-3d.module.js');
const {PlyParser, SplatBufferGenerator} = await import(pathToFileURL(library));

const data = fs.readFileSync(input);
const splats = PlyParser.parseToUncompressedSplatArray(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength), 1);
const buffer = SplatBufferGenerator.getStandardGenerator(5, 2, 0, undefined, 5.0, 256).generateFromUncompressedSplatArray(splats);
fs.writeFileSync(output, Buffer.from(buffer.bufferData));
console.log(JSON.stringify({output, splats: buffer.getSplatCount(), bytes: fs.statSync(output).size}));
