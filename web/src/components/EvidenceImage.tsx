import type { Evidence, Photo } from '../types';

export default function EvidenceImage({photo, evidence}: {photo?: Photo; evidence?: Evidence}) {
  if (!photo) return <p className="empty">Seleziona una foto per esaminare l’evidenza.</p>;
  const box = evidence?.photo_id === photo.id ? evidence.box : null;
  return <figure className="evidence"><div className="evidence-frame"><img src={photo.asset_url} alt="Foto dimostrativa, evidenza del rilievo selezionato" />
    {box && <svg aria-label="Regione di evidenza" viewBox={`0 0 ${photo.width} ${photo.height}`}><rect x={box[0]} y={box[1]} width={box[2] - box[0]} height={box[3] - box[1]} fill="rgb(218 111 70 / 12%)" stroke="#ed9b60" strokeWidth={Math.max(3, photo.width / 220)} /></svg>}</div>
    <figcaption>{box ? 'Regione indicata in coordinate della foto originale.' : 'Nessuna regione localizzata per questa evidenza.'}</figcaption></figure>;
}
