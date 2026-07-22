"use client";
import { useState } from "react";

/**
 * Brand mark. Uses your real logo at `public/logo.png` if present; otherwise
 * falls back to an on-brand SVG "S" so nothing ever renders broken.
 *
 * TO USE YOUR EXACT LOGO: save the Syncbroad image as
 *   frontend/dashboard/public/logo.png
 * (a `public/logo.svg` also works — change the src below). No code changes needed.
 */
export function LogoMark({ className }: { className?: string }) {
  const [failed, setFailed] = useState(false);
  const base = process.env.NEXT_PUBLIC_BASE_PATH || "";

  if (!failed) {
    // eslint-disable-next-line @next/next/no-img-element
    return (
      <img
        src={`${base}/logo.png`}
        alt="Syncbroad Networks"
        className={className}
        onError={() => setFailed(true)}
      />
    );
  }

  // Fallback SVG mark (cyan "S" ribbon) — matches the Syncbroad brand colour.
  return (
    <svg viewBox="0 0 40 40" className={className} fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
      <defs>
        <linearGradient id="sb-s" x1="6" y1="6" x2="34" y2="34" gradientUnits="userSpaceOnUse">
          <stop stopColor="#3BB4E8" />
          <stop offset="1" stopColor="#1E88C7" />
        </linearGradient>
      </defs>
      <path
        d="M29 13c0-4-4-6-9-6s-9 2-9 6c0 8 18 4 18 13 0 4-4 6-9 6s-9-2-9-6"
        stroke="url(#sb-s)"
        strokeWidth="4.5"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}
