import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

export default function AvatarModel({url}: {url: string}) {
  const mount = useRef<HTMLDivElement>(null);
  const [message, setMessage] = useState('Caricamento del modello…');
  useEffect(() => {
    const element = mount.current;
    if (!element) return;
    let disposed = false, frame = 0;
    const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true});
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    element.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    // The GLB uses PBR materials; without an environment the metal walker renders black.
    const environment = new THREE.PMREMGenerator(renderer);
    scene.environment = environment.fromScene(new RoomEnvironment(), .04).texture;
    const camera = new THREE.PerspectiveCamera(30, 1, .05, 50);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.enablePan = false; controls.minPolarAngle = .5; controls.maxPolarAngle = 1.75;
    const resize = () => { const w = element.clientWidth, h = element.clientHeight; renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); };
    const observer = new ResizeObserver(resize); observer.observe(element); resize();
    new GLTFLoader().load(url, gltf => {
      if (disposed) return;
      scene.add(gltf.scene);
      const box = new THREE.Box3().setFromObject(gltf.scene), centre = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
      const distance = size.y / 2 / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.3;
      // glTF faces +Z; start at the same three-quarter view as the preview image.
      camera.position.copy(centre).add(new THREE.Vector3(Math.sin(.52), .08, Math.cos(.52)).multiplyScalar(distance));
      controls.target.copy(centre); controls.update();
      setMessage('');
    }, undefined, () => { if (!disposed) setMessage('Modello 3D non disponibile. L’immagine resta disponibile.'); });
    const animate = () => { controls.update(); renderer.render(scene, camera); frame = requestAnimationFrame(animate); };
    animate();
    return () => {
      disposed = true; cancelAnimationFrame(frame); observer.disconnect(); controls.dispose();
      scene.traverse(o => { if (o instanceof THREE.Mesh) { o.geometry.dispose(); (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { Object.values(m).forEach(v => { if (v instanceof THREE.Texture) v.dispose(); }); m.dispose(); }); } });
      scene.environment?.dispose(); environment.dispose(); renderer.dispose(); renderer.domElement.remove();
    };
  }, [url]);
  // The canvas lives in its own node: React must never reconcile children of the element three.js appends to.
  return <div className="avatar-model" aria-label="Modello 3D della figura"><div className="avatar-canvas" ref={mount} />{message ? <p className="avatar-message" role="status">{message}</p> : null}</div>;
}
