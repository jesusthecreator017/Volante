// Small motion helpers. Everything is skipped (final state at once) when the visitor prefers reduced motion.

export const calm = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Adds `.in` to every `.reveal` under `root` as it scrolls into view (CSS does the motion). Returns a cleanup. */
export function reveal(root: HTMLElement): () => void {
  const els = [...root.querySelectorAll<HTMLElement>(".reveal")];
  if (!("IntersectionObserver" in window)) {
    for (const el of els) el.classList.add("in");
    return () => {};
  }
  const io = new IntersectionObserver(
    (es) => {
      for (const e of es) if (e.isIntersecting) (e.target.classList.add("in"), io.unobserve(e.target));
    },
    { rootMargin: "0px 0px -6% 0px" },
  );
  for (const el of els) io.observe(el);
  return () => io.disconnect();
}

/** Counts `el` up from 0 to `to` the first time it is visible (~0.9 s, ease-out). The final text is set up front for
 *  screen readers and no-JS-observer browsers, then animated. */
export function countUp(el: HTMLElement, to: number, fmt: (x: number) => string = (x) => String(Math.round(x))): HTMLElement {
  el.textContent = fmt(to);
  if (calm() || !("IntersectionObserver" in window)) return el;
  const io = new IntersectionObserver((es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    io.disconnect();
    const t0 = performance.now();
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / 900);
      el.textContent = fmt(to * (1 - (1 - p) ** 3));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
  io.observe(el);
  return el;
}
