import type { Metadata } from "next";
import CaptureReview from "@/components/capture/CaptureReview";

export const metadata: Metadata = {
  title: "Step Wise · Your room",
  robots: { index: false, follow: false },
};

export default function CapturePage() {
  return <CaptureReview />;
}
