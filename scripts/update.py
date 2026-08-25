#!/usr/bin/env python3
"""HydroClean landing auto-update.

Dijalankan harian oleh cron Hermes (07:30 WIB).
- Scrape ulasan Google Maps (HydroClean Indonesia) via browser-use CLI
- Scrape tab Reposts @hydroclean.id (link reel + thumbnail + likes)
- Tulis data/google.json + data/reels.json, download thumbnail baru
- Commit & push ke GitHub (Pages) bila ada perubahan

Output stdout hanya saat ada perubahan / error (pola watchdog no_agent).
"""
import json, os, re, subprocess, sys, tempfile, time

PROJ = os.path.expanduser("~/projects/hydroclean-landing")
DATA = os.path.join(PROJ, "data")
ASSETS = os.path.join(PROJ, "assets")
PLACE_URL = ("https://www.google.com/maps/place/HydroClean+Indonesia/"
             "@-6.2369958,106.8236181,17z/data=!4m8!3m7!1s0x2e69f18a40c82757:"
             "0x7bd9f7da7f98b90d!8m2!3d-6.2369958!4d106.8236181!9m1!1b1!16s%2Fg%2F11f008s5yl?hl=id")

BROWSER_CODE = r'''
# Auto-update data testimoni HydroClean
import time, json, os
out = {}

# ---------- GOOGLE ----------
goto_url("''' + PLACE_URL + r'''")
wait_for_load()
time.sleep(7)
try:
    cdp('Emulation.setDeviceMetricsOverride', width=1400, height=900, deviceScaleFactor=1, mobile=False)
except Exception:
    pass
time.sleep(3)
n = js('document.querySelectorAll("div.jftiEf").length')
if n == 0:
    goto_url("''' + PLACE_URL + r'''")
    wait_for_load(); time.sleep(7)
prev = 0
for i in range(30):
    js("""(() => {
      const feed = [...document.querySelectorAll('div.m6QErb')].find(d => d.querySelectorAll('div.jftiEf').length > 0 && d.scrollHeight > d.clientHeight);
      if (feed) { feed.scrollTop = feed.scrollHeight; feed.dispatchEvent(new Event('scroll',{bubbles:true})); }
      return 1; })()""")
    time.sleep(2)
    n = js('document.querySelectorAll("div.jftiEf").length')
    if n >= 45 or (n == prev and i > 12): break
    prev = n
js("""(() => { document.querySelectorAll('button.w8nwRe').forEach(b=>b.click()); return 1; })()""")
time.sleep(3)
raw = js("""(() => {
  const out = [];
  document.querySelectorAll('div.jftiEf').forEach(el => {
    out.push({
      name: el.querySelector('.d4r55')?.textContent?.trim(),
      sub: el.querySelector('.RfnDt')?.textContent?.trim(),
      stars: el.querySelector('.kvMYJc')?.getAttribute('aria-label'),
      when: el.querySelector('.rsqaWe')?.textContent?.trim(),
      text: el.querySelector('.wiI7pd')?.textContent?.trim()});
  });
  return JSON.stringify(out); })()""")
out['google'] = json.loads(raw)
# rating & jumlah total
head = js("""(() => {
  const r = document.querySelector('div.fontDisplayLarge')?.textContent?.trim();
  const t = [...document.querySelectorAll('div,span')].map(e=>e.textContent&&e.textContent.trim()).find(x=>x&&/^[\d.,]+ ulasan$/.test(x));
  return JSON.stringify({rating:r, total:t}); })()""")
out['google_head'] = json.loads(head)

# ---------- INSTAGRAM REPOSTS ----------
goto_url("https://www.instagram.com/hydroclean.id/reposts/")
wait_for_load()
time.sleep(7)
items = {}
prev = 0
for i in range(12):
    raw = js("""(() => {
      const out = [];
      document.querySelectorAll('main a[href*="/reel/"], main a[href*="/p/"]').forEach(a => {
        const img = a.querySelector('img');
        out.push({href: a.getAttribute('href'), src: img ? img.src : null});
      });
      return JSON.stringify(out); })()""")
    for it in json.loads(raw):
        if it['href'] not in items or (it['src'] and not items[it['href']].get('src')):
            items[it['href']] = it
    js('window.scrollTo(0, document.body.scrollHeight)')
    time.sleep(2.5)
    if len(items) == prev and i > 5: break
    prev = len(items)
out['reels'] = list(items.values())

# ---------- profil stats ----------
goto_url("https://www.instagram.com/hydroclean.id/")
wait_for_load(); time.sleep(6)
stats = js("""(() => {
  const m = document.querySelector('meta[property="og:description"], meta[name="description"]');
  return m ? m.getAttribute('content') : null; })()""")
out['ig_meta'] = stats

ws = os.environ.get('BH_AGENT_WORKSPACE','.')
with open(os.path.join(ws, 'update_dump.json'), 'w') as f:
    json.dump(out, f, ensure_ascii=False)
print('DUMP_OK ' + os.path.join(ws, 'update_dump.json'))
'''


def run_browser(code: str) -> str:
    r = subprocess.run(["browser-use"], input=code, capture_output=True, text=True, timeout=900)
    m = re.search(r"DUMP_OK (\S+)", r.stdout)
    if not m:
        raise RuntimeError("browser scrape failed:\n" + r.stdout[-2000:] + "\n" + r.stderr[-2000:])
    return m.group(1)


def fetch_reel_meta(href: str) -> str:
    """Ambil og:description satu reel via browser-use (untuk likes)."""
    code = f'''
# Ambil meta reel
import time
goto_url("https://www.instagram.com{href}")
time.sleep(5)
m = js("""(() => {{ const m = document.querySelector('meta[property=\\"og:description\\"], meta[name=\\"description\\"]'); return m ? m.getAttribute('content') : 'NONE'; }})()""")
print("META::" + (m or "NONE"))
'''
    r = subprocess.run(["browser-use"], input=code, capture_output=True, text=True, timeout=180)
    m = re.search(r"META::(.*)", r.stdout, re.S)
    return m.group(1).strip() if m else ""


def main():
    dump_path = run_browser(BROWSER_CODE)
    dump = json.load(open(dump_path))

    changes = []

    # ---------- GOOGLE ----------
    old_g = json.load(open(os.path.join(DATA, "google.json"))) if os.path.exists(os.path.join(DATA, "google.json")) else {}
    seen, reviews = set(), []
    for r in dump["google"]:
        if r.get("stars") != "5 bintang" or not r.get("text"):
            continue
        key = (r["name"], (r["text"] or "")[:40])
        if key in seen:
            continue
        seen.add(key)
        reviews.append({
            "name": r["name"], "sub": r.get("sub") or "",
            "when": r.get("when") or "",
            "text": re.sub(r"\s+", " ", r["text"]).strip(), "stars": 5,
        })
    def _age(w):
        w = (w or "").lower()
        m = re.search(r"(\d+)", w)
        n = int(m.group(1)) if m else 1
        if "tahun" in w: return n * 365
        if "bulan" in w: return n * 30
        if "minggu" in w: return n * 7
        if "hari" in w: return n
        if "jam" in w or "menit" in w: return 0
        return 999
    reviews.sort(key=lambda r: _age(r.get("when")))
    reviews = reviews[:30]
    head = dump.get("google_head") or {}
    rating = (head.get("rating") or "4,9").replace(".", ",")
    total = re.sub(r"\s*ulasan", "", head["total"]) if head.get("total") else ""
    new_g = {"rating": rating, "total": total or old_g.get("total", ""), "reviews": reviews}
    if new_g != old_g and reviews:
        old_names = {(r["name"], r["text"][:40]) for r in old_g.get("reviews", [])}
        fresh = [r for r in reviews if (r["name"], r["text"][:40]) not in old_names]
        json.dump(new_g, open(os.path.join(DATA, "google.json"), "w"), ensure_ascii=False, indent=1)
        if fresh:
            changes.append(f"Google: +{len(fresh)} review baru ({', '.join(r['name'] for r in fresh[:5])})")
        elif len(reviews) != len(old_g.get("reviews", [])):
            changes.append(f"Google: jumlah review berubah ({len(old_g.get('reviews', []))} -> {len(reviews)})")

    # ---------- REELS ----------
    old_r = json.load(open(os.path.join(DATA, "reels.json"))) if os.path.exists(os.path.join(DATA, "reels.json")) else {"reels": []}
    old_by_href = {r.get("href", "").replace("https://www.instagram.com", ""): r for r in old_r.get("reels", [])}
    reels = []
    new_handles = []
    for it in dump["reels"]:
        href = it["href"]
        handle = href.split("/")[1]
        thumb_rel = f"assets/reel_{handle}.jpg"
        thumb_abs = os.path.join(PROJ, thumb_rel)
        prev_entry = old_by_href.get(href)
        # thumbnail: download bila baru
        if not os.path.exists(thumb_abs) and it.get("src"):
            subprocess.run(["curl", "-sL", "-A", "Mozilla/5.0", "-o", thumb_abs, it["src"]], timeout=60)
        # likes: pakai lama bila ada, fetch bila reel baru
        likes = prev_entry.get("likes") if prev_entry else None
        comments = prev_entry.get("comments") if prev_entry else None
        caption = prev_entry.get("caption", "") if prev_entry else ""
        if prev_entry is None:
            new_handles.append(handle)
            meta = fetch_reel_meta(href)
            mm = re.match(r"([\d,.]+[KM]?)\s+likes,\s+([\d,.]+)\s+comments", meta or "")
            if mm:
                likes, comments = mm.group(1), mm.group(2)
            if meta and ': "' in meta:
                caption = meta.split(': "', 1)[1][:200]
        reels.append({"handle": handle, "href": "https://www.instagram.com" + href,
                      "thumb": thumb_rel, "likes": likes, "comments": comments, "caption": caption})

    # urutan dipertahankan sesuai tab Reposts (terbaru lebih dulu)
    new_reels = {"followers": old_r.get("followers", "241 rb"), "posts": old_r.get("posts", "2.056"), "reels": reels}
    ig_meta = dump.get("ig_meta") or ""
    mm = re.search(r"([\d,.]+[KMrb\s]*?)\s*Followers", ig_meta, re.I)
    if mm:
        new_reels["followers"] = mm.group(1).strip().replace("K", " rb").replace(",", ".")
    if new_reels != old_r and reels:
        json.dump(new_reels, open(os.path.join(DATA, "reels.json"), "w"), ensure_ascii=False, indent=1)
        if new_handles:
            changes.append(f"Instagram: +{len(new_handles)} reel baru (@{', @'.join(new_handles)})")

    # ---------- COMMIT & PUSH ----------
    if changes:
        subprocess.run(["git", "add", "-A"], cwd=PROJ, check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=PROJ)
        if diff.returncode != 0:
            msg = "auto-update: " + "; ".join(changes)
            subprocess.run(["git", "commit", "-m", msg], cwd=PROJ, check=True)
            subprocess.run(["git", "push"], cwd=PROJ, check=True)
            print("✅ HydroClean landing diperbarui — " + "; ".join(changes))
    # tanpa perubahan: diam (watchdog pattern)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"⚠️ HydroClean auto-update gagal: {e}", file=sys.stderr)
        sys.exit(1)
