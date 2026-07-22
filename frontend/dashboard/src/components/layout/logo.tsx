/**
 * Syncbroad brand mark — the real logo at public/logo.png.
 * To change it, just replace that file.
 */
export function LogoMark({ className }: { className?: string }) {
  const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={`${base}/logo.png`} alt="Syncbroad Networks" className={className} />;
}
