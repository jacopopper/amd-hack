"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Avatar } from "@/lib/types";

// three.js loads only when the reviewer asks for the 3D figure.
const AvatarModel = dynamic(() => import("./AvatarModel"), { ssr: false, loading: () => <p className="absolute inset-0 grid place-items-center text-xs text-white/55">Loading model…</p> });

// The confirmed profile as an illustrative figure: preview image first, optional orbitable GLB.
export default function AvatarViewer({ assessmentId, refreshKey }: { assessmentId: string; refreshKey: string }) {
  const [avatar, setAvatar] = useState<{ key: string; value: Avatar } | null>(null);
  const [view3d, setView3d] = useState(false);

  useEffect(() => {
    let current = true;
    api<Avatar>(`/assessments/${assessmentId}/avatar`)
      .then((value) => current && setAvatar({ key: refreshKey, value }))
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [assessmentId, refreshKey]);

  // Ignore a figure fetched for a previous analysis or profile.
  const value = avatar?.key === refreshKey ? avatar.value : null;
  if (!value || (!value.preview_url && !value.glb_url)) return null;
  const showModel = !!value.glb_url && (view3d || !value.preview_url);
  return (
    <section className="mt-4 border-t border-white/10 pt-3" aria-label="Illustrative figure of the profile">
      <div className="flex gap-3">
        <div className="relative aspect-[2/3] w-28 shrink-0 overflow-hidden rounded-lg bg-white/[0.06]">
          {showModel ? (
            <AvatarModel url={value.glb_url!} />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element -- same-origin API image
            <img src={value.preview_url!} width={value.preview_width ?? undefined} height={value.preview_height ?? undefined} alt="Illustrative figure of the person" className="h-full w-full object-contain" />
          )}
        </div>
        <div className="min-w-0 text-[13px] leading-relaxed text-white/55">
          <p>A schematic illustration, not what the person really looks like.{value.has_walker ? " The walker appears because the profile confirms it." : ""}</p>
          {value.glb_url && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {value.preview_url && (
                <button aria-pressed={view3d} onClick={() => setView3d((v) => !v)} className="rounded-lg bg-white/[0.08] px-2.5 py-1 text-xs text-white/80 hover:bg-white/15">
                  {view3d ? "Show image" : "3D view"}
                </button>
              )}
              <a href={value.glb_url} download="avatar.glb" className="rounded-lg bg-white/[0.08] px-2.5 py-1 text-xs text-white/80 hover:bg-white/15">
                Download GLB
              </a>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
