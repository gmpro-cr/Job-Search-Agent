/**
 * motion.js — quiet, snappy interaction layer.
 *
 *   .reveal-on-scroll          fade-up when intersecting viewport
 *   .underline-sweep           triggers a sweep underline on reveal
 *   [data-count-to="N"]        animate from 0 to N over ~900ms
 *   .magnetic                  primary CTA subtly tracks the cursor
 *
 * Loads at end of base.html. All helpers honour prefers-reduced-motion;
 * if reduced motion is requested, we skip the animations and just apply
 * the final state.
 */
(function () {
  'use strict';

  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ─── 1. Reveal-on-scroll (IntersectionObserver) ─────────────────
  function initReveal() {
    const els = document.querySelectorAll('.reveal-on-scroll, .underline-sweep');
    if (!els.length) return;
    if (reduce || !('IntersectionObserver' in window)) {
      els.forEach(el => el.classList.add('is-visible'));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: '0px 0px -8% 0px' }
    );
    els.forEach(el => io.observe(el));
  }

  // ─── 2. Count-up numbers ────────────────────────────────────────
  function initCounters() {
    const targets = document.querySelectorAll('[data-count-to]');
    if (!targets.length) return;
    const animate = (el) => {
      const target = parseFloat(el.dataset.countTo) || 0;
      const decimals = parseInt(el.dataset.countDecimals || '0', 10);
      const duration = parseInt(el.dataset.countDuration || '900', 10);
      const prefix = el.dataset.countPrefix || '';
      const suffix = el.dataset.countSuffix || '';
      if (reduce) {
        el.textContent = prefix + target.toFixed(decimals) + suffix;
        return;
      }
      const start = performance.now();
      const startVal = 0;
      const fmt = new Intl.NumberFormat(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      });
      const tick = (now) => {
        const t = Math.min(1, (now - start) / duration);
        // cubic-out
        const eased = 1 - Math.pow(1 - t, 3);
        const value = startVal + (target - startVal) * eased;
        el.textContent = prefix + fmt.format(value) + suffix;
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    };
    // Only animate when intersecting so off-screen counters don't fire wasted frames
    if (!('IntersectionObserver' in window)) {
      targets.forEach(animate);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            animate(entry.target);
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.4 }
    );
    targets.forEach(el => io.observe(el));
  }

  // ─── 3. Magnetic hover ──────────────────────────────────────────
  // Subtle: max ~6px translate. Only on coarse-pointer = false (mouse).
  // Delayed slightly so the .page-body page-rise animation (320ms) fully
  // settles before we start writing transforms onto child elements; otherwise
  // the magnetic transform fights the parent's translateY and snaps in.
  function initMagnetic() {
    if (reduce) return;
    const coarse = window.matchMedia('(pointer: coarse)').matches;
    if (coarse) return;
    setTimeout(() => {
      document.querySelectorAll('.magnetic').forEach(el => {
        const strength = parseFloat(el.dataset.magneticStrength || '0.18');
        el.addEventListener('pointermove', (e) => {
          const r = el.getBoundingClientRect();
          const dx = (e.clientX - r.left - r.width / 2) * strength;
          const dy = (e.clientY - r.top - r.height / 2) * strength;
          el.style.transform = `translate(${dx}px, ${dy}px)`;
        });
        el.addEventListener('pointerleave', () => {
          el.style.transform = '';
        });
      });
    }, 360);
  }

  // ─── Boot ───────────────────────────────────────────────────────
  function boot() {
    initReveal();
    initCounters();
    initMagnetic();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
