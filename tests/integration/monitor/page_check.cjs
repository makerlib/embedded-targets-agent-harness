// Headless checks of tools/monitor/index.html for HK-015 AC-007.
// Usage: node page_check.cjs '<json config>'  ->  prints one JSON object.
// Config: { url, file?, toEnd?, waitLive? }
"use strict";
const { chromium } = require("playwright");

(async () => {
  const cfg = JSON.parse(process.argv[2]);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const requests = [];
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  // Remote fonts are optional (spec §9.22 req 4); answer them locally with an empty
  // stylesheet so results do not depend on the network.
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (route) =>
    route.fulfill({ status: 200, contentType: "text/css", body: "" }));
  page.on("request", (r) => requests.push({ method: r.method(), url: r.url() }));

  await page.goto(cfg.url);
  if (cfg.waitLive) {
    await page.waitForFunction(() => document.querySelector("#mode-text").textContent === "Live", null, { timeout: 15000 });
    await page.waitForFunction((n) => document.querySelectorAll("#log li[data-seq]").length >= n, cfg.waitLive, { timeout: 15000 });
  } else {
    await page.waitForSelector("#log li");
  }
  if (cfg.file) {
    await page.setInputFiles("#file", cfg.file);
    await page.waitForFunction(() => document.querySelector("#mode-text").textContent === "File replay");
  }
  if (cfg.toEnd) {
    await page.evaluate(() => {
      const s = document.querySelector("#scrub");
      s.value = s.max;
      s.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  await page.waitForTimeout(150);

  const result = await page.evaluate(() => {
    const q = (s) => document.querySelector(s);
    const text = (s) => (q(s) ? q(s).textContent.trim() : null);
    const tiles = [...document.querySelectorAll(".tile")].map((t) => ({
      k: t.querySelector(".k") ? t.querySelector(".k").textContent : "",
      v: t.querySelector(".v") ? t.querySelector(".v").textContent : "",
      s: t.querySelector(".s") ? t.querySelector(".s").textContent : "",
    }));
    const controls = [...document.querySelectorAll("button, input, select, a, form, label")].map((el) =>
      ({ tag: el.tagName.toLowerCase(), id: el.id || null, text: el.textContent.trim().slice(0, 40), type: el.type || null, filter: el.dataset ? el.dataset.f || null : null }));
    return {
      mode: text("#mode-text"),
      verdictValue: text("#verdict-value"),
      verdictAuthority: text("#verdict-authority"),
      verdictTile: tiles.find((t) => t.k === "Verdict") || null,
      coverage: text("#coverage-note"),
      gapWarnHidden: q("#gap-warn").hidden,
      gapWarn: text("#gap-warn"),
      unregistered: document.querySelectorAll("[data-unregistered]").length,
      logSeqs: [...document.querySelectorAll("#log li[data-seq]")].map((li) => Number(li.dataset.seq)),
      logText: text("#log"),
      forms: document.querySelectorAll("form").length,
      controls,
      title: text("#title"),
    };
  });
  result.requests = requests;
  result.errors = errors;
  await browser.close();
  process.stdout.write(JSON.stringify(result));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
