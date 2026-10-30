// Small dependency-free SVG charts: line (with crosshair) and grouped bars (with per-bar hover).
// Colors come from CSS custom properties so light/dark switch without re-coding.
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const el = (tag, attrs = {}, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  };

  function tooltip(host) {
    let t = host.querySelector(".tip");
    if (!t) { t = document.createElement("div"); t.className = "tip"; t.setAttribute("role", "status"); host.appendChild(t); }
    return {
      show(html, x, y) {
        t.innerHTML = html;
        const w = t.offsetWidth, hw = host.clientWidth;
        let left = x + 14;
        if (left + w > hw) left = Math.max(0, x - w - 14);
        t.style.left = left + "px";
        t.style.top = Math.max(0, y - 10) + "px";
        t.classList.add("on");
      },
      hide() { t.classList.remove("on"); },
    };
  }
  const row = (color, label, val) =>
    `<div class="row"><i class="dot" style="background:${color}"></i><span>${label}</span><span>${val}</span></div>`;

  function niceTicks(min, max, n = 5) {
    const span = max - min || 1;
    const step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }

  // ---------------------------------------------------------------- line chart
  // opts: { x: Date[], series: [{name, color(var), values, dash}], log, yFmt, xFmt, height }
  function line(host, opts) {
    const draw = () => {
      host.querySelectorAll("svg").forEach((s) => s.remove());
      const W = host.clientWidth, H = opts.height || 300;
      const m = { t: 10, r: 16, b: 28, l: 44 };
      const iw = W - m.l - m.r, ih = H - m.t - m.b;
      const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": opts.label || "" });
      host.prepend(svg);
      const g = el("g", { transform: `translate(${m.l},${m.t})` }, svg);
      const xs = opts.x, n = xs.length;
      const all = opts.series.flatMap((s) => s.values);
      const f = opts.log ? Math.log2 : (v) => v;
      let lo = Math.min(...all.map(f)), hi = Math.max(...all.map(f));
      const pad = (hi - lo) * 0.06; lo -= pad; hi += pad;
      const X = (i) => (i / (n - 1)) * iw;
      const Y = (v) => ih - ((f(v) - lo) / (hi - lo)) * ih;

      const ticks = opts.log
        ? [0.5, 1, 2, 4, 8].filter((v) => f(v) >= lo && f(v) <= hi)
        : niceTicks(lo, hi);
      ticks.forEach((v) => {
        const y = Y(v);
        el("line", { x1: 0, x2: iw, y1: y, y2: y, class: v === 1 && opts.log ? "base-l" : "grid-l" }, g);
        el("text", { x: -8, y: y + 4, "text-anchor": "end" }, g).textContent = opts.yFmt(v);
      });
      // year ticks
      const step = W < 560 ? 2 : 1;
      let last = -1;
      xs.forEach((d, i) => {
        const yr = d.getFullYear();
        if (yr !== last && (yr % step === 0)) {
          last = yr;
          el("text", { x: X(i), y: ih + 20, "text-anchor": "middle" }, g).textContent = yr;
        } else if (yr !== last) last = yr;
      });

      opts.series.forEach((s) => {
        const d = s.values.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
        el("path", { d, fill: "none", stroke: css(s.color), "stroke-width": 2, "stroke-linejoin": "round",
          "stroke-linecap": "round", "stroke-dasharray": s.dash ? "5 4" : "none" }, g);
      });
      const cross = el("line", { y1: 0, y2: ih, class: "cross", opacity: 0 }, g);
      const dots = opts.series.map((s) => el("circle", { r: 4, fill: css(s.color), stroke: css("--surface"), "stroke-width": 2, opacity: 0 }, g));
      const tip = tooltip(host);
      const hit = el("rect", { width: iw, height: ih, fill: "transparent", style: "cursor:crosshair" }, g);
      const move = (ev) => {
        const r = svg.getBoundingClientRect();
        const px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left - m.l;
        const i = Math.max(0, Math.min(n - 1, Math.round((px / iw) * (n - 1))));
        cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("opacity", 1);
        opts.series.forEach((s, k) => { dots[k].setAttribute("cx", X(i)); dots[k].setAttribute("cy", Y(s.values[i])); dots[k].setAttribute("opacity", 1); });
        const html = `<b>${opts.xFmt(xs[i])}</b>` + opts.series.map((s) => row(css(s.color), s.name, opts.tipFmt(s.values[i]))).join("");
        tip.show(html, X(i) + m.l, m.t + 8);
      };
      const leave = () => { tip.hide(); cross.setAttribute("opacity", 0); dots.forEach((d) => d.setAttribute("opacity", 0)); };
      hit.addEventListener("pointermove", move);
      hit.addEventListener("pointerleave", leave);
    };
    draw();
    observe(host, draw);
  }

  // ---------------------------------------------------------------- bar chart
  // opts: { cats: string[], series: [{name, color, values}], yFmt, tipFmt, height, highlight }
  function bars(host, opts) {
    const draw = () => {
      host.querySelectorAll("svg").forEach((s) => s.remove());
      const W = host.clientWidth, H = opts.height || 260;
      const m = { t: 12, r: 8, b: 28, l: 44 };
      const iw = W - m.l - m.r, ih = H - m.t - m.b;
      const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": opts.label || "" });
      host.prepend(svg);
      const g = el("g", { transform: `translate(${m.l},${m.t})` }, svg);
      const all = opts.series.flatMap((s) => s.values).concat(0);
      let lo = Math.min(...all), hi = Math.max(...all);
      const pad = (hi - lo) * 0.08; hi += pad; if (lo < 0) lo -= pad;
      const ticks = niceTicks(lo, hi);
      lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
      const Y = (v) => ih - ((v - lo) / (hi - lo)) * ih;
      ticks.forEach((v) => {
        el("line", { x1: 0, x2: iw, y1: Y(v), y2: Y(v), class: v === 0 ? "base-l" : "grid-l" }, g);
        el("text", { x: -8, y: Y(v) + 4, "text-anchor": "end" }, g).textContent = opts.yFmt(v);
      });
      if (lo < 0 && !ticks.includes(0)) el("line", { x1: 0, x2: iw, y1: Y(0), y2: Y(0), class: "base-l" }, g);

      const nc = opts.cats.length, ns = opts.series.length;
      const band = iw / nc, inner = band * (ns > 1 ? 0.78 : 0.62), gap = 2;
      const bw = Math.max(3, (inner - gap * (ns - 1)) / ns);
      const thinLabels = W < 520 && nc > 8;
      const tip = tooltip(host);
      opts.cats.forEach((c, ci) => {
        const x0 = ci * band + (band - inner) / 2;
        if (!thinLabels || ci % 2 === 0)
          el("text", { x: ci * band + band / 2, y: ih + 20, "text-anchor": "middle" }, g).textContent = c;
        opts.series.forEach((s, si) => {
          const v = s.values[ci];
          if (v == null) return;
          const x = x0 + si * (bw + gap), y0 = Y(0), y1 = Y(v);
          const top = Math.min(y0, y1), h = Math.max(1, Math.abs(y1 - y0)), r = Math.min(4, bw / 2, h);
          // rounded data-end, square at the baseline
          const d = v >= 0
            ? `M${x},${y0}V${top + r}Q${x},${top} ${x + r},${top}H${x + bw - r}Q${x + bw},${top} ${x + bw},${top + r}V${y0}Z`
            : `M${x},${y0}V${top + h - r}Q${x},${top + h} ${x + r},${top + h}H${x + bw - r}Q${x + bw},${top + h} ${x + bw},${top + h - r}V${y0}Z`;
          const op = opts.highlight != null && opts.highlight !== ci ? 0.45 : 1;
          el("path", { d, fill: css(s.color), opacity: op }, g);
        });
        const hit = el("rect", { x: ci * band, y: 0, width: band, height: ih, fill: "transparent", style: "cursor:default" }, g);
        hit.addEventListener("pointerenter", () => {
          hit.setAttribute("fill", css("--surface-2")); hit.setAttribute("fill-opacity", 0.5);
          g.insertBefore(hit, g.firstChild.nextSibling);
          const html = `<b>${opts.tipTitle ? opts.tipTitle(c) : c}</b>` +
            opts.series.map((s) => row(css(s.color), s.name, opts.tipFmt(s.values[ci]))).join("");
          tip.show(html, ci * band + band / 2 + m.l, m.t + 8);
        });
        hit.addEventListener("pointerleave", () => { hit.setAttribute("fill", "transparent"); tip.hide(); });
      });
    };
    draw();
    observe(host, draw);
  }

  // redraw on resize and theme change
  const redraws = new Set();
  function observe(host, fn) {
    redraws.add(fn);
    let w = host.clientWidth;
    new ResizeObserver(() => { if (host.clientWidth !== w) { w = host.clientWidth; fn(); } }).observe(host);
  }
  window.addEventListener("themechange", () => redraws.forEach((f) => f()));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => redraws.forEach((f) => f()));

  window.Charts = { line, bars };
})();
