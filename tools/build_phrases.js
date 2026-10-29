// Builds tools/phrases.json from the app's own logic (run: node tools/build_phrases.js, needs playwright).
// Every entry is exactly the text the app sends to Gemini, so the clips match what the app asks for.
const {chromium} = require('playwright');
const path = require('path'), fs = require('fs');
(async()=>{
  const b = await chromium.launch(); const p = await b.newPage();
  await p.goto('file://' + path.resolve(__dirname, '../index.html'));
  const out = await p.evaluate(()=>{
    const list = new Map();
    const add = (parts, kind)=>{
      parts = (Array.isArray(parts) ? parts : [parts]).map(sayable).filter(Boolean);
      if(!parts.length) return;
      const t = joinParts(parts); if(!list.has(t)) list.set(t, kind);
    };
    fixedPhrases().forEach(t=>add(t, "fixed"));
    add(helpParts(), "help");
    add(["Hej! Så här låter jag.", "Summan är svaret när man plussar.", "8 + 5 = 13."], "test");
    const base = ["green","yellow","keys"];
    add(["Vad hjälpte dig mest?", ...base.map(k=>REF_LABELS[k])], "reflect");
    add(["Vad hjälpte dig mest?", ...[...base,"known"].map(k=>REF_LABELS[k])], "reflect");
    const terms = Object.keys(TERMS);
    for(const term of terms){
      const T = TERMS[term], rest = terms.filter(k=>k!==term);
      for(let i=0;i<rest.length;i++) for(let j=i+1;j<rest.length;j++){
        add([T.q, ...[term, rest[i], rest[j]].sort((x,y)=>x.localeCompare(y,"sv"))], "quiz");
      }
      add(["Ja!", T.def], "quiz-answer"); add([`Det heter ${term}.`, T.def], "quiz-answer");
    }
    for(const l of [0,1]) for(const s of pool(l)){
      add(`Svaret var ${s}. Försök igen!`, "answer");
      termOptions(parse(s)).forEach(([term, sentence])=>add([term, sentence, TERMS[term].def], "word-card"));
    }
    const syms = ["0","1","2","3","4","5","6","7","8","9","+","−","="];
    for(const a of syms){
      add(`Nästan! ${a} finns, men på fel plats.`, "hint");
      for(const c of syms) if(c!==a) add(`Nästan! ${a} och ${c} finns, men på fel plats.`, "hint");
    }
    return {voice:"Zephyr", model:"gemini-2.5-flash-preview-tts", style:GEM_STYLE,
            phrases:[...list].map(([text,kind])=>({id:phraseId(text), kind, text}))};
  });
  // most useful first, so a partial library (daily limits) already covers the common cases
  const order = ["fixed","help","test","reflect","quiz","quiz-answer","word-card","answer","hint"];
  out.phrases.sort((x,y)=>order.indexOf(x.kind)-order.indexOf(y.kind));
  const ids = new Set(out.phrases.map(x=>x.id));
  if(ids.size !== out.phrases.length) throw new Error("hash collision");
  fs.writeFileSync(path.resolve(__dirname,'phrases.json'), JSON.stringify(out, null, 1));
  const byKind = {}; out.phrases.forEach(x=>byKind[x.kind]=(byKind[x.kind]||0)+1);
  console.log(out.phrases.length, "phrases", byKind);
  await b.close();
})();
