# Ljudbibliotek

1. `node tools/build_phrases.js` bygger `phrases.json` ur appens egen logik (körs av Claude när texterna ändras).
2. `make_library.py` körs på Macen bredvid `phrases.json` (i valvet: `03 - Outputs/App Ideas/Sifferjakt/audio-library/`). Det gör `audio/*.m4a`, `audio/index.json` och `sifferjakt-audio.zip`.
3. Innehållet i zip-filen hamnar i `audio/` i det här repot. Appen spelar fraser därifrån först, sedan sparat Gemini-ljud, sedan Gemini direkt, sist enhetens röst.

Nyckeln (`phraseId`) är en FNV-1a-hash av exakt den text som skickas till Gemini, så ändras en text behöver dess klipp göras om.
