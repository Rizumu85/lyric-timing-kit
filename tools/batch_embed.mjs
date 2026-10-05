// Offline batch writer. Changes only default USLT and the workflow's Ruby slot.
import fs from 'node:fs/promises';
import crypto from 'node:crypto';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { ROOT, PYTHON } from './kit-config.mjs';
import { parseId3, embedDualLyrics, MAX_TAG_BYTES, RUBY_DESCRIPTOR } from './format/id3.mjs';
import { toLrc,pairBilingual } from './format/lyrics.mjs';
import { toRubyLrc } from './format/ruby-lrc.mjs';
import { parseFaKara } from './format/fa-kara.mjs';

const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const sync=size=>[size>>21&127,size>>14&127,size>>7&127,size&127];
function embedPlain(original, plain, language) {
  const parsed=parseId3(original);
  if (![3,4].includes(parsed.version)||parsed.flags!==0) throw new Error('Unsupported ID3 layout; original left untouched');
  const encode=text=>parsed.version===4?Buffer.from(text,'utf8'):Buffer.concat([Buffer.from([255,254]),Buffer.from(text,'utf16le')]);
  const payload=Buffer.concat([Buffer.from([parsed.version===4?3:1]),Buffer.from(language,'ascii'),encode(''),Buffer.alloc(parsed.version===4?1:2),encode(plain)]);
  const size=Buffer.alloc(4);if(parsed.version===4)size.set(sync(payload.length));else size.writeUInt32BE(payload.length);
  const frame=Buffer.concat([Buffer.from('USLT'),size,Buffer.alloc(2),payload]);
  const retained=parsed.frames.filter(f=>!f.lyric||(f.lyric.descriptor!==''&&f.lyric.descriptor!==RUBY_DESCRIPTOR));
  const frames=[frame,...retained.map(f=>Buffer.from(f.raw))], length=frames.reduce((n,f)=>n+f.length,0);
  const padding=Math.max(1024,parsed.bodySize-length);
  if(length+padding+10>MAX_TAG_BYTES)throw new Error('Tag too large');
  return Buffer.concat([Buffer.from([73,68,51,parsed.version,parsed.revision||0,0,...sync(length+padding)]),...frames,Buffer.alloc(padding),original.subarray(parsed.totalSize)]);
}

export async function embedSong(audioPath,fiaPath,backupDir,language,leadMs=50,sidecarInfo=null) {
  const doc=JSON.parse(await fs.readFile(fiaPath,'utf8')),original=await fs.readFile(audioPath);
  // Only MP3 is written here; every other format has its own kind of tag, which embed_tags.py writes.
  const otherFormat=!/\.mp3$/i.test(audioPath),before=otherFormat?null:parseId3(original);
  const metadata=doc.song||{},plain=toLrc(doc.lyrics.lines,metadata,leadMs);
  // Untimed opening punctuation belongs before the ruby base, not inside it.
  for(const line of doc.lyrics.lines)for(const word of line.words||[]){
    if(!word.syllables?.some(s=>s.ruby?.length))continue;
    const text=word.syllables.map(s=>s.text).join(''),prefix=word.text.indexOf(text);
    if(prefix>0)word.syllables.unshift({text:word.text.slice(0,prefix),startTime:word.startTime,endTime:word.startTime});
  }
  const hasRuby=doc.lyrics.lines.some(l=>l.words?.some(w=>w.ruby?.length||w.syllables?.some(s=>s.ruby?.length)));
  const ruby=hasRuby?toRubyLrc(doc.lyrics.lines,metadata,leadMs):null;
  if(ruby){
    const parsed=pairBilingual(parseFaKara(ruby).lines);
    const count=lines=>lines.flatMap(l=>l.words||[]).flatMap(w=>w.syllables||[]).filter(s=>s.ruby?.length).length;
    if(parsed.length!==doc.lyrics.lines.length||count(parsed)!==count(doc.lyrics.lines))throw new Error('Ruby roundtrip lost lyric lines or readings; original left untouched');
    const textShape=lines=>lines.map(l=>[l.fullText,l.translation&&l.translation!==l.fullText?l.translation:'']);
    const rubyShape=lines=>lines.map(l=>(l.words||[]).flatMap(w=>w.syllables||[]).filter(s=>s.ruby?.length).map(s=>[s.text,s.ruby.map(r=>r.text).join('')]));
    if(JSON.stringify(textShape(parsed))!==JSON.stringify(textShape(doc.lyrics.lines))||JSON.stringify(rubyShape(parsed))!==JSON.stringify(rubyShape(doc.lyrics.lines)))throw new Error('Ruby roundtrip changed lyric text, translation or reading; original left untouched');
  }
  await fs.writeFile(path.join(path.dirname(fiaPath),'aligned.bilingual.lrc'),plain,'utf8');
  if(ruby)await fs.writeFile(path.join(path.dirname(fiaPath),'aligned.ruby.lrc'),ruby,'utf8');
  if(otherFormat){
    if(sidecarInfo?.path)throw new Error('Sidecar lyrics are only handled for MP3');
    const folder=path.dirname(fiaPath),python=PYTHON;
    const output=execFileSync(python,[path.join(ROOT,'embed_tags.py'),audioPath,path.join(folder,'aligned.bilingual.lrc'),ruby?path.join(folder,'aligned.ruby.lrc'):'-',backupDir,language],{encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}});
    return {...JSON.parse(output),sourceFia:fiaPath,leadMs,lineCount:doc.lyrics.lines.length,ruby:hasRuby,sidecar:null};
  }
  const updated=hasRuby?Buffer.from(embedDualLyrics(original,{plain,ruby})):embedPlain(original,plain,language);
  const after=parseId3(updated),audioHash=hash(original.subarray(before.totalSize));
  const kept=p=>p.frames.filter(f=>!f.lyric).map(f=>[f.id,hash(f.raw)]);
  if(audioHash!==hash(updated.subarray(after.totalSize))||JSON.stringify(kept(before))!==JSON.stringify(kept(after)))throw new Error('Audio or non-lyric metadata changed');
  if(after.lyrics[0].text!==plain||(ruby&&after.lyrics[1].text!==ruby))throw new Error('Lyric roundtrip failed');
  await fs.mkdir(backupDir,{recursive:true});
  let beforeSidecar=null,sidecar=null;
  if(sidecarInfo?.path){
    beforeSidecar=await fs.readFile(sidecarInfo.path);
    if(hash(beforeSidecar)!==sidecarInfo.sha256)throw new Error('Sidecar changed since inventory; original music left untouched');
    const sidecarBackup=path.join(backupDir,'original-sidecar.lrc');
    try{await fs.writeFile(sidecarBackup,beforeSidecar,{flag:'wx'});}catch(e){if(e.code!=='EEXIST')throw e;if(hash(await fs.readFile(sidecarBackup))!==hash(beforeSidecar))throw new Error('Sidecar backup mismatch');}
    sidecar={path:sidecarInfo.path,backup:sidecarBackup};
  }
  const backupPath=path.join(backupDir,hash(original)+'.original.mp3');
  try{await fs.writeFile(backupPath,original,{flag:'wx'});}catch(e){if(e.code!=='EEXIST')throw e;if(hash(await fs.readFile(backupPath))!==hash(original))throw new Error('Invalid backup');}
  if(hash(await fs.readFile(audioPath))!==hash(original))throw new Error('Original changed before commit');
  const temporary=audioPath+'.'+crypto.randomBytes(8).toString('hex')+'.tmp';
  try{await fs.writeFile(temporary,updated,{flag:'wx'});await fs.rename(temporary,audioPath);}finally{await fs.rm(temporary,{force:true});}
  if(hash(await fs.readFile(audioPath))!==hash(updated))throw new Error('Written checksum mismatch');
  if(sidecarInfo?.path){
    const stage=sidecarInfo.path+'.'+crypto.randomBytes(8).toString('hex')+'.tmp';
    try{await fs.writeFile(stage,plain,{flag:'wx'});await fs.rename(stage,sidecarInfo.path);}catch(error){
      if(hash(await fs.readFile(audioPath))===hash(updated)){
        const restore=audioPath+'.'+crypto.randomBytes(8).toString('hex')+'.tmp';
        try{await fs.writeFile(restore,original,{flag:'wx'});await fs.rename(restore,audioPath);}finally{await fs.rm(restore,{force:true});}
      }
      throw error;
    }finally{await fs.rm(stage,{force:true});}
  }
  return {audioPath,backupPath,sourceFia:fiaPath,leadMs,sourceSha256:hash(original),outputSha256:hash(updated),audioTailSha256:audioHash,audioBytesUnchanged:true,nonLyricFramesUnchanged:true,lineCount:doc.lyrics.lines.length,ruby:hasRuby,sidecar,versions:after.lyrics.map(l=>({language:l.language,descriptor:l.descriptor,characters:l.text.length}))};
}

if(process.argv[1]===new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1').replaceAll('/','\\')) {
  const [audio,fia,backup,lang='jpn',lead='50',sidecar,sidecarHash]=process.argv.slice(2);
  console.log(JSON.stringify(await embedSong(audio,fia,backup,lang,Number(lead),sidecar?{path:sidecar,sha256:sidecarHash}:null)));
}
