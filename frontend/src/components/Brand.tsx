export default function Brand({ size = "md" }: { size?: "md" | "lg" }) {
  return (
    <span className="flex items-center gap-2.5">
      <svg viewBox="0 0 24 24" className={size === "lg" ? "h-7 w-7" : "h-6 w-6"} fill="none" stroke="white" strokeWidth={1.8} strokeLinejoin="round">
        <path d="M3.5 10.5 12 4l8.5 6.5V20h-17z" />
        <circle cx="12" cy="14" r="2.2" fill="white" stroke="none" />
      </svg>
      <span className={`${size === "lg" ? "text-xl" : "text-base"} font-semibold tracking-tight text-white`}>Step Wise</span>
    </span>
  );
}
