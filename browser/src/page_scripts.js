"use strict";

// Functions that run INSIDE the web page (page.evaluate). Each one must be fully self-contained:
// it is turned into text, sent to the page and run there, so it cannot use anything from this file.
// They only read the page, or mark an element with data-assistant-n so Node can click or type into it.
// Nothing here follows instructions found in the page; the page is treated purely as data.

/** Facts about the page used to spot sign-in and payment pages. */
function pageStateInPage() {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none";
  };
  const isCard = (el) =>
    /cc-(number|csc|exp)/.test(el.getAttribute("autocomplete") || "") ||
    /(card.?number|cardnumber|cc.?num|cvv|cvc|security.?code)/i.test(
      [el.name, el.id, el.placeholder, el.getAttribute("aria-label")].join(" ")
    );
  const inputs = Array.from(document.querySelectorAll("input"));
  const heading = document.querySelector("h1");
  return {
    url: location.href,
    title: document.title || "",
    readyState: document.readyState,
    passwordInputs: inputs.filter((el) => el.type === "password" && visible(el)).length,
    cardInputs: inputs.filter((el) => el.type !== "hidden" && isCard(el) && visible(el)).length,
    heading: heading ? (heading.innerText || "").trim().slice(0, 120) : "",
  };
}

/**
 * Finds buttons, links and similar things whose visible name matches `wanted`, best first.
 * Order of attempts: real clickable elements (links, buttons, roles); if none match, any visible text.
 */
function findClickableInPage(wanted, limit) {
  const norm = (s) =>
    String(s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const want = norm(wanted);
  document.querySelectorAll("[data-assistant-n]").forEach((el) => el.removeAttribute("data-assistant-n"));
  if (!want) return { candidates: [], total: 0 };

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    if (s.visibility === "hidden" || s.display === "none" || s.opacity === "0") return false;
    return !el.closest("[aria-hidden='true'], [hidden], [inert]");
  };
  const labelOf = (el) =>
    (el.getAttribute("aria-label") || el.innerText || el.value || el.title || el.alt ||
      (el.querySelector("img[alt]") || {}).alt || "").trim();
  const scoreOf = (label) => {
    const l = norm(label);
    if (!l || l.length > 200) return 0;
    if (l === want) return 1;
    if (l.startsWith(want + " ")) return 0.9;
    if ((" " + l + " ").includes(" " + want + " ")) return 0.8;
    if (l.includes(want)) return 0.6;
    if (l.length >= 3 && want.includes(l)) return 0.55;
    return 0;
  };
  const kindOf = (el) => {
    const role = el.getAttribute("role");
    if (el.tagName === "A" || role === "link") return "link";
    if (el.tagName === "INPUT") return "button";
    if (el.tagName === "BUTTON" || role === "button") return "button";
    if (role === "tab") return "tab";
    if (role === "menuitem") return "menu item";
    return "item";
  };
  const regionOf = (el) => {
    const region = el.closest("nav, header, footer, main, aside, form, dialog, [role='dialog']");
    return region ? region.tagName.toLowerCase().replace("dialog", "dialog") : "";
  };
  const formKindOf = (el) => {
    const form = el.closest("form");
    if (!form) return "";
    if (Array.from(form.querySelectorAll("input[type='password']")).some(visible)) return "login";
    const card = Array.from(form.querySelectorAll("input")).some(
      (i) =>
        /cc-(number|csc|exp)/.test(i.getAttribute("autocomplete") || "") ||
        /(card.?number|cardnumber|cc.?num|cvv|cvc)/i.test([i.name, i.id, i.placeholder].join(" "))
    );
    return card ? "payment" : "";
  };

  const clickable =
    "a[href], button, [role='button'], [role='link'], [role='menuitem'], [role='tab'], " +
    "input[type='submit'], input[type='button'], input[type='image'], summary, [onclick]";
  let found = [];
  const seen = new Set();
  const add = (el, label, score, fallback) => {
    if (seen.has(el) || el.disabled || el.getAttribute("aria-disabled") === "true" || !visible(el)) return;
    seen.add(el);
    found.push({ el, label, score, fallback });
  };

  for (const el of document.querySelectorAll(clickable)) {
    const label = labelOf(el);
    const score = scoreOf(label);
    if (score > 0) add(el, label, score, false);
  }
  // an inner link wins over a wrapper that merely contains the same text
  found = found.filter((a) => !found.some((b) => b !== a && a.el.contains(b.el) && norm(b.label) === norm(a.label)));

  if (!found.length) {
    const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_ELEMENT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const own = Array.from(node.childNodes)
        .filter((c) => c.nodeType === Node.TEXT_NODE)
        .map((c) => c.textContent)
        .join(" ")
        .trim();
      const score = scoreOf(own);
      if (score >= 0.6) add(node.closest(clickable) || node, own, score, true);
    }
  }

  found.sort((a, b) => b.score - a.score);
  const top = found.slice(0, Math.max(1, limit || 5));
  const candidates = top.map((f, i) => {
    f.el.setAttribute("data-assistant-n", String(i));
    return {
      n: i,
      label: f.label.replace(/\s+/g, " ").slice(0, 120),
      score: f.score,
      kind: kindOf(f.el),
      context: regionOf(f.el),
      formKind: formKindOf(f.el),
      fallback: f.fallback,
    };
  });
  return { candidates, total: found.length };
}

/** Finds text boxes by their label, placeholder, aria-label, name or nearby label text. */
function findFieldInPage(wanted, limit) {
  const norm = (s) =>
    String(s || "")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const want = norm(wanted);
  document.querySelectorAll("[data-assistant-n]").forEach((el) => el.removeAttribute("data-assistant-n"));
  if (!want) return { candidates: [], total: 0 };

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && !el.closest("[aria-hidden='true'], [hidden], [inert]");
  };
  const scoreOf = (text) => {
    const l = norm(text);
    if (!l || l.length > 200) return 0;
    if (l === want) return 1;
    if (l.startsWith(want + " ")) return 0.9;
    if ((" " + l + " ").includes(" " + want + " ")) return 0.8;
    if (l.includes(want)) return 0.6;
    return 0;
  };
  const textOfIds = (ids) =>
    (ids || "").split(/\s+/).map((id) => (document.getElementById(id) || {}).innerText || "").join(" ").trim();
  const skip = ["hidden", "submit", "button", "checkbox", "radio", "image", "file", "reset", "range", "color"];

  const fields = Array.from(
    document.querySelectorAll("input, textarea, [contenteditable=''], [contenteditable='true']")
  ).filter((el) => {
    if (el.tagName === "INPUT" && skip.includes((el.type || "text").toLowerCase())) return false;
    return !el.disabled && !el.readOnly && visible(el);
  });

  const out = [];
  for (const el of fields) {
    const labels = Array.from(el.labels || []).map((l) => (l.innerText || "").trim());
    const names = [
      ...labels,
      el.getAttribute("aria-label"),
      textOfIds(el.getAttribute("aria-labelledby")),
      el.placeholder,
      el.title,
      el.name,
      el.id,
    ].filter(Boolean);
    const score = Math.max(0, ...names.map(scoreOf));
    if (score <= 0) continue;
    const type = (el.type || (el.isContentEditable ? "contenteditable" : "text")).toLowerCase();
    const auto = (el.getAttribute("autocomplete") || "").toLowerCase();
    const hint = [el.name, el.id, el.placeholder, el.getAttribute("aria-label")].join(" ");
    let kind = "text";
    if (type === "password") kind = "password";
    else if (/cc-|card/.test(auto) || /(card.?number|cardnumber|cc.?num|cvv|cvc|security.?code)/i.test(hint)) kind = "payment";
    else if (auto === "one-time-code" || /\b(otp|one.?time|verification.?code|2fa|pin)\b/i.test(hint)) kind = "password";
    out.push({
      el,
      score,
      label: (names[0] || "").replace(/\s+/g, " ").slice(0, 120),
      type,
      kind,
    });
  }
  out.sort((a, b) => b.score - a.score);
  const top = out.slice(0, Math.max(1, limit || 5));
  const candidates = top.map((f, i) => {
    f.el.setAttribute("data-assistant-n", String(i));
    return { n: i, label: f.label, score: f.score, type: f.type, kind: f.kind };
  });
  return { candidates, total: out.length };
}

function scrollInPage(direction) {
  const el = document.scrollingElement || document.documentElement;
  const before = Math.round(el.scrollTop);
  const step = Math.round(window.innerHeight * 0.8);
  if (direction === "down") el.scrollBy({ top: step, behavior: "instant" });
  else if (direction === "up") el.scrollBy({ top: -step, behavior: "instant" });
  else if (direction === "top") el.scrollTo({ top: 0, behavior: "instant" });
  else if (direction === "bottom") el.scrollTo({ top: el.scrollHeight, behavior: "instant" });
  const y = Math.round(el.scrollTop);
  const max = el.scrollHeight - window.innerHeight;
  return { y, moved: y !== before, atTop: y <= 0, atBottom: y >= max - 2 };
}

/** The first real video result on a YouTube page (not an ad, a Short or a channel). */
function firstVideoLinkInPage() {
  const ads = "ytd-ad-slot-renderer, ytd-promoted-sparkles-web-renderer, ytd-display-ad-renderer, ytd-in-feed-ad-layout-renderer";
  for (const a of document.querySelectorAll("a[href^='/watch'], a[href*='youtube.com/watch']")) {
    if (a.closest(ads)) continue;
    const r = a.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const title = (a.getAttribute("title") || a.getAttribute("aria-label") || a.innerText || "").trim();
    if (!title) continue;
    return { href: a.href, title: title.replace(/\s+/g, " ").slice(0, 150) };
  }
  return null;
}

function playVideoInPage() {
  const video = document.querySelector("video");
  if (!video) return { found: false, playing: false };
  return Promise.resolve(video.play())
    .then(() => ({ found: true, playing: !video.paused }))
    .catch(() => ({ found: true, playing: !video.paused }));
}

module.exports = {
  pageStateInPage,
  findClickableInPage,
  findFieldInPage,
  scrollInPage,
  firstVideoLinkInPage,
  playVideoInPage,
};
