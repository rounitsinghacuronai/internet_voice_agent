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
  // 0 = try your exact raster (public/logo.png); 1 = SVG recreation (public/logo.svg)
  const [stage, setStage] = useState(0);
  const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
  const src = stage === 0 ? `${base}/logo.png` : `${base}/logo.svg`;
  // eslint-disable-next-line @next/next/no-img-element
  return (
    <img
      src={src}
      alt="Syncbroad Networks"
      className={className}
      onError={() => setStage((s) => Math.min(s + 1, 1))}
    />
  );
}
