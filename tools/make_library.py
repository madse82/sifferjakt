#!/usr/bin/env python3
"""Make the Sifferjakt audio library with Gemini TTS (run on the Mac).

Reads phrases.json next to this script, writes audio/<id>.m4a and audio/index.json,
and packs everything into sifferjakt-audio.zip for Claude to push to GitHub.
- Key: read from the Obsidian Aloud plugin settings in the vault (never printed).
- Safe to stop and re-run: finished clips are skipped.
- Paces requests and waits on Google's rate limits; stops cleanly on a daily limit or empty credits.
Standard library only; uses macOS afconvert for small AAC files (falls back to WAV).
"""
import base64, json, pathlib, shutil, subprocess, sys, time, urllib.error, urllib.request, wave, zipfile

HERE = pathlib.Path(__file__).resolve().parent
VAULT = next((p for p in HERE.parents if (p / ".obsidian").is_dir()), None)
KEY = json.loads((VAULT / ".obsidian/plugins/aloud-tts/data.json").read_text())["gemini_apiKey"]
CFG = json.loads((HERE / "phrases.json").read_text())
OUT = HERE / "audio"; OUT.mkdir(exist_ok=True)
AFCONVERT = shutil.which("afconvert")
EXT = "m4a" if AFCONVERT else "wav"
PAUSE = 7  # seconds between requests (about 8 per minute)

def tts(text):
    body = {"contents": [{"parts": [{"text": CFG["style"] + text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": CFG["voice"]}}}}}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{CFG['model']}:generateContent",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": KEY})
    r = json.load(urllib.request.urlopen(req, timeout=120))
    part = next(p for p in r["candidates"][0]["content"]["parts"] if "inlineData" in p)
    mime = part["inlineData"].get("mimeType", "")
    rate = int(mime.split("rate=")[1].split(";")[0]) if "rate=" in mime else 24000
    return base64.b64decode(part["inlineData"]["data"]), rate

def save(pid, pcm, rate):
    wav = OUT / f"{pid}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    if AFCONVERT:
        subprocess.run([AFCONVERT, "-f", "m4af", "-d", "aac", "-b", "40000", str(wav), str(OUT / f"{pid}.m4a")], check=True)
        wav.unlink()

phrases = CFG["phrases"]
todo = [p for p in phrases if not (OUT / f"{p['id']}.{EXT}").exists()]
print(f"{len(phrases)} fraser, {len(phrases)-len(todo)} klara, {len(todo)} kvar. Röst {CFG['voice']}.")
stopped = None
for n, p in enumerate(todo, 1):
    for attempt in range(4):
        try:
            pcm, rate = tts(p["text"]); save(p["id"], pcm, rate)
            print(f"[{n}/{len(todo)}] {p['text'][:70]}")
            break
        except urllib.error.HTTPError as e:
            msg = e.read()[:300].decode(errors="ignore")
            if e.code == 429 and ("per day" in msg.lower() or "perday" in msg.lower()):
                stopped = "Dagens gräns hos Google är nådd. Kör skriptet igen i morgon, det fortsätter där det slutade."; break
            if e.code == 429:
                print("  Googles minutgräns, väntar 65 s …"); time.sleep(65); continue
            if e.code == 402:
                stopped = "Gemini-krediterna är slut (402). Fyll på i Google AI Studio och kör igen."; break
            if e.code in (500, 503) and attempt < 3:
                time.sleep(10); continue
            print(f"  hoppar över (HTTP {e.code}): {p['text'][:60]}"); break
        except Exception as e:
            if attempt < 3: time.sleep(10); continue
            print(f"  hoppar över ({e}): {p['text'][:60]}"); break
    if stopped: break
    time.sleep(PAUSE)

done = sorted(f.stem for f in OUT.glob(f"*.{EXT}"))
(OUT / "index.json").write_text(json.dumps({"voice": CFG["voice"], "model": CFG["model"], "ext": EXT, "ids": done}))
with zipfile.ZipFile(HERE / "sifferjakt-audio.zip", "w", zipfile.ZIP_STORED) as z:
    for f in OUT.iterdir():
        if f.suffix in (f".{EXT}", ".json"): z.write(f, f"audio/{f.name}")
print(stopped or "Klart!")
print(f"{len(done)} av {len(phrases)} fraser finns. Säg till Claude att pusha sifferjakt-audio.zip.")
