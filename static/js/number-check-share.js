(function () {
  // «مشاركة كصورة» on the number-check card: draws the customer-facing result on a
  // canvas (1080 px wide, always light) and shares it as a PNG (WhatsApp via the Web
  // Share API), or downloads it. The image is built ONLY from the card's
  // `#rd-ncheck-share-data` JSON (no price, ICCID or sale) and the card's
  // data-site-logo / data-site-name. The layout is pure (layoutShare) so node can test it.
  "use strict";

  var WIDTH = 1080;
  var MARGIN = 80;
  var RIGHT = WIDTH - MARGIN; // RTL start edge
  var FONT = "'IBM Plex Sans Arabic', system-ui, sans-serif";
  var FONT_WEIGHTS = [400, 600, 700];

  var LABEL_BUSY = "جارٍ التجهيز…";
  var MSG_SAVED = "تم حفظ الصورة";
  var MSG_FAILED = "تعذّر تجهيز الصورة";
  var TITLE = "نتيجة فحص الرقم";
  var FOOTER_TAIL = "فحص فوري للخطوط";
  var DEFAULT_SITE_NAME = "Pro Sim";
  // Arabic letters + digits, so the Arabic unicode-range subset is requested too.
  var FONT_SAMPLE = "مفعل 0123";
  var LOGO_IMG = ".rd-ncheck__logo img";

  // Colour tokens read from a light-themed probe, so the image never follows the page theme.
  var TOKEN_VARS = {
    ink: "--rd-ink",
    muted: "--rd-muted",
    surface: "--rd-surface",
    surface2: "--rd-surface-2",
    border: "--bs-border-color",
    success: "--rd-success",
    successSoft: "--rd-success-soft",
    warning: "--rd-warning",
    warningSoft: "--rd-warning-soft",
    danger: "--rd-danger",
    dangerSoft: "--rd-danger-soft",
    accent: "--rd-accent",
  };
  // Only used for a token the stylesheet does not define (light-theme values).
  var TOKEN_FALLBACK = {
    ink: "#0a0a0a",
    muted: "#6b6b68",
    surface: "#ffffff",
    surface2: "#f4f4f2",
    border: "#ececea",
    success: "#0a7a3e",
    successSoft: "#e8f6ee",
    warning: "#c2410c",
    warningSoft: "#fff0e9",
    danger: "#b91c1c",
    dangerSoft: "#fdecec",
    accent: "#ff5a1f",
  };

  // ---- pure helpers -------------------------------------------------------

  function groupPhone(raw) {
    return String(raw == null ? "" : raw).trim();
  }

  function phoneDigits(display) {
    return String(display || "").replace(/\D/g, "");
  }

  // "6.08", "/", "400": drawn one piece at a time, left to right, so the bidi
  // algorithm can never reorder a number run.
  function usedTotalParts(used, total) {
    var parts = [String(used)];
    if (total) parts.push("/", String(total));
    return parts;
  }

  // Greedy word wrap in logical order; `measure(text)` -> width.
  function wrapText(text, maxWidth, measure, maxLines) {
    var words = String(text || "").split(/\s+/).filter(Boolean);
    var lines = [];
    var line = "";
    words.forEach(function (word) {
      var next = line ? line + " " + word : word;
      if (line && measure(next) > maxWidth) {
        lines.push(line);
        line = word;
      } else {
        line = next;
      }
    });
    if (line) lines.push(line);
    if (maxLines && lines.length > maxLines) {
      var kept = lines.slice(0, maxLines);
      kept[maxLines - 1] = ellipsize(lines.slice(maxLines - 1).join(" "), maxWidth, measure);
      return kept;
    }
    return lines;
  }

  function ellipsize(text, maxWidth, measure) {
    var s = String(text || "");
    if (measure(s) <= maxWidth) return s;
    while (s.length > 1 && measure(s + "…") > maxWidth) s = s.slice(0, -1);
    return s.replace(/\s+$/, "") + "…";
  }

  // Largest size <= `size` at which `text` fits `maxWidth`.
  function fitSize(size, textWidthAtSize, maxWidth) {
    if (textWidthAtSize <= maxWidth || textWidthAtSize <= 0) return size;
    return Math.max(12, Math.floor((size * maxWidth) / textWidthAtSize));
  }

  // Same-origin URLs only: a cross-origin image would taint the canvas.
  function isSameOrigin(src, base) {
    if (!src) return false;
    try {
      var u = new URL(src, base.href);
      return u.origin === base.origin;
    } catch (e) {
      return false;
    }
  }

  function toneColors(tone, c) {
    if (tone === "ok") return { fg: c.success, bg: c.successSoft };
    if (tone === "warn") return { fg: c.warning, bg: c.warningSoft };
    if (tone === "danger") return { fg: c.danger, bg: c.dangerSoft };
    return { fg: c.muted, bg: c.surface2 };
  }

  function fontString(weight, size) {
    return weight + " " + size + "px " + FONT;
  }

  // Reads every token from a data-bs-theme="light" probe element.
  function readTokens(doc, getStyle) {
    var colors = {};
    var probe = doc.createElement("div");
    probe.setAttribute("data-bs-theme", "light");
    probe.hidden = true;
    doc.body.appendChild(probe);
    try {
      var style = getStyle(probe);
      Object.keys(TOKEN_VARS).forEach(function (key) {
        var raw = String(style.getPropertyValue(TOKEN_VARS[key]) || "").trim();
        if (!raw) {
          colors[key] = TOKEN_FALLBACK[key];
          return;
        }
        // Resolve var()/rgba to a plain colour the canvas understands.
        probe.style.color = "";
        probe.style.color = raw;
        colors[key] = getStyle(probe).color || raw;
      });
    } finally {
      doc.body.removeChild(probe);
    }
    return colors;
  }

  /**
   * Pure layout: returns { width, height, ops }.
   * data: the card's share JSON. opts: { siteName, hasSiteLogo, hasLineLogo }.
   * measure(text, weight, size) -> width in px.
   */
  function layoutShare(data, measure, c, opts) {
    opts = opts || {};
    var ops = [];
    var rect = function (x, y, w, h, r, fill, stroke) {
      ops.push({ t: "rect", x: x, y: y, w: w, h: h, r: r, fill: fill, stroke: stroke || null });
    };
    var text = function (s, x, y, weight, size, fill, align) {
      ops.push({ t: "text", s: s, x: x, y: y, weight: weight, size: size, fill: fill, dir: "rtl", align: align || "right" });
    };
    // LTR text: always drawn from an explicit left x, never trusting canvas bidi.
    var ltr = function (s, x, y, weight, size, fill) {
      ops.push({ t: "text", s: s, x: x, y: y, weight: weight, size: size, fill: fill, dir: "ltr", align: "left" });
    };
    var ltrRight = function (s, rightX, y, weight, size, fill) {
      ltr(s, rightX - measure(s, weight, size), y, weight, size, fill);
    };
    var m = function (s, weight, size) {
      return measure(s, weight, size);
    };

    var siteName = opts.siteName || DEFAULT_SITE_NAME;
    var lineName = String(data.line_name || data.network || "");
    var status = String(data.status || "") || "—";
    var tone = toneColors(data.status_tone, c);

    rect(RIGHT - 150, 48, 150, 150, 28, opts.hasLineLogo ? c.surface : c.surface2, c.border);
    if (opts.hasLineLogo) {
      ops.push({ t: "image", key: "line", x: RIGHT - 135, y: 63, w: 120, h: 120 });
    } else {
      text(lineName.trim().charAt(0) || "—", RIGHT - 75, 148, 700, 72, c.ink, "center");
    }
    var phone = data.phone_display || groupPhone(data.phone) || "—";
    ltrRight(phone, RIGHT - 180, 118, 700, 64, c.ink);
    var pillW = m(status, 700, 32) + 70;
    rect(RIGHT - 180 - pillW, 140, pillW, 56, 28, tone.bg);
    ops.push({ t: "circle", x: RIGHT - 180 - 28, y: 168, r: 8, fill: tone.fg });
    text(status, RIGHT - 220, 176, 700, 32, tone.fg);
    if (opts.hasSiteLogo) {
      ops.push({ t: "image", key: "site", x: MARGIN, y: 70, w: 280, h: 90 });
    } else {
      text(siteName, MARGIN, 130, 700, 36, c.ink, "left");
    }

    var y = 230;
    (data.facts || []).forEach(function (row) {
      var label = String(row.label || "");
      var value = String(row.value || "—");
      rect(MARGIN, y, WIDTH - 2 * MARGIN, 64, 12, c.surface, c.border);
      rect(RIGHT - 280, y, 280, 64, 12, c.surface2);
      text(label, RIGHT - 24, y + 42, 500, 28, c.muted);
      if (row.ltr) ltrRight(value, RIGHT - 304, y + 42, 600, 30, c.ink);
      else text(value, RIGHT - 304, y + 42, 600, 30, c.ink);
      y += 76;
    });

    var rings = (data.usage || []).filter(function (g) {
      return !(String(g.used) === "—" && !g.total && !g.unlimited);
    });
    y += 24;
    if (!rings.length || data.has_usage === false) {
      text(data.notice || "لا تتوفر بيانات استهلاك لهذا الرقم", RIGHT, y + 36, 400, 30, c.muted);
      y += 70;
    } else {
      var col = (WIDTH - 2 * MARGIN) / 3;
      rings.forEach(function (g, i) {
        var cx = RIGHT - col * i - col / 2;
        ops.push({ t: "circle", x: cx, y: y + 54, r: 46, fill: c.surface2 });
        ltr(String(g.used), cx - m(String(g.used), 700, 28) / 2, y + 64, 700, 28, c.ink);
        text(g.label, cx, y + 130, 600, 26, c.ink, "center");
        var caption = g.included === false ? "غير مشمولة" : g.unlimited ? "غير محدود" : "";
        if (!caption && g.total) caption = String(g.used) + " / " + String(g.total);
        if (caption) text(caption, cx, y + 164, 500, 24, c.muted, "center");
      });
      y += 190;
    }

    return { width: WIDTH, height: y + 48, ops: ops };
  }

  // ---- drawing ------------------------------------------------------------

  function roundRectPath(ctx, x, y, w, h, r) {
    var radius = Math.min(r || 0, w / 2, h / 2);
    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(x, y, w, h, radius);
      return;
    }
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
  }

  function paint(ctx, layout, colors, images) {
    ctx.fillStyle = colors.surface;
    ctx.fillRect(0, 0, layout.width, layout.height);
    layout.ops.forEach(function (op) {
      if (op.t === "rect") {
        roundRectPath(ctx, op.x, op.y, op.w, op.h, op.r);
        ctx.fillStyle = op.fill;
        ctx.fill();
        if (op.stroke) {
          ctx.strokeStyle = op.stroke;
          ctx.lineWidth = 2;
          ctx.stroke();
        }
      } else if (op.t === "circle") {
        ctx.beginPath();
        ctx.arc(op.x, op.y, op.r, 0, Math.PI * 2);
        ctx.fillStyle = op.fill;
        ctx.fill();
      } else if (op.t === "text") {
        ctx.font = fontString(op.weight, op.size);
        ctx.direction = op.dir;
        ctx.textAlign = op.align;
        ctx.fillStyle = op.fill;
        ctx.fillText(op.s, op.x, op.y);
      } else if (op.t === "image") {
        var img = images[op.key];
        if (!img) return;
        var k = Math.min(op.w / img.width, op.h / img.height);
        var iw = img.width * k;
        var ih = img.height * k;
        ctx.drawImage(img, op.x + (op.w - iw) / 2, op.y + (op.h - ih) / 2, iw, ih);
      }
    });
  }

  // ---- browser flow -------------------------------------------------------

  function loadImage(src) {
    return new Promise(function (resolve) {
      if (!isSameOrigin(src, window.location)) {
        resolve(null);
        return;
      }
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { resolve(null); };
      img.src = src;
    });
  }

  function loadFonts() {
    var fonts = document.fonts;
    if (!fonts || !fonts.load) return Promise.resolve();
    return Promise.all(
      FONT_WEIGHTS.map(function (w) {
        return fonts.load(w + " 40px 'IBM Plex Sans Arabic'", FONT_SAMPLE).catch(function () {});
      })
    );
  }

  function readData(card) {
    var node = card.querySelector('script[type="application/json"]');
    return JSON.parse(node ? node.textContent : "{}");
  }

  async function renderBlob(card) {
    var data = readData(card);
    var lineLogo = data.line_logo || "";
    var siteLogo = card.getAttribute("data-site-logo") || "";
    var siteName = (card.getAttribute("data-site-name") || "").trim() || DEFAULT_SITE_NAME;
    var loaded = await Promise.all([loadImage(siteLogo), loadImage(lineLogo), loadFonts()]);
    var images = { site: loaded[0], line: loaded[1] };
    var colors = readTokens(document, window.getComputedStyle.bind(window));

    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d");
    var measure = function (s, weight, size) {
      ctx.font = fontString(weight, size);
      return ctx.measureText(s).width;
    };
    var layout = layoutShare(data, measure, colors, {
      siteName: siteName,
      hasSiteLogo: !!images.site,
      hasLineLogo: !!images.line,
    });
    canvas.width = layout.width;
    canvas.height = layout.height;
    paint(ctx, layout, colors, images);
    var blob = await new Promise(function (resolve) {
      canvas.toBlob(resolve, "image/png");
    });
    if (!blob) throw new Error("toBlob failed");
    return { blob: blob, data: data };
  }

  function toast(message, kind) {
    if (window.rdToast) window.rdToast(message, kind);
  }

  function download(file) {
    var url = URL.createObjectURL(file);
    var a = document.createElement("a");
    a.href = url;
    a.download = file.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  async function shareCard(btn) {
    var card = btn.closest("[data-rd-ncheck]");
    if (!card || btn.disabled) return;
    var label = btn.querySelector("[data-rd-share-label]");
    var original = label ? label.textContent : "";
    btn.disabled = true;
    if (label) label.textContent = LABEL_BUSY;
    try {
      var out = await renderBlob(card);
      var digits = phoneDigits(out.data.phone_display);
      var file = new File([out.blob], "number-check" + (digits ? "-" + digits : "") + ".png", { type: "image/png" });
      var nav = window.navigator;
      if (nav && nav.canShare && nav.canShare({ files: [file] })) {
        try {
          await nav.share({ files: [file], title: TITLE, text: (TITLE + " " + digits).trim() });
          return;
        } catch (err) {
          if (err && err.name === "AbortError") return; // the user closed the sheet
          console.warn("navigator.share failed, downloading instead", err);
        }
      }
      download(file);
      toast(MSG_SAVED, "success");
    } catch (err) {
      console.error("number-check share failed", err);
      toast(MSG_FAILED, "error");
    } finally {
      btn.disabled = false;
      if (label) label.textContent = original;
    }
  }

  // The monogram sits under the logo <img> in the tile; a logo file that fails to load is
  // hidden so the monogram shows instead of a broken image.
  function hideBrokenLogos(root) {
    if (!root || !root.querySelectorAll) return;
    Array.prototype.forEach.call(root.querySelectorAll(LOGO_IMG), function (img) {
      if (img.complete && img.naturalWidth === 0) img.hidden = true;
    });
  }

  // `error` does not bubble: listen in the capture phase on document.
  document.addEventListener(
    "error",
    function (event) {
      var img = event.target;
      if (!img || img.tagName !== "IMG" || !img.closest) return;
      if (img.closest(".rd-ncheck__logo")) img.hidden = true;
    },
    true
  );

  // Cards injected by the polling flow (or already failed when the page is shown).
  hideBrokenLogos(document);
  if (window.htmx && window.htmx.onLoad) {
    window.htmx.onLoad(function (elt) { hideBrokenLogos(elt); });
  }

  // Delegated on document: works for every card injected into the result box and is
  // removed with the page by page-scope.js.
  document.addEventListener("click", function (event) {
    var target = event.target;
    var btn = target && target.closest ? target.closest("[data-rd-share-check]") : null;
    if (!btn) return;
    if (event.preventDefault) event.preventDefault();
    shareCard(btn);
  });

  window.rdNcheckShare = {
    groupPhone: groupPhone,
    phoneDigits: phoneDigits,
    usedTotalParts: usedTotalParts,
    wrapText: wrapText,
    ellipsize: ellipsize,
    fitSize: fitSize,
    isSameOrigin: isSameOrigin,
    toneColors: toneColors,
    readTokens: readTokens,
    layoutShare: layoutShare,
    hideBrokenLogos: hideBrokenLogos,
    TOKEN_VARS: TOKEN_VARS,
    TOKEN_FALLBACK: TOKEN_FALLBACK,
  };
})();
