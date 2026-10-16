import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';
import type { Anchor, Hazard, Scene } from '../types';

export default function SceneViewer({scene, hazards, selectedId, onSelect, onPick, picking}: {scene?: Scene; hazards: Hazard[]; selectedId?: string; onSelect: (id: string) => void; onPick: (anchor: Anchor) => void; picking: boolean}) {
  const mount = useRef<HTMLDivElement>(null);
  const callbacks = useRef({onSelect, onPick, picking});
  callbacks.current = {onSelect, onPick, picking};
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const element = mount.current;
    if (!element || !scene?.ply_url) return;
    let disposed = false, frame = 0;
    let renderer: THREE.WebGLRenderer | undefined;
    let observer: ResizeObserver | undefined;
    let controls: OrbitControls | undefined;
    const abort = new AbortController();
    const world = new THREE.Scene(); world.background = new THREE.Color('#1e372d');
    const group = new THREE.Group();
    if (scene.display_transform) { group.matrix.fromArray(scene.display_transform.flat()).transpose(); group.matrixAutoUpdate = false; }
    world.add(group);
    const markers: THREE.Mesh[] = [];
    const cleanup = () => { cancelAnimationFrame(frame); observer?.disconnect(); controls?.dispose(); world.traverse(o => { if (o instanceof THREE.Mesh || o instanceof THREE.Points) { o.geometry.dispose(); const materials = Array.isArray(o.material) ? o.material : [o.material]; materials.forEach(m => m.dispose()); } }); renderer?.dispose(); renderer?.domElement.remove(); };
    setError(''); setLoading(true);
    (async () => {
      try {
        const response = await fetch(scene.ply_url!, {signal: abort.signal, credentials: 'same-origin'});
        if (!response.ok) throw Error('Nuvola non disponibile');
        const buffer = await response.arrayBuffer();
        if (disposed) return;
        const geometry = new PLYLoader().parse(buffer); geometry.computeBoundingSphere();
        renderer = new THREE.WebGLRenderer({antialias: true}); renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
        element.appendChild(renderer.domElement);
        const points = new THREE.Points(geometry, new THREE.PointsMaterial({size: .026, vertexColors: true})); group.add(points);
        for (const position of scene.cameras ?? []) {
          const cameraMarker = new THREE.Mesh(new THREE.SphereGeometry(.025, 8, 8), new THREE.MeshBasicMaterial({color: '#b4cbbb'}));
          cameraMarker.position.fromArray(position); group.add(cameraMarker);
        }
        for (const h of hazards) {
          const anchor = h.review ? h.review.effective.anchor : h.anchor;
          if (anchor?.scene_id !== scene.id || h.review?.decision === 'reject') continue;
          const marker = new THREE.Mesh(new THREE.SphereGeometry(.065, 12, 12), new THREE.MeshBasicMaterial({color: h.id === selectedId ? '#f4c47f' : '#ee895d'}));
          marker.position.fromArray(anchor.position_world); marker.userData.hazardId = h.id; group.add(marker); markers.push(marker);
        }
        world.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(group), center = bounds.getCenter(new THREE.Vector3());
        const size = Math.max(bounds.getSize(new THREE.Vector3()).length(), 1);
        const camera = new THREE.PerspectiveCamera(45, 1, .01, size * 100);
        camera.position.copy(center).add(new THREE.Vector3(size * .7, size * .55, size * .75));
        controls = new OrbitControls(camera, renderer.domElement); controls.target.copy(center); controls.enableDamping = true;
        const resize = () => { if (!renderer) return; const w = element.clientWidth, h = element.clientHeight; renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); };
        observer = new ResizeObserver(resize); observer.observe(element); resize();
        const ray = new THREE.Raycaster(); ray.params.Points!.threshold = .08;
        renderer.domElement.addEventListener('click', event => {
          const rect = renderer!.domElement.getBoundingClientRect();
          ray.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
          const marker = ray.intersectObjects(markers)[0];
          if (marker) { callbacks.current.onSelect(marker.object.userData.hazardId); return; }
          const hit = callbacks.current.picking && ray.intersectObject(points)[0];
          const selected = hazards.find(h => h.id === selectedId);
          if (hit && selected) callbacks.current.onPick({scene_id: scene.id, position_world: group.worldToLocal(hit.point.clone()).toArray() as [number, number, number], method: 'reviewer_corrected', source_photo_ids: selected.evidence.map(e => e.photo_id), uncertainty: 'Punto scelto dal revisore: verificare sulla foto sorgente.'});
        });
        const animate = () => { if (disposed) return; controls?.update(); renderer?.render(world, camera); frame = requestAnimationFrame(animate); };
        animate(); setLoading(false);
      } catch (e) { if (!disposed) { setError(`Vista 3D non disponibile. La revisione fotografica resta disponibile. ${(e as Error).message}`); setLoading(false); cleanup(); } }
    })();
    return () => { disposed = true; abort.abort(); cleanup(); };
  }, [scene?.id, scene?.ply_url, hazards, selectedId]);
  return <div className="viewer-wrap"><div className="viewer" ref={mount} aria-label="Vista della nuvola di punti" />{loading && <p className="viewer-message">Caricamento della scena…</p>}{error && <p className="viewer-message" role="status">{error}</p>}{!scene?.ply_url && <p className="viewer-message">Scena non disponibile. Continua con le foto.</p>}<span className="viewer-caption">{scene?.provenance === 'fixture' ? 'Geometria sintetica · nessuna ricostruzione reale' : 'Scena relativa'} / scala {scene?.scale_state ?? 'sconosciuta'}</span></div>;
}
