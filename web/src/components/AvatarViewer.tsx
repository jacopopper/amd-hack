import { lazy, Suspense, useEffect, useState } from 'react';
import { api } from '../api';
import type { Avatar } from '../types';

const AvatarModel = lazy(() => import('./AvatarModel'));

// Drop-in: <AvatarViewer assessmentId={id} refreshKey={anythingThatChangesAfterAnalysis} />
// Shows the preview image straight away; the three.js chunk only loads when "Vista 3D" is chosen.
export default function AvatarViewer({assessmentId, refreshKey}: {assessmentId: string; refreshKey?: string}) {
  const [avatar, setAvatar] = useState<Avatar | null>(null);
  const [view3d, setView3d] = useState(false);
  useEffect(() => {
    let current = true;
    setAvatar(null); setView3d(false);
    api<Avatar>(`/assessments/${assessmentId}/avatar`).then(value => { if (current) setAvatar(value); }).catch(() => undefined);
    return () => { current = false; };
  }, [assessmentId, refreshKey]);
  if (!avatar || (!avatar.preview_url && !avatar.glb_url)) return null;
  const showModel = !!avatar.glb_url && (view3d || !avatar.preview_url);
  return <section className="avatar-card" aria-label="Figura illustrativa del profilo">
    <div className="avatar-stage">{showModel
      ? <Suspense fallback={<p className="avatar-message">Caricamento del modello…</p>}><AvatarModel url={avatar.glb_url!} /></Suspense>
      : <img src={avatar.preview_url!} width={avatar.preview_width ?? undefined} height={avatar.preview_height ?? undefined} alt="Figura illustrativa della persona" />}</div>
    <div className="avatar-side"><span className="eyebrow">Il profilo, in figura</span>
      <p>Illustrazione schematica, non l’aspetto reale della persona.{avatar.has_walker ? ' Il deambulatore compare perché confermato nel profilo.' : ''}</p>
      {avatar.glb_url && <div className="actions">
        {avatar.preview_url && <button className="secondary" aria-pressed={view3d} onClick={() => setView3d(!view3d)}>{view3d ? 'Mostra immagine' : 'Vista 3D'}</button>}
        <a className="button secondary" href={avatar.glb_url} download="avatar.glb">Scarica GLB</a></div>}</div>
  </section>;
}
