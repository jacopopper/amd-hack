"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Box, Camera, Check, ChevronRight, Focus, LockKeyhole, RotateCcw, ShieldAlert, Sparkles } from "lucide-react";
import Brand from "@/components/Brand";
import "./capture.css";

type View = { id: string; image: string };
type Finding = { id: string; label: string; color: string; view_index: number; photo_id: string; box: [number, number, number, number]; observation: string; action: string; uncertainty: string; provenance: string };
type Mode = "splats" | "surface" | "points" | "photo";
const asset = (path: string) => `/capture-data/${path}`;
const modes: [Mode, string][] = [["splats", "Gaussian splats"], ["surface", "Mesh"], ["points", "Points"], ["photo", "Photos"]];

// The review UI stays in the current app. Only the version-pinned WebGL engine
// is isolated in a same-origin iframe, so its Three r170 does not conflict with r186.
export default function CaptureReview() {
  const frame = useRef<HTMLIFrameElement>(null);
  const [views, setViews] = useState<View[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [index, setIndex] = useState(23);
  const [mode, setMode] = useState<Mode>("splats");
  const [selected, setSelected] = useState("carpet");
  const [ready, setReady] = useState(false);
  const [splatsReady, setSplatsReady] = useState(false);
  const [status, setStatus] = useState("Loading observed surfaces…");
  const [error, setError] = useState("");
  const [reviewed, setReviewed] = useState<string[]>([]);
  const current = views[index];
  const finding = findings.find((item) => item.id === selected);
  const regions = findings.filter((item) => item.view_index === index);

  function send(command: Record<string, unknown>) {
    frame.current?.contentWindow?.postMessage({ channel: "step-wise-room", ...command }, location.origin);
  }

  useEffect(() => {
    const abort = new AbortController();
    const get = async (path: string) => {
      const response = await fetch(asset(path), { signal: abort.signal });
      if (!response.ok) throw new Error(`${path}: HTTP ${response.status}. Prepare the local assets before opening the room.`);
      return response.json();
    };
    Promise.all([get("viewpoints.json"), get("hazards.json")])
      .then(([data, hazards]) => { setViews(data.views); setFindings(hazards); })
      .catch((e) => { if (!abort.signal.aborted) setError((e as Error).message); });
    return () => abort.abort();
  }, []);

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== frame.current?.contentWindow || event.data?.channel !== "step-wise-room") return;
      const message = event.data;
      if (message.type === "ready") setReady(true);
      if (message.type === "loading-splats") setSplatsReady(false);
      if (message.type === "splats-ready") { setSplatsReady(true); setStatus("757,792 Gaussian splats · 9,000 training steps"); }
      if (message.type === "view") setIndex(message.index);
      if (message.type === "select") setSelected(message.id);
      if (message.type === "status") setStatus(message.text);
      if (message.type === "error") setError(message.text);
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, []);

  useEffect(() => { if (ready) send({ type: "mode", value: mode }); }, [ready, mode, splatsReady]);
  useEffect(() => { if (ready) send({ type: "highlight", id: selected }); }, [ready, selected]);

  function photo(next: number) {
    if (!views.length) return;
    const value = (next + views.length) % views.length;
    setIndex(value);
    send({ type: "view", index: value });
  }
  function focus(item: Finding) {
    setSelected(item.id);
    photo(item.view_index);
  }

  return (
    <main className="capture-review" lang="en">
      <header className="capture-header">
        <Brand />
        <span className="capture-breadcrumb">Local capture <ChevronRight size={14} /> Living room & kitchen</span>
        <span className="capture-private"><LockKeyhole size={13} /> Only on this device</span>
      </header>

      <div className="capture-workspace">
        <section className="capture-viewer" aria-label="Room reconstruction">
          <iframe ref={frame} title="Interactive 3D room" src="/local-room-viewer.html" className="capture-engine" />
          {mode === "photo" && current && (
            <div className="capture-photo">
              <div className="capture-photo-image">
                {/* eslint-disable-next-line @next/next/no-img-element -- private local source */}
                <img src={asset(current.image)} alt={`Source photo ${current.id}`} />
                {regions.map((item) => (
                  <button key={item.id} onClick={() => setSelected(item.id)} className="capture-photo-region"
                    aria-label={`Demo hazard: ${item.label}`}
                    style={{ left: `${item.box[0] * 100}%`, top: `${item.box[1] * 100}%`, width: `${(item.box[2] - item.box[0]) * 100}%`, height: `${(item.box[3] - item.box[1]) * 100}%`, borderColor: item.color }}>
                    <span style={{ background: item.color }}>{item.label} · demo</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="capture-scene-heading"><span className="capture-eyebrow">YOUR SPACE, FROM EVERY ANGLE</span><h1>Living room & kitchen</h1><p>39 real photos. A clearer perspective.</p></div>
          <div className="capture-modebar" role="group" aria-label="Display mode">
            {modes.map(([value, label]) => <button key={value} disabled={value === "splats" && !splatsReady} aria-pressed={mode === value} onClick={() => setMode(value)}>{value === "photo" ? <Camera size={14} /> : value === "splats" ? <Sparkles size={14} /> : <Box size={14} />}{label}</button>)}
          </div>
          {mode === "splats" && <span className="capture-polish" title="Observed floor and ceiling surfaces are regularized. Unknown areas are not filled in."><Sparkles size={14} /> Polished surfaces</span>}
          {error && <div className="capture-error" role="alert">{error}</div>}
          <div className="capture-view-bottom">
            <p>{mode === "photo" ? "Original photo · manually marked demo regions" : "Drag to explore · scroll to zoom"}</p>
            <button onClick={() => send({ type: "reset" })} disabled={!ready || mode === "photo"}><RotateCcw size={14} /> Reset view</button>
          </div>
        </section>

        <aside className="capture-sidebar">
          <div className="capture-sidebar-title"><span className="capture-eyebrow">ROOM REVIEW</span><h2>Two hazards to check</h2><p>A safer home starts with the details.</p></div>
          <div className="capture-demo-notice"><ShieldAlert size={17} /><div><strong>Demo findings · mocked</strong><p>Cord and carpet manually marked as requested. Not JEV detections. Approximate 3D pins.</p></div></div>
          <div className="capture-findings">
            {findings.map((item, n) => <button key={item.id} className={`capture-finding ${item.id === selected ? "selected" : ""}`} aria-pressed={item.id === selected} onClick={() => focus(item)}>
              <span className="capture-finding-number" style={{ background: item.color }}>{n + 1}</span><span><strong>{item.label}</strong><small>{reviewed.includes(item.id) ? "Seen in this session" : "Check against the source photo"}</small></span><ChevronRight size={15} />
            </button>)}
          </div>
          {finding && <article className="capture-detail">
            <div className="capture-detail-photo">
              {/* eslint-disable-next-line @next/next/no-img-element -- private local evidence */}
              <img src={asset(views[finding.view_index]?.image ?? "")} alt={`Photo evidence: ${finding.label}`} />
              <div className="capture-evidence-box" style={{ left: `${finding.box[0] * 100}%`, top: `${finding.box[1] * 100}%`, width: `${(finding.box[2] - finding.box[0]) * 100}%`, height: `${(finding.box[3] - finding.box[1]) * 100}%`, borderColor: finding.color }} />
              <span>{finding.photo_id} · original photo</span>
            </div>
            <h3>{finding.label}</h3><p>{finding.observation}</p>
            <h4>A small improvement</h4><p>{finding.action}</p>
            <div className="capture-detail-actions"><button onClick={() => { focus(finding); setMode("photo"); }}><Camera size={14} /> Check photo</button><button onClick={() => { focus(finding); setMode("splats"); }} disabled={!splatsReady}><Focus size={14} /> View in 3D</button></div>
            <button className="capture-reviewed" aria-pressed={reviewed.includes(finding.id)} onClick={() => setReviewed((old) => old.includes(finding.id) ? old.filter((id) => id !== finding.id) : [...old, finding.id])}><Check size={15} /> {reviewed.includes(finding.id) ? "Seen · undo" : "Mark as seen"}</button>
            <small className="capture-review-note">Session reminder only; not saved or clinically validated.</small>
          </article>}
        </aside>
      </div>

      <footer className="capture-footer">
        <div className="capture-photo-nav">
          <button aria-label="Previous photo" onClick={() => photo(index - 1)} disabled={!views.length}><ArrowLeft size={17} /></button>
          <label className="sr-only" htmlFor="capture-view">Photographic viewpoint</label>
          <select id="capture-view" value={index} onChange={(e) => photo(Number(e.target.value))}>{views.map((item, i) => <option key={item.id} value={i}>{i + 1} / {views.length} · {item.id}</option>)}</select>
          <button aria-label="Next photo" onClick={() => photo(index + 1)} disabled={!views.length}><ArrowRight size={17} /></button>
        </div>
        <div className="capture-quickviews">{[["Kitchen", 23], ["Sofa", 33], ["Dining", 8]].map(([label, i]) => <button key={label} onClick={() => photo(Number(i))}>{label}</button>)}</div>
        <p className="capture-model-status" role="status">{status}</p>
      </footer>
      <div className="capture-disclaimer">Observed reconstruction, not a survey. {mode === "splats" ? "Floor and ceiling: derived geometric regularization, not measured. " : ""}Unphotographed areas remain unknown. Relative scale and hazard positions are not validated. No external uploads.</div>
    </main>
  );
}
