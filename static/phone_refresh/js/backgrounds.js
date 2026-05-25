/* =====================================================================
   Pro Sim — background variants
   Each variant is a { seed(W,H), draw(now, ctx, api) } pair.
   Switch via:  window.BG.set("stars" | "network" | "dots")
   Persists the choice in localStorage ("rn_bg").
   ===================================================================== */
(function () {
    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = document.getElementById("bg-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");

    let W = 0, H = 0;
    let raf = 0;
    let current = null;
    let currentName = "stars";
    let baseColor = "rgba(148,163,255,0.22)";
    let accentPulse = "rgba(253,224,71,1)";
    let accentGlow  = "rgba(250,204,21,0.9)";

    function refreshColor() {
        const s = getComputedStyle(document.documentElement);
        const c = s.getPropertyValue("--dot").trim();
        if (c) baseColor = c;
        const p = s.getPropertyValue("--bg-accent-pulse").trim();
        const g = s.getPropertyValue("--bg-accent-glow").trim();
        if (p) accentPulse = p;
        if (g) accentGlow  = g;
    }

    function rgbaSetAlpha(rgba, a) {
        const m = rgba.match(/rgba?\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
        if (!m) return rgba;
        return "rgba(" + m[1] + "," + m[2] + "," + m[3] + "," + a.toFixed(3) + ")";
    }

    function baseRgb() {
        const m = baseColor.match(/rgba?\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
        return m ? (m[1] + "," + m[2] + "," + m[3]) : "148,163,255";
    }

    function resizeCanvas() {
        W = document.documentElement.clientWidth || window.innerWidth;
        H = document.documentElement.clientHeight || window.innerHeight;
        canvas.width  = W * DPR;
        canvas.height = H * DPR;
        canvas.style.width  = W + "px";
        canvas.style.height = H + "px";
        ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    }

    /* ---------- variant 1: stars + shooting stars ---------- */
    const stars = (() => {
        let items = [];
        let shooters = [];

        function seed(W, H) {
            const target = Math.max(90, Math.min(240, Math.floor((W * H) / 7000)));
            items = new Array(target).fill(0).map(() => ({
                x: Math.random() * W,
                y: Math.random() * H,
                r: Math.random() * 1.2 + 0.25,
                baseA: 0.25 + Math.random() * 0.75,
                tw: 0.5 + Math.random() * 2.2,
                phase: Math.random() * Math.PI * 2
            }));
            shooters = [];
        }

        function spawn(W, H) {
            const fromLeft = Math.random() < 0.5;
            const startX = fromLeft
                ? -40 + Math.random() * W * 0.35
                : W * 0.65 + Math.random() * W * 0.35 + 40;
            const startY = -20 + Math.random() * H * 0.5;
            const angle = fromLeft
                ? Math.PI / 5 + (Math.random() - 0.5) * 0.25
                : Math.PI - Math.PI / 5 + (Math.random() - 0.5) * 0.25;
            const speed = 9 + Math.random() * 6;
            shooters.push({
                x: startX, y: startY,
                vx: Math.cos(angle) * speed,
                vy: Math.sin(angle) * speed,
                life: 0,
                maxLife: 80 + Math.random() * 40,
                width: 1.4 + Math.random() * 0.8
            });
        }

        function draw(now) {
            const t = now * 0.001;
            const rgb = baseRgb();

            for (let i = 0; i < items.length; i++) {
                const s = items[i];
                const a = Math.max(0, Math.min(1, s.baseA * (0.55 + 0.45 * Math.sin(t * s.tw + s.phase))));
                ctx.fillStyle = "rgba(" + rgb + "," + a.toFixed(3) + ")";
                ctx.beginPath();
                ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
                ctx.fill();
            }

            if (shooters.length < 2 && Math.random() < 0.004) spawn(W, H);

            ctx.save();
            ctx.lineCap = "round";
            for (let k = shooters.length - 1; k >= 0; k--) {
                const sh = shooters[k];
                sh.x += sh.vx;
                sh.y += sh.vy;
                sh.life++;

                const offscreen = sh.x < -120 || sh.x > W + 120 || sh.y > H + 120;
                if (sh.life > sh.maxLife || offscreen) {
                    shooters.splice(k, 1);
                    continue;
                }

                const fade = 1 - sh.life / sh.maxLife;
                const tailLen = 14;
                const tx = sh.x - sh.vx * tailLen;
                const ty = sh.y - sh.vy * tailLen;

                const grad = ctx.createLinearGradient(tx, ty, sh.x, sh.y);
                grad.addColorStop(0, "rgba(" + rgb + ",0)");
                grad.addColorStop(1, "rgba(255,255,255," + (0.85 * fade).toFixed(3) + ")");
                ctx.strokeStyle = grad;
                ctx.lineWidth = sh.width;
                ctx.shadowBlur = 10;
                ctx.shadowColor = "rgba(" + rgb + "," + (0.6 * fade).toFixed(3) + ")";
                ctx.beginPath();
                ctx.moveTo(tx, ty);
                ctx.lineTo(sh.x, sh.y);
                ctx.stroke();

                ctx.shadowBlur = 14;
                ctx.shadowColor = "rgba(255,255,255," + (0.8 * fade).toFixed(3) + ")";
                ctx.fillStyle = "rgba(255,255,255," + fade.toFixed(3) + ")";
                ctx.beginPath();
                ctx.arc(sh.x, sh.y, 1.8, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.restore();
        }

        return { seed, draw };
    })();

    /* ---------- variant 2: data network (nodes + golden pulses) ---------- */
    const network = (() => {
        let nodes = [];
        let pulses = [];
        const linkDist = 150;
        const linkDist2 = linkDist * linkDist;

        function seed(W, H) {
            const target = Math.max(40, Math.min(110, Math.floor((W * H) / 18000)));
            nodes = new Array(target).fill(0).map(() => ({
                x: Math.random() * W,
                y: Math.random() * H,
                vx: (Math.random() - 0.5) * 0.22,
                vy: (Math.random() - 0.5) * 0.22,
                r: Math.random() * 1.2 + 0.7
            }));
            pulses = [];
        }

        function spawnPulse(edges) {
            if (!edges.length || pulses.length >= 28) return;
            if (Math.random() > 0.22) return;
            const e = edges[Math.floor(Math.random() * edges.length)];
            pulses.push({
                i: e[0], j: e[1],
                t: 0,
                speed: 0.003 + Math.random() * 0.005
            });
        }

        function draw() {
            for (let i = 0; i < nodes.length; i++) {
                const n = nodes[i];
                n.x += n.vx; n.y += n.vy;
                if (n.x < -20) n.x = W + 20;
                if (n.x > W + 20) n.x = -20;
                if (n.y < -20) n.y = H + 20;
                if (n.y > H + 20) n.y = -20;
            }

            const edges = [];
            ctx.lineWidth = 1;
            for (let i = 0; i < nodes.length; i++) {
                for (let j = i + 1; j < nodes.length; j++) {
                    const a = nodes[i], b = nodes[j];
                    const dx = a.x - b.x, dy = a.y - b.y;
                    const d2 = dx * dx + dy * dy;
                    if (d2 < linkDist2) {
                        edges.push([i, j]);
                        const closeness = 1 - d2 / linkDist2;
                        ctx.strokeStyle = rgbaSetAlpha(baseColor, closeness * 0.5);
                        ctx.beginPath();
                        ctx.moveTo(a.x, a.y);
                        ctx.lineTo(b.x, b.y);
                        ctx.stroke();
                    }
                }
            }

            for (let i = 0; i < nodes.length; i++) {
                const n = nodes[i];
                ctx.fillStyle = baseColor;
                ctx.beginPath();
                ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
                ctx.fill();
            }

            spawnPulse(edges);

            ctx.save();
            ctx.lineCap = "round";
            for (let k = pulses.length - 1; k >= 0; k--) {
                const p = pulses[k];
                p.t += p.speed;
                const a = nodes[p.i], b = nodes[p.j];
                if (!a || !b || p.t >= 1) { pulses.splice(k, 1); continue; }
                const dx = a.x - b.x, dy = a.y - b.y;
                if (dx * dx + dy * dy >= linkDist2) { pulses.splice(k, 1); continue; }

                const x = a.x + (b.x - a.x) * p.t;
                const y = a.y + (b.y - a.y) * p.t;
                const tailT = Math.max(0, p.t - 0.28);
                const tx = a.x + (b.x - a.x) * tailT;
                const ty = a.y + (b.y - a.y) * tailT;

                const grad = ctx.createLinearGradient(tx, ty, x, y);
                grad.addColorStop(0, rgbaSetAlpha(accentPulse, 0));
                grad.addColorStop(1, rgbaSetAlpha(accentPulse, 0.85));
                ctx.strokeStyle = grad;
                ctx.lineWidth = 1.6;
                ctx.shadowBlur = 8;
                ctx.shadowColor = accentGlow;
                ctx.beginPath();
                ctx.moveTo(tx, ty);
                ctx.lineTo(x, y);
                ctx.stroke();

                ctx.shadowBlur = 14;
                ctx.fillStyle = accentPulse;
                ctx.beginPath();
                ctx.arc(x, y, 2.4, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.restore();
        }

        return { seed, draw };
    })();

    /* ---------- variant 3: drifting dots + threads (original) ---------- */
    const dots = (() => {
        let items = [];

        function seed(W, H) {
            const target = Math.max(36, Math.min(90, Math.floor((W * H) / 22000)));
            items = new Array(target).fill(0).map(() => ({
                x: Math.random() * W,
                y: Math.random() * H,
                vx: (Math.random() - 0.5) * 0.25,
                vy: (Math.random() - 0.5) * 0.25,
                r: Math.random() * 1.4 + 0.4
            }));
        }

        function draw() {
            const linkDist = 130;
            const linkDist2 = linkDist * linkDist;

            for (let i = 0; i < items.length; i++) {
                const d = items[i];
                d.x += d.vx; d.y += d.vy;
                if (d.x < -20) d.x = W + 20;
                if (d.x > W + 20) d.x = -20;
                if (d.y < -20) d.y = H + 20;
                if (d.y > H + 20) d.y = -20;

                ctx.beginPath();
                ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
                ctx.fillStyle = baseColor;
                ctx.fill();
            }

            ctx.lineWidth = 1;
            for (let i = 0; i < items.length; i++) {
                for (let j = i + 1; j < items.length; j++) {
                    const a = items[i], b = items[j];
                    const dx = a.x - b.x, dy = a.y - b.y;
                    const d2 = dx * dx + dy * dy;
                    if (d2 < linkDist2) {
                        const alpha = 1 - d2 / linkDist2;
                        ctx.strokeStyle = rgbaSetAlpha(baseColor, alpha * 0.5);
                        ctx.beginPath();
                        ctx.moveTo(a.x, a.y);
                        ctx.lineTo(b.x, b.y);
                        ctx.stroke();
                    }
                }
            }
        }

        return { seed, draw };
    })();

    const VARIANTS = { stars, network, dots };
    const ORDER = ["network", "stars", "dots"];

    function tick(now) {
        ctx.clearRect(0, 0, W, H);
        if (current) current.draw(now || 0);
        raf = requestAnimationFrame(tick);
    }

    function start(name) {
        if (!VARIANTS[name]) name = "stars";
        cancelAnimationFrame(raf);
        current = VARIANTS[name];
        currentName = name;
        current.seed(W, H);
        raf = requestAnimationFrame(tick);
    }

    window.addEventListener("resize", () => {
        resizeCanvas();
        if (current) current.seed(W, H);
    });

    refreshColor();
    resizeCanvas();

    window.BG = {
        variants: ORDER.slice(),
        current: () => currentName,
        set(name) {
            localStorage.setItem("rn_bg", name);
            start(name);
        },
        cycle() {
            const i = ORDER.indexOf(currentName);
            const next = ORDER[(i + 1) % ORDER.length];
            this.set(next);
            return next;
        },
        refreshColor
    };

    start(localStorage.getItem("rn_bg") || "network");
})();
