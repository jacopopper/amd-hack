"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import PatientSpace from "@/components/PatientSpace";

// Static export cannot prerender unknown IDs, so the patient is chosen by ?id=.
function FromQuery() {
  const id = useSearchParams().get("id");
  if (!id) return <div className="grid h-full place-items-center bg-[#0a0b0d] text-sm text-white/50">No patient selected.</div>;
  return <PatientSpace key={id} id={id} />;
}

export default function PatientPage() {
  return (
    <Suspense>
      <FromQuery />
    </Suspense>
  );
}
