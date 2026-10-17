#!/usr/bin/env python3
"""Explicit local, single-image VGGT experiment; never runs Jev or Gemma."""
import argparse
import fcntl
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from step_wise import db
from step_wise.config import ROOT, Settings
from step_wise.pipeline.photos import decode
from step_wise.pipeline.reconstruction import live_scene_local


VIEWER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Step Wise · Kitchen in 3D</title>
<style>
:root{color-scheme:light;--paper:#f5f3eb;--ink:#214b3b;--line:#d7dacf}*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 system-ui,sans-serif}
header{padding:22px 32px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:20px;align-items:center}
.brand{font:30px Georgia,serif;letter-spacing:-1px}.eyebrow{font-size:10px;letter-spacing:2px;text-transform:uppercase}
main{padding:28px 32px;display:grid;grid-template-columns:minmax(260px,330px) minmax(0,1fr);gap:32px}
h1{font:42px/1.08 Georgia,serif;letter-spacing:-1.5px;margin:12px 0 20px}h2{font:24px Georgia,serif;margin:24px 0 10px}
.original{width:100%;display:block;border-radius:3px}p{color:#5f6b61}small{font-size:12px;color:#667168}
.notice{padding:14px 16px;background:#ebe6d2;border-left:3px solid #ad9054;color:#59583f;font-size:13px}
.stage{position:relative;background:#172c25;border-radius:5px;overflow:hidden;height:calc(100svh - 190px);min-height:500px;max-height:950px}
canvas{display:block;width:100%;height:100%;touch-action:none}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
button,a{font:inherit;font-size:13px;color:var(--ink);border:1px solid #bdc8bd;background:transparent;border-radius:3px;padding:9px 13px;text-decoration:none;cursor:pointer}
button:hover,a:hover{background:#e2e9dc}button:focus-visible,a:focus-visible{outline:3px solid #cf9659;outline-offset:3px}
button[aria-pressed=true]{background:var(--ink);color:white}#loading{position:absolute;inset:0;display:grid;place-items:center;color:#dce5d5;padding:25px;text-align:center}
.caption{display:flex;justify-content:space-between;gap:15px;padding:10px 0;font-size:12px;color:#667168}
@media(max-width:900px){header{padding:18px 20px}main{padding:22px 20px;grid-template-columns:1fr;gap:20px}.original{max-width:420px}h1{font-size:36px}.stage{height:65svh;min-height:420px}.caption{flex-wrap:wrap}}
</style>
<script type="importmap">{"imports":{"three":"./vendor/three.module.js"}}</script></head>
<body><header><div class="brand">Step Wise.</div><div class="eyebrow">3D study / actual VGGT output</div></header>
<main><aside><div class="eyebrow">One photograph → estimated geometry</div><h1>A new angle<br>on the kitchen.</h1>
<img class="original" src="input.png" alt="The original kitchen photo supplied for this experiment">
<h2>Your photo, in depth.</h2><p>Drag to orbit. Scroll to zoom. Right-drag to pan. Switch between a colored surface and the underlying point cloud.</p>
<div class="notice">Single-view estimate. Only photographed surfaces are reconstructed. Hidden geometry is unknown; scale is relative and no measurements are validated.</div>
<p id="runtime">Loading run information…</p><small>VGGT-1B · local NVIDIA CUDA inference<br>Research checkpoint · no Jev or Gemma call</small></aside>
<section><div class="stage" id="stage"><div id="loading" role="status">Loading the reconstructed surface…</div></div>
<div class="toolbar"><button id="angled">Angled view</button><button id="source">Photo viewpoint</button><button id="surface" aria-pressed="true">Surface</button><button id="points" aria-pressed="false">Point cloud</button><a href="assets/kitchen-display.ply" download>Download PLY ↓</a><a href="scene.json">Run details ↗</a></div>
<div class="caption"><span id="stats"></span><span>Visible-surface geometry · not a complete room model</span></div></section></main>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from './vendor/OrbitControls.js';
import {PLYLoader} from './vendor/PLYLoader.js';
const stage=document.querySelector('#stage'),loading=document.querySelector('#loading');
try{
const run=await fetch('./scene.json').then(r=>{if(!r.ok)throw Error('Run information unavailable');return r.json()});
const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.setClearColor('#172c25');stage.append(renderer.domElement);
renderer.domElement.setAttribute('aria-label','Interactive VGGT reconstruction of the kitchen');renderer.domElement.setAttribute('role','img');
const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(50,1,.001,1000),controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;
controls.addEventListener('change',()=>renderer.render(scene,camera));
const group=new THREE.Group();group.rotation.x=Math.PI;scene.add(group);
const binary=async(name)=>{const r=await fetch(name);if(!r.ok)throw Error('Geometry unavailable');return r.arrayBuffer()};
const [positions,colors,indices,cloud]=await Promise.all([binary('surface-positions.bin'),binary('surface-colors.bin'),binary('surface-indices.bin'),new PLYLoader().loadAsync('./assets/kitchen-display.ply')]);
const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(new Float32Array(positions),3));geometry.setAttribute('color',new THREE.BufferAttribute(new Uint8Array(colors),3,true));geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(indices),1));
const surface=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({vertexColors:true,side:THREE.DoubleSide}));group.add(surface);
cloud.computeBoundingBox();const size=cloud.boundingBox.getSize(new THREE.Vector3()),span=Math.max(size.x,size.y,size.z),center=cloud.boundingBox.getCenter(new THREE.Vector3()).multiply(new THREE.Vector3(1,-1,-1));
const points=new THREE.Points(cloud,new THREE.PointsMaterial({size:span*.002,vertexColors:true,sizeAttenuation:true}));points.visible=false;group.add(points);
camera.near=Math.max(span*.0001,0.000001);camera.far=span*100;controls.minDistance=span*.01;controls.maxDistance=span*12;
const sourceView=()=>{const k=run.images[0].intrinsics;camera.fov=2*Math.atan(518/(2*k[1][1]))*180/Math.PI;camera.position.set(0,0,0);controls.target.set(0,0,-Math.max(Math.abs(center.z),span*.1));camera.updateProjectionMatrix();controls.update()};
const angleView=()=>{sourceView();camera.position.x+=span*.14;camera.position.y+=span*.06;controls.update()};
document.querySelector('#source').onclick=sourceView;document.querySelector('#angled').onclick=angleView;
const mode=(showSurface)=>{surface.visible=showSurface;points.visible=!showSurface;document.querySelector('#surface').setAttribute('aria-pressed',String(showSurface));document.querySelector('#points').setAttribute('aria-pressed',String(!showSurface));renderer.render(scene,camera)};
document.querySelector('#surface').onclick=()=>mode(true);document.querySelector('#points').onclick=()=>mode(false);
new ResizeObserver(()=>{const w=stage.clientWidth,h=stage.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix()}).observe(stage);
document.querySelector('#runtime').textContent=`${run.runtime.gpu} · ${run.runtime.elapsed_seconds.toFixed(1)} s total reconstruction · ${(run.runtime.peak_allocated_bytes/2**30).toFixed(1)} GiB peak GPU allocation.`;
document.querySelector('#stats').textContent=`${cloud.getAttribute('position').count.toLocaleString()} points · ${(geometry.index.count/3).toLocaleString()} surface triangles`;loading.remove();angleView();
renderer.setAnimationLoop(()=>{controls.update();renderer.render(scene,camera)});
}catch(error){loading.textContent='The 3D view could not load: '+error.message;console.error(error)}
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    metadata = json.loads((ROOT / 'model-cache/VGGT-1B/provenance.json').read_text())
    settings = Settings(data=output, recon_backend='vggt',
                        vggt_checkpoint=str(ROOT / 'model-cache/VGGT-1B/model.pt'),
                        vggt_checkpoint_sha256=metadata['sha256'],
                        vggt_commit='a288dd0f14786c93483e45524328726ab7b1b4ce')
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('A CUDA GPU is required; this experiment never falls back to CPU')
    output.mkdir(parents=True, exist_ok=False)
    image, _ = decode(args.image.read_bytes())
    image.save(output / 'input.png')
    context = {'settings': settings, 'directory': output}
    with (ROOT / 'model-cache/vggt-inference.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(f'Running VGGT on {torch.cuda.get_device_name(0)}; one image, no duplicated views', flush=True)
        scene = live_scene_local(context, {'id': 'kitchen'}, [{'id': 'input', 'path': 'input.png'}])
    scene.update({'reconstruction_kind': 'single_view_estimate', 'source_image_sha256': db.digest(args.image.read_bytes()),
                  'model_revision': metadata['revision'], 'metric_validated': False,
                  'limitations': ['Only visible surfaces; no hidden-room reconstruction', 'No multi-view consistency validation', 'Relative scale; no validated dimensions', 'Surface triangulation omits large neighboring depth discontinuities']})
    npz = np.load(output / scene['analysis_path'])
    points = npz['world_points'][0, ::2, ::2].astype('<f4')
    depth, confidence = npz['depth'][0, ::2, ::2, 0], npz['confidence'][0, ::2, ::2]
    colors = np.asarray(Image.open(output / 'assets/input-vggt.png'))[::2, ::2, :3].copy()
    l, t, r, b = scene['images'][0]['transform']['content_box']
    y, x = np.mgrid[0:518:2, 0:518:2]
    valid = (x >= l) & (x < r) & (y >= t) & (y < b) & (depth > 0) & np.isfinite(points).all(axis=-1)
    valid &= confidence >= np.quantile(confidence[valid], .15)
    grid = np.arange(depth.size).reshape(depth.shape)
    corners = np.stack([grid[:-1, :-1], grid[:-1, 1:], grid[1:, :-1], grid[1:, 1:]], axis=-1).reshape(-1, 4)
    values = depth.ravel()[corners]
    keep = valid.ravel()[corners].all(axis=1) & ((values.max(axis=1) - values.min(axis=1)) <= .08 * values.min(axis=1))
    corners = corners[keep]
    triangles = np.concatenate([corners[:, [0, 2, 1]], corners[:, [1, 2, 3]]]).astype('<u4')
    assert len(triangles) > 1000 and np.isfinite(points).all(), 'Insufficient usable surface geometry'
    points.tofile(output / 'surface-positions.bin')
    colors.tofile(output / 'surface-colors.bin')
    triangles.tofile(output / 'surface-indices.bin')
    scene['surface_triangles'] = len(triangles)
    (output / 'scene.json').write_text(json.dumps(scene, indent=2, allow_nan=False) + '\n')
    vendor = output / 'vendor'
    vendor.mkdir()
    three = ROOT / 'frontend/node_modules/three'
    for source in ('build/three.module.js', 'build/three.core.js', 'examples/jsm/controls/OrbitControls.js', 'examples/jsm/loaders/PLYLoader.js'):
        shutil.copyfile(three / source, vendor / Path(source).name)
    shutil.copyfile(three / 'LICENSE', vendor / 'LICENSE-three.txt')
    (output / 'index.html').write_text(VIEWER)
    print(json.dumps({'output': str(output), 'runtime': scene['runtime'], 'surface_triangles': len(triangles)}, indent=2), flush=True)


if __name__ == '__main__':
    main()
