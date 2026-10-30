#!/usr/bin/env python3
"""Make the Sifferjakt audio library with Gemini TTS (run on the Mac).

Default: Gemini Batch API. It has its own limits (not the small daily cap for
live TTS requests), costs half, and runs on Google's side, so the Mac can sleep.
  caffeinate -i /usr/bin/python3 make_library.py          # batch (default)
  /usr/bin/python3 make_library.py --live                 # one request at a time

Reads phrases.json next to this script, writes audio/<id>.m4a, audio/index.json,
progress.log and sifferjakt-audio.zip. Safe to stop and re-run: finished clips are
skipped and a running batch is picked up again (batch_state.json).
Key: read from the Obsidian Aloud plugin settings in the vault (never printed).
Standard library only; macOS afconvert makes small AAC files (falls back to WAV).
"""
import base64, json, pathlib, shutil, subprocess, sys, time, urllib.error, urllib.request, wave, zipfile

HERE = pathlib.Path(__file__).resolve().parent
VAULT = next((p for p in HERE.parents if (p / ".obsidian").is_dir()), None)
KEY = json.loads((VAULT / ".obsidian/plugins/aloud-tts/data.json").read_text())["gemini_apiKey"]
CFG = json.loads((HERE / "phrases.json").read_text())
OUT = HERE / "audio"; OUT.mkdir(exist_ok=True)
STATE = HERE / "batch_state.json"
LOG = HERE / "progress.log"
AFCONVERT = shutil.which("afconvert")
EXT = "m4a" if AFCONVERT else "wav"
API = "https://generativelanguage.googleapis.com/v1beta"
STRICT = "Läs upp exakt texten mellan citattecknen på svenska, lugnt och tydligt, och lägg inte till något annat: "
CHUNK = 100          # requests per batch job (stays well under the TTS batch token queue)
POLL = 30            # seconds between status checks

def log(line):
    print(line, flush=True)
    with open(LOG, "a") as f: f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")

class ApiError(Exception):
    def __init__(self, code, body): super().__init__(f"HTTP {code}"); self.code, self.body = code, body

def call(method, path, body=None, timeout=120):
    req = urllib.request.Request(API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "x-goog-api-key": KEY})
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, e.read().decode(errors="ignore"))

def request_for(text, strict=False):
    prompt = (STRICT + "”" + text + "”") if strict else (CFG["style"] + text)
    return {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": CFG["voice"]}}}}}

def audio_of(resp):
    """(pcm bytes, rate) from a GenerateContentResponse, or None."""
    for c in resp.get("candidates", []):
        for p in c.get("content", {}).get("parts", []):
            d = p.get("inlineData") or p.get("inline_data")
            if d and d.get("data"):
                mime = d.get("mimeType") or d.get("mime_type") or ""
                rate = int(mime.split("rate=")[1].split(";")[0]) if "rate=" in mime else 24000
                return base64.b64decode(d["data"]), rate
    return None

def save(pid, pcm, rate):
    wav = OUT / f"{pid}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    if AFCONVERT:
        subprocess.run([AFCONVERT, "-f", "m4af", "-d", "aac", "-b", "40000", str(wav), str(OUT / f"{pid}.m4a")], check=True)
        wav.unlink()

def write_index():
    valid = {p["id"] for p in CFG["phrases"]}
    done = sorted(f.stem for f in OUT.glob(f"*.{EXT}") if f.stem in valid)
    (OUT / "index.json").write_text(json.dumps({"voice": CFG["voice"], "model": CFG["model"], "ext": EXT, "ids": done}))
    return done

def todo():
    return [p for p in CFG["phrases"] if not (OUT / f"{p['id']}.{EXT}").exists()]

def daily_limit(body):
    """True if a 429 is the per-day quota (Google puts PerDay in quotaId)."""
    return "PerDay" in body or "per day" in body.lower()

def retry_delay(body, default=65):
    try:
        for d in json.loads(body)["error"].get("details", []):
            if "retryDelay" in d: return int(float(d["retryDelay"].rstrip("s"))) + 2
    except Exception: pass
    return default

# ---------------- batch mode ----------------
def find_inlined(obj):
    """Walk the batch result and return the list of inlined responses, wherever Google nests it."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("inlinedResponses", "inlined_responses"):
                if isinstance(v, list): return v
                r = find_inlined(v)
                if r is not None: return r
            else:
                r = find_inlined(v)
                if r is not None: return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_inlined(v)
            if r is not None: return r
    return None

def state_of(job):
    return (job.get("metadata", {}).get("state") or job.get("state") or "").replace("BATCH_STATE_", "").replace("JOB_STATE_", "")

TRIED = set()   # phrases already sent once in this run get the strict prompt next time
def submit(chunk):
    body = {"batch": {"display_name": f"sifferjakt-{int(time.time())}",
                      "input_config": {"requests": {"requests": [
                          {"request": request_for(p["text"], strict=p["id"] in TRIED), "metadata": {"key": p["id"]}} for p in chunk]}}}}
    TRIED.update(p["id"] for p in chunk)
    job = call("POST", f"/models/{CFG['model']}:batchGenerateContent", body, timeout=300)
    name = job.get("name")
    if not name: raise RuntimeError("Inget batch-namn i svaret: " + json.dumps(job)[:300])
    STATE.write_text(json.dumps({"name": name, "ids": [p["id"] for p in chunk]}))
    log(f"Batch skickad: {name} ({len(chunk)} fraser).")
    return name

def wait_and_collect(name, ids):
    last = None
    while True:
        job = call("GET", f"/{name}")
        st = state_of(job)
        if st != last: log(f"  Batch {name.split('/')[-1]}: {st or 'okänt'}"); last = st
        if st in ("SUCCEEDED",) or job.get("done") and st not in ("FAILED", "CANCELLED", "EXPIRED"):
            break
        if st in ("FAILED", "CANCELLED", "EXPIRED"):
            log("  Batchen misslyckades: " + json.dumps(job.get("error") or job)[:400]); STATE.unlink(missing_ok=True); return 0
        time.sleep(POLL)
    items = find_inlined(job) or []
    saved = 0
    for i, item in enumerate(items):
        key = (item.get("metadata") or {}).get("key") or (ids[i] if i < len(ids) else None)
        a = audio_of(item.get("response") or {})
        if key and a:
            save(key, *a); saved += 1
    STATE.unlink(missing_ok=True)
    write_index()
    log(f"  Sparade {saved} av {len(ids)} klipp.")
    if not items: log("  Hittade inga svar i batchen. Början av svaret: " + json.dumps(job)[:600])
    return saved

def run_batch():
    if STATE.exists():
        st = json.loads(STATE.read_text())
        log(f"Fortsätter med pågående batch {st['name']}.")
        wait_and_collect(st["name"], st["ids"])
    while True:
        left = todo()
        if not left: return None
        chunk = left[:CHUNK]
        try:
            name = submit(chunk)
        except ApiError as e:
            if e.code == 402: return "Gemini-krediterna är slut (402). Fyll på i Google AI Studio och kör igen."
            if e.code == 429: return "Google tar inte emot fler batcher just nu (429). Kör igen om en stund."
            return f"Batchen kunde inte skickas (HTTP {e.code}): {e.body[:300]}"
        if wait_and_collect(name, [p["id"] for p in chunk]) == 0:
            return "Batchen gav inga klipp. Se progress.log, eller prova --live."

# ---------------- live mode ----------------
def run_live():
    left = todo(); log(f"Live: {len(left)} kvar.")
    for n, p in enumerate(left, 1):
        for attempt in range(4):
            try:
                a = audio_of(call("POST", f"/models/{CFG['model']}:generateContent", request_for(p["text"], strict=attempt > 0)))
                if not a: raise ValueError("Gemini svarade utan ljud")
                save(p["id"], *a); write_index(); log(f"[{n}/{len(left)}] {p['text'][:70]}")
                break
            except ApiError as e:
                if e.code == 429 and daily_limit(e.body):
                    return "Dagens gräns för röstanrop är nådd. Gränsen nollställs vid midnatt i Kalifornien (kl. 08–09 svensk tid), eller använd batch-läget (utan --live)."
                if e.code == 429 and attempt < 3:
                    s = retry_delay(e.body); log(f"  Minutgräns, väntar {s} s …"); time.sleep(s); continue
                if e.code == 429: return "Google svarar bara 429. Troligen dagens gräns; använd batch-läget."
                if e.code == 402: return "Gemini-krediterna är slut (402). Fyll på i Google AI Studio och kör igen."
                if e.code in (500, 503) and attempt < 3: time.sleep(10); continue
                log(f"  hoppar över (HTTP {e.code}): {p['text'][:60]}"); break
            except Exception as e:
                if attempt < 3: time.sleep(5); continue
                log(f"  hoppar över ({e}): {p['text'][:60]}"); break
        time.sleep(7)

log(f"Start ({'live' if '--live' in sys.argv else 'batch'}): {len(CFG['phrases'])} fraser, {len(todo())} kvar. Röst {CFG['voice']}.")
stopped = run_live() if "--live" in sys.argv else run_batch()
done = write_index()
with zipfile.ZipFile(HERE / "sifferjakt-audio.zip", "w", zipfile.ZIP_STORED) as z:
    for f in OUT.iterdir():
        if (f.suffix == f".{EXT}" and f.stem in done) or f.name == "index.json": z.write(f, f"audio/{f.name}")
log(stopped or "Klart!")
log(f"{len(done)} av {len(CFG['phrases'])} fraser finns. Säg till Claude att pusha.")
