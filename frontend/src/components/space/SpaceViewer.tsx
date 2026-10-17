"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { PLYLoader } from "three/addons/loaders/PLYLoader.js";
import { categoryLabel, priorityMeta } from "@/lib/labels";
import { effective } from "@/lib/hazards";
import type { Anchor, Hazard, Scene } from "@/lib/types";

export type ViewMode = "dollhouse" | "plan";
export type Tool = "none" | "place" | "measure";

type Runtime = {
  renderer: THREE.WebGLRenderer;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  world: THREE.Scene;
  group: THREE.Group; // raw scene coordinates with display_transform applied
  points: THREE.Points;
  stems: THREE.Group;
  measure: THREE.Group;
  extent: number;
  floorY: number;
  center: THREE.Vector3;
  span: number;
  tags: Map<string, THREE.Vector3>; // world position of each tag disc
  flight: { from: [THREE.Vector3, THREE.Vector3]; to: [THREE.Vector3, THREE.Vector3]; start: number; duration: number } | null;
  interacted: boolean;
};

// Round, soft-edged sprite so the cloud reads as surfaces instead of square pixels.
function dotTexture() {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d")!;
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.6, "rgba(255,255,255,0.9)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
}

function homeView(rt: Pick<Runtime, "center" | "span">, mode: ViewMode): [THREE.Vector3, THREE.Vector3] {
  const target = rt.center.clone();
  if (mode === "plan") return [target.clone().add(new THREE.Vector3(0, rt.span * 1.9, 0.0001)), target];
  return [target.clone().add(new THREE.Vector3(rt.span * 0.95, rt.span * 0.85, rt.span * 1.05)), target];
}

export default function SpaceViewer({
  scene,
  hazards,
  selectedId,
  mode,
  tool,
  rightInset = 0,
  onSelect,
  onPick,
}: {
  scene?: Scene;
  hazards: Hazard[];
  selectedId?: string;
  mode: ViewMode;
  tool: Tool;
  rightInset?: number;
  onSelect: (id: string) => void;
  onPick: (anchor: Anchor) => void;
}) {
  const mount = useRef<HTMLDivElement>(null);
  const tagEls = useRef(new Map<string, HTMLButtonElement>());
  const measureLabel = useRef<HTMLDivElement>(null);
  const runtime = useRef<Runtime | null>(null);
  const latest = useRef({ onPick, tool, selectedId, hazards, scene, rightInset });
  const [state, setState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [measurement, setMeasurement] = useState<string | null>(null);

  useEffect(() => {
    latest.current = { onPick, tool, selectedId, hazards, scene, rightInset };
  });

  // Scene lifetime: the cloud is loaded once per scene asset.
  useEffect(() => {
    const element = mount.current;
    if (!element || !scene?.ply_url) return;
    let disposed = false;
    let frame = 0;
    const abort = new AbortController();
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    const world = new THREE.Scene();
    const group = new THREE.Group();
    if (scene.display_transform) {
      group.matrix.fromArray(scene.display_transform.flat()).transpose();
      group.matrixAutoUpdate = false;
    }
    world.add(group);
    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.autoRotateSpeed = 0.35;
    let appliedInset = -1;
    const frameCamera = () => {
      const w = element.clientWidth;
      const h = Math.max(element.clientHeight, 1);
      const inset = latest.current.rightInset;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      // A wider virtual frame shifts the optical centre into the area left of the side card.
      if (inset > 0) camera.setViewOffset(w + inset, h, inset, 0, w, h);
      else camera.clearViewOffset();
      camera.updateProjectionMatrix();
      appliedInset = inset;
    };
    const observer = new ResizeObserver(frameCamera);
    setState("loading");

    (async () => {
      try {
        const response = await fetch(scene.ply_url!, { signal: abort.signal, credentials: "same-origin" });
        if (!response.ok) throw new Error("scena non disponibile");
        const geometry = new PLYLoader().parse(await response.arrayBuffer());
        if (disposed) return;
        geometry.computeBoundingSphere();
        const extent = Math.max(geometry.boundingSphere!.radius, 1e-6);
        const colored = geometry.hasAttribute("color");
        const points = new THREE.Points(
          geometry,
          new THREE.PointsMaterial({ size: extent * 0.016, map: dotTexture(), alphaTest: 0.35, transparent: true, vertexColors: colored, color: colored ? 0xffffff : 0xc9d2dc, sizeAttenuation: true }),
        );
        group.add(points);
        world.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(points);
        const center = bounds.getCenter(new THREE.Vector3());
        const size = bounds.getSize(new THREE.Vector3());
        const span = Math.max(size.x, size.z, size.y, 1e-3);

        // Soft contact shadow under the model, as in a dollhouse view.
        const shadowCanvas = document.createElement("canvas");
        shadowCanvas.width = shadowCanvas.height = 128;
        const sctx = shadowCanvas.getContext("2d")!;
        const sg = sctx.createRadialGradient(64, 64, 0, 64, 64, 64);
        sg.addColorStop(0, "rgba(0,0,0,0.55)");
        sg.addColorStop(1, "rgba(0,0,0,0)");
        sctx.fillStyle = sg;
        sctx.fillRect(0, 0, 128, 128);
        const shadow = new THREE.Mesh(new THREE.PlaneGeometry(span * 2.2, span * 2.2), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(shadowCanvas), transparent: true, depthWrite: false }));
        shadow.rotation.x = -Math.PI / 2;
        shadow.position.set(center.x, bounds.min.y - extent * 0.01, center.z);
        world.add(shadow);

        const stems = new THREE.Group();
        const measure = new THREE.Group();
        world.add(stems, measure);
        camera.near = extent / 500;
        camera.far = extent * 60;
        const rt: Runtime = { renderer, camera, controls, world, group, points, stems, measure, extent, floorY: bounds.min.y, center, span, tags: new Map(), flight: null, interacted: false };
        const [position, target] = homeView(rt, "dollhouse");
        // Enter with a short glide, like opening a space.
        camera.position.copy(position.clone().multiplyScalar(1.6));
        controls.target.copy(target);
        rt.flight = { from: [camera.position.clone(), target.clone()], to: [position, target], start: performance.now(), duration: 1400 };
        controls.addEventListener("start", () => {
          rt.interacted = true;
          rt.flight = null;
        });
        element.appendChild(renderer.domElement);
        observer.observe(element);
        runtime.current = rt;
        setState("ready");

        const ray = new THREE.Raycaster();
        ray.params.Points!.threshold = extent * 0.02;
        const measured: THREE.Vector3[] = [];
        let down = { x: 0, y: 0 };
        renderer.domElement.addEventListener("pointerdown", (e) => (down = { x: e.clientX, y: e.clientY }));
        renderer.domElement.addEventListener("click", (event) => {
          if (Math.hypot(event.clientX - down.x, event.clientY - down.y) > 4) return; // a drag, not a click
          const { tool, selectedId, hazards, onPick, scene: current } = latest.current;
          if (tool === "none" || !current) return;
          const rect = renderer.domElement.getBoundingClientRect();
          ray.setFromCamera(new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1), camera);
          const hit = ray.intersectObject(points)[0];
          if (!hit || hit.index === undefined) return;
          // Use the hit vertex itself: hit.point lies on the ray and can fall outside the scene bounds.
          const local = new THREE.Vector3().fromBufferAttribute(points.geometry.getAttribute("position"), hit.index);
          if (tool === "place") {
            const selected = hazards.find((h) => h.id === selectedId);
            if (selected)
              onPick({
                scene_id: current.id,
                position_world: local.toArray() as [number, number, number],
                method: "reviewer_corrected",
                source_photo_ids: selected.evidence.map((e) => e.photo_id),
                uncertainty: "Punto scelto dal revisore: verificare sulla foto sorgente.",
              });
            return;
          }
          // Measure: two clicks on the cloud, reported only with a calibrated scale.
          if (measured.length === 2) measured.length = 0;
          measured.push(group.localToWorld(local.clone()));
          measure.clear();
          for (const p of measured) {
            const dot = new THREE.Mesh(new THREE.SphereGeometry(extent * 0.012, 16, 16), new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false }));
            dot.position.copy(p);
            measure.add(dot);
          }
          if (measured.length === 2) {
            measure.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(measured), new THREE.LineBasicMaterial({ color: 0xffffff, depthTest: false })));
            const raw = group.worldToLocal(measured[0].clone()).distanceTo(group.worldToLocal(measured[1].clone()));
            setMeasurement(current.scale_factor ? `${Math.round(raw * current.scale_factor * 100)} cm` : null);
            measure.userData.mid = measured[0].clone().add(measured[1]).multiplyScalar(0.5);
          } else {
            setMeasurement(null);
            measure.userData.mid = null;
          }
        });

        const projected = new THREE.Vector3();
        const place = (el: HTMLElement | null | undefined, world: THREE.Vector3 | null | undefined) => {
          if (!el) return;
          if (!world) return void (el.style.visibility = "hidden");
          projected.copy(world).project(camera);
          const visible = projected.z < 1 && Math.abs(projected.x) < 1.2 && Math.abs(projected.y) < 1.2;
          el.style.visibility = visible ? "visible" : "hidden";
          el.style.transform = `translate(-50%, -50%) translate(${((projected.x + 1) / 2) * element.clientWidth}px, ${((1 - projected.y) / 2) * element.clientHeight}px)`;
        };
        const animate = (now: number) => {
          if (disposed) return;
          if (rt.flight) {
            const t = Math.min((now - rt.flight.start) / rt.flight.duration, 1);
            const k = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
            camera.position.lerpVectors(rt.flight.from[0], rt.flight.to[0], k);
            controls.target.lerpVectors(rt.flight.from[1], rt.flight.to[1], k);
            if (t === 1) rt.flight = null;
          }
          if (appliedInset !== latest.current.rightInset) frameCamera();
          controls.autoRotate = !rt.interacted && !rt.flight && !latest.current.selectedId && controls.enableRotate;
          controls.update();
          for (const [id, el] of tagEls.current) place(el, rt.tags.get(id));
          place(measureLabel.current, measure.userData.mid);
          renderer.render(world, camera);
          frame = requestAnimationFrame(animate);
        };
        frame = requestAnimationFrame(animate);
      } catch {
        if (!disposed) setState("error");
      }
    })();

    return () => {
      disposed = true;
      abort.abort();
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      world.traverse((o) => {
        if (o instanceof THREE.Mesh || o instanceof THREE.Points || o instanceof THREE.Line) {
          o.geometry.dispose();
          const material = o.material as THREE.Material & { map?: THREE.Texture | null };
          material.map?.dispose();
          material.dispose();
        }
      });
      renderer.dispose();
      renderer.domElement.remove();
      runtime.current = null;
      setMeasurement(null);
      setState("idle");
    };
    // Keyed on identity + asset: polling returns new array instances for the same scene.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene?.id, scene?.ply_url]);

  // Tags: positions and stems follow hazards/decisions without reloading the cloud.
  useEffect(() => {
    const rt = runtime.current;
    if (!rt || state !== "ready") return;
    rt.stems.children.forEach((o) => {
      const line = o as THREE.Line;
      line.geometry.dispose();
      (line.material as THREE.Material).dispose();
    });
    rt.stems.clear();
    rt.tags.clear();
    const lift = rt.extent * 0.14;
    for (const h of hazards) {
      const e = effective(h);
      if (!e.anchor || e.anchor.scene_id !== scene?.id || h.review?.decision === "reject") continue;
      const base = rt.group.localToWorld(new THREE.Vector3().fromArray(e.anchor.box_world?.center ?? e.anchor.position_world));
      const disc = base.clone().add(new THREE.Vector3(0, lift, 0));
      rt.tags.set(h.id, disc);
      const color = new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue(priorityMeta[e.priority].color.slice(4, -1)).trim());
      rt.stems.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([base, disc]), new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.85 })));
    }
  }, [hazards, scene?.id, state]);

  // Selection: glide to the tag, keeping the current viewing direction.
  useEffect(() => {
    const rt = runtime.current;
    if (!rt || state !== "ready" || !selectedId) return;
    const tag = rt.tags.get(selectedId);
    if (!tag) return;
    const target = tag.clone().setY(tag.y - rt.extent * 0.1);
    const direction = rt.camera.position.clone().sub(rt.controls.target).setY(0).normalize();
    const position = target.clone().add(direction.multiplyScalar(rt.span * 0.55)).add(new THREE.Vector3(0, rt.span * 0.35, 0));
    rt.flight = { from: [rt.camera.position.clone(), rt.controls.target.clone()], to: [position, target], start: performance.now(), duration: 1100 };
  }, [selectedId, state]);

  // View mode: dollhouse orbit or top-down plan.
  useEffect(() => {
    const rt = runtime.current;
    if (!rt || state !== "ready") return;
    rt.controls.enableRotate = mode === "dollhouse";
    rt.interacted = true;
    const [position, target] = homeView(rt, mode);
    rt.flight = { from: [rt.camera.position.clone(), rt.controls.target.clone()], to: [position, target], start: performance.now(), duration: 1200 };
  }, [mode, state]);

  useEffect(() => {
    const rt = runtime.current;
    if (tool !== "measure" && rt) {
      rt.measure.clear();
      rt.measure.userData.mid = null;
    }
  }, [tool]);

  const tagged = hazards.filter((h) => effective(h).anchor?.scene_id === scene?.id && h.review?.decision !== "reject");

  return (
    <div className={`absolute inset-0 overflow-hidden bg-[radial-gradient(ellipse_at_50%_40%,#2a2e35_0%,#121418_60%,#0a0b0d_100%)] ${tool !== "none" ? "cursor-crosshair" : "cursor-grab active:cursor-grabbing"}`}>
      <div ref={mount} className="absolute inset-0" aria-label="Casa in 3D" />
      <div className="pointer-events-none absolute inset-0">
        {state === "ready" &&
          tagged.map((h) => {
            const e = effective(h);
            const selected = h.id === selectedId;
            return (
              <button
                key={h.id}
                ref={(el) => {
                  if (el) tagEls.current.set(h.id, el);
                  else tagEls.current.delete(h.id);
                }}
                onClick={() => onSelect(h.id)}
                style={{ visibility: "hidden", ["--tag" as string]: priorityMeta[e.priority].color }}
                className="group pointer-events-auto absolute left-0 top-0 flex items-center"
                aria-label={categoryLabel[e.category] ?? e.category}
              >
                <span className={`relative grid place-items-center rounded-full border-2 border-white bg-[var(--tag)] shadow-[0_2px_10px_rgba(0,0,0,0.5)] transition-all ${selected ? "h-6 w-6" : "h-4 w-4 group-hover:h-5 group-hover:w-5"}`}>
                  {selected && <span className="absolute inset-0 animate-ping rounded-full bg-[var(--tag)] opacity-60" />}
                </span>
                <span className={`absolute left-full ml-2 whitespace-nowrap rounded-md bg-black/75 px-2 py-1 text-xs text-white backdrop-blur transition-opacity ${selected ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
                  {categoryLabel[e.category] ?? e.category}
                </span>
              </button>
            );
          })}
        <div ref={measureLabel} style={{ visibility: "hidden" }} className="absolute left-0 top-0 rounded-md bg-white px-2 py-1 font-mono text-xs font-medium text-black shadow">
          {measurement ?? "scala non calibrata"}
        </div>
      </div>
      {state === "loading" && <div className="absolute inset-0 grid place-items-center text-sm text-white/60">Apertura della casa…</div>}
      {state === "error" && <div className="absolute inset-0 grid place-items-center text-sm text-white/60">Vista 3D non disponibile. La revisione resta possibile dalle foto.</div>}
      {!scene?.ply_url && <div className="absolute inset-0 grid place-items-center text-sm text-white/60">Questa stanza non ha una ricostruzione 3D. Usa la vista foto.</div>}
    </div>
  );
}
