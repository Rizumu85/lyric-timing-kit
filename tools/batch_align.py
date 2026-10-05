"""Resumable GPU batch alignment; validated drafts are embedded with exact backups."""
import argparse
import gc
import json
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import numpy as np
import torch
import torchaudio
from huggingface_hub import snapshot_download
from janome.tokenizer import Tokenizer
import pykakasi
import uroman
import romkan
from pypinyin import lazy_pinyin
from opencc import OpenCC
from demucs.api import Separator
from demucs.hf import load_safetensors_model
from demucs.apply import BagOfModels

ROOT=Path(__file__).resolve().parent
from kit_config import OUT, BACKUPS, FA_KARA
sys.path.insert(0,str(FA_KARA))
import haruraw2norm as hn
import align_yohane
from export_song import build_line

def write_json(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)

def hira(text): return ''.join(chr(ord(c)-96) if 'ァ'<=c<='ヶ' else c for c in text)

class CachedSeparator(Separator):
    def _load_model(self):
        cached=Path(snapshot_download('adefossez/HTDemucs',local_files_only=True))
        self._model=BagOfModels([load_safetensors_model(cached/'955717e8.safetensors')])
        self._audio_channels=self._model.audio_channels
        self._samplerate=self._model.samplerate

class Pipeline:
    def __init__(self):
        torch.set_num_threads(4)
        self.tokenizer=Tokenizer();self.kks=pykakasi.kakasi();self.roman=uroman.Uroman()
        self.s2t=OpenCC('s2t');self.t2jp=OpenCC('t2jp')
        self.separator=CachedSeparator(device='cuda',shifts=0,progress=False)
        self.japanese=align_yohane.Wav2Vec2ForcedAligner(snapshot_download('NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn',local_files_only=True))
        self.multilingual=torchaudio.pipelines.MMS_FA.get_model().to('cuda').eval()
        self.dictionary=torchaudio.pipelines.MMS_FA.get_dict()
        self.mms_tokenizer=torchaudio.pipelines.MMS_FA.get_tokenizer()
        # How far before a line's known start its search window may begin. Loose lyric-sheet
        # times need slack; starts that are already accurate should use a small value.
        self.window_margin=0.6
        # How far past the next line's start the window may run, so a line's last syllables are not squeezed out.
        self.window_tail=None

    def annotate(self,text):
        result=[]
        def plain(value):
          for chunk in re.split(r'(\s+)',value):
            if not chunk:continue
            if chunk.isspace():result.append(chunk);continue
            for token in self.tokenizer.tokenize(chunk):
                surface=token.surface
                if not re.search(r'[\u3400-\u9fff々]',surface):result.append(surface);continue
                reading=hira(token.reading)
                if reading=='*':reading=''.join(t['hira'] for t in self.kks.convert(surface))
                if not reading or re.search('[\u3400-\u9fff]',reading):
                    normalized=self.t2jp.convert(self.s2t.convert(surface)).translate(str.maketrans({'靑':'青','髙':'高','﨑':'崎'}))
                    if normalized!=surface:
                        reading=''.join(hira(t.reading) if t.reading!='*' else ''.join(k['hira'] for k in self.kks.convert(t.surface)) for t in self.tokenizer.tokenize(normalized))
                if not reading or re.search('[\u3400-\u9fff]',reading):raise ValueError(f'Cannot determine reading: {surface}')
                suffix=re.search('[ぁ-ゖ]+$',surface)
                if suffix and reading.endswith(suffix[0]) and len(surface)>len(suffix[0]):
                    result.append('{'+surface[:-len(suffix[0])]+'|'+reading[:-len(suffix[0])]+'}'+suffix[0])
                else:result.append('{'+surface+'|'+reading+'}')
        # The dictionary splits these into counter readings (に+にん); in lyrics they are ひとり / ふたり.
        text=re.sub(r'(?<![㐀-鿿々0-9０-９])(一人|二人)(?![㐀-鿿々(（])',lambda m:m[1]+('(ひとり)' if m[1]=='一人' else '(ふたり)'),text)
        position=0
        # ○ and × may carry a reading too (maru / batsu buttons)
        # ｜ opens a base that is not plain kanji, as in Aozora ruby: ｜夜の手(ナイトハンド)
        for match in re.finditer(r'(?:｜([^｜(（)）]+)|([\u3400-\u9fff々○×]+))[(（]([a-zA-Zぁ-ヿ]+)[)）]',text):
            plain(text[position:match.start()])
            # romaji spells ん as m before b/m/p (rembo), and honnou is ほんのう, not ほんおう:
            # write both the way the converter expects
            reading=romkan.to_hiragana(re.sub('nn(?=[aiueoy])',"n'n",re.sub('m(?=[bmp])','n',match[3].lower()))) if re.fullmatch('[a-zA-Z]+',match[3]) else match[3]
            result.append('{'+(match[1] or match[2])+'|'+reading+'}')
            position=match.end()
        plain(text[position:])
        return ''.join(result)

    def prepare(self,source):
        items=[];rows=[]
        for row in source['rows']:
            text=row['original']
            if row.get('unsung') and row['sourceStart'] is not None:
                # Shown but not sung (on-screen captions): keep the lyric sheet's time, align nothing.
                rows.append({**row,'annotated':text,'nonVocal':True})
                items.append({'orig':'\n','type':0,'pron':''})
                continue
            if row['sourceStart'] is not None and re.fullmatch(r'[\s・ー－—.\-]+',text) and re.search('[・.]',text) and re.search('[ー－—-]',text):
                # Morse code written into the lyrics is sung as ト (dot) and ツー (dash).
                rows.append({**row,'annotated':text})
                items.extend({'orig':c,'type':3 if not c.isspace() else 0,'pron':'' if c.isspace() else ('to' if c in '・.' else 'tsu')} for c in text)
                items.append({'orig':'\n','type':0,'pron':''})
                continue
            if source['language']=='ja':
                if re.search(r'[{}\[\]]',text):raise ValueError('歌词含注音格式保留字符，需人工确认')
                annotated=self.annotate(text)
                units=hn.process_haruhi_line(annotated,'jaen')
                for unit in units:
                    unit['pron']=re.sub('[^a-z\']','',unit.get('pron','').lower())
            else:
                annotated=text;units=[]
                parts=re.findall(r'[\u3400-\u9fff]|[\u3041-\u30ff]+|[A-Za-zÀ-žА-Яа-яЁё가-힣]+(?:[’\'][A-Za-z]+)*|\d+(?:\.\d+)?|[^\w]|_',text)
                if ''.join(parts)!=text:raise ValueError('歌词含未支持的字符，请补充读音或核对文字')
                for part in parts:
                    kana=bool(re.fullmatch('[\u3041-\u30ff]+',part))
                    sung=kana or bool(re.search(r'[\u3400-\u9fffA-Za-zÀ-žА-Яа-яЁё가-힣0-9]',part))
                    roman=self.roman.romanize_string(part,'jpn' if kana else {'zh':'cmn','ru':'rus','ko':'kor','en':'eng'}.get(source['language'])) if sung else ''
                    if re.search('[\u3400-\u9fff]',part) and roman.isdigit():roman=''.join(lazy_pinyin(part))
                    if part.isdigit():roman=hn.number_to_english(part)
                    pron=re.sub('[^a-z\']','',roman.lower())
                    units.append({'orig':part,'type':3 if pron else 0,'pron':pron})
            if not any(u.get('pron') for u in units) and re.search('[가-힣]',text):
                # a Korean line inside a Japanese song: romanize it as one sung unit
                units=[{'orig':text,'type':3,'pron':re.sub("[^a-z']",'',self.roman.romanize_string(text,'kor').lower())}]
            if not any(u.get('pron') for u in units):raise ValueError(f'没有可对齐读音：{text}')
            canonical=re.sub(r'\{([^|]+)\|[^}]+\}',r'\1',annotated)
            rows.append({**row,'original':canonical,'annotated':annotated})
            items.extend(units);items.append({'orig':'\n','type':0,'pron':''})
        return items,rows

    def emissions(self,waveform,language):
        sr=16000;stride=320;core=24*sr;context=sr
        total=int(self.japanese.model._get_feat_extract_output_lengths(len(waveform)))
        output=[]
        with torch.inference_mode():
            for begin in range(0,len(waveform),core):
                left=max(0,begin-context);right=min(len(waveform),begin+core+context)
                if language=='ja':
                    inputs=self.japanese.processor(audio=waveform[left:right].numpy(),sampling_rate=sr,return_tensors='pt')
                    logits=self.japanese.model(**inputs.to('cuda')).logits
                else:logits,_=self.multilingual(waveform[None,left:right].to('cuda'))
                first=(begin-left)//stride;count=min(core//stride,total-begin//stride)
                if count>0:output.append(logits[:,first:first+count].float().log_softmax(-1).cpu())
        emission=torch.cat(output,dim=1)[0]
        if len(emission)!=total:raise ValueError('Acoustic frame mismatch')
        return emission

    def process(self,source,embed=True,constrained_lines=False):
        folder=OUT/source['key'];start=time.monotonic()
        items,rows=self.prepare(source)
        write_json(folder/'prepared.json',{'rows':rows,'items':items})
        cache=folder/('ja-emission.pt' if source['language']=='ja' else 'mms-emission.pt')
        if cache.exists():emission=torch.load(cache,weights_only=True,map_location='cpu')
        else:
            print(f"SEPARATE {source['name']}",flush=True)
            _,stems=self.separator.separate_audio_file(Path(source['audio']))
            vocals=torchaudio.functional.resample(stems['vocals'].mean(0),self.separator.samplerate,16000)
            del stems;gc.collect()
            print(f"ALIGN {source['name']}",flush=True)
            emission=self.emissions(vocals,source['language']);del vocals
            torch.save(emission,cache)
        voiced=[u for u in items if u.get('pron')]
        pronunciations=[u['pron'] for u in voiced]
        tokenize=self.japanese.tokenize if source['language']=='ja' else self.mms_tokenizer
        blank=self.japanese.blank if source['language']=='ja' else 0
        if any(r['sourceStart'] is None for r in rows) and any(r['sourceStart'] is not None for r in rows):
            raise ValueError('带时间歌词中还有未打轴正文；需确认，未写入音乐文件')
        spans=align_yohane._align_token_spans(emission,tokenize(pronunciations),blank=blank)
        if constrained_lines and all(r['sourceStart'] is not None for r in rows):
            # Repeated hooks and refrains are ambiguous to a whole-song CTC pass.
            # Keep the existing sentence timestamps as search windows, then align
            # words inside each window so a repeated line cannot jump to an earlier
            # occurrence.  A small margin tolerates ordinary hand-written LRC drift.
            row_units=[];group=[]
            for item in items:
                if item['orig']=='\n':row_units.append([u for u in group if u.get('pron')]);group=[]
                else:group.append(item)
            if len(row_units)!=len(rows):raise ValueError('Lyric row grouping changed')
            constrained=[];local_details=[]
            # Share of each 20 ms frame that is not CTC blank, smoothed over 140 ms.
            activity=np.convolve(1-emission[:,blank].exp().numpy(),np.ones(7)/7,mode='same')
            for n,(row,units) in enumerate(zip(rows,row_units)):
                if not units:
                    continue
                onset=float(row['sourceStart'])
                next_onset=float(rows[n+1]['sourceStart']) if n+1<len(rows) else float(source['duration'])
                if next_onset<=onset:
                    constrained.append(spans[sum(len(x) for x in row_units[:n]):sum(len(x) for x in row_units[:n+1])])
                    local_details.append({'line':n+1,'applied':False,'reason':'非递增原时间'})
                    continue
                previous_onset=float(rows[n-1]['sourceStart']) if n else 0.0
                # Do not let a short-window phrase search reach the previous
                # repeated phrase.  This matters for call-and-response lines
                # whose starts are less than a second apart.
                margin=min(self.window_margin,max(0.12,(next_onset-onset)*.25),max(0.02,(onset-previous_onset)*.25))
                left=max(0,int((onset-margin)/.02))
                right=min(len(emission),max(left+1,int((next_onset+(self.window_tail if self.window_tail is not None else min(.08,margin/3)))/.02)))
                def local_align(stop):
                    try:
                        local=align_yohane._align_token_spans(emission[left:stop],tokenize([u['pron'] for u in units]),blank=blank)
                        return [[type(s)(token=s.token,start=s.start+left,end=s.end+left,score=s.score) for s in group] for group in local]
                    except Exception:
                        return []
                shifted=local_align(right)
                # A line followed by an interlude can have its last words dragged across the
                # silence to a lead-in or ad-lib just before the next line. Also try ending the
                # window where the first 2 s silence begins, and keep the better-scoring result.
                quiet=np.flatnonzero(activity[left:right]<.2)
                silence=next((run for run in np.split(quiet,np.flatnonzero(np.diff(quiet)>1)+1) if len(run)>=100),None)
                if silence is not None:
                    early=local_align(left+int(silence[0])+10)
                    mean=lambda groups:float(np.mean([s.score for g in groups for s in g])) if groups and all(groups) else -1
                    if mean(early)>mean(shifted):shifted=early
                usable=bool(shifted) and all(group for group in shifted)
                if usable:
                    constrained.extend(shifted);local_details.append({'line':n+1,'applied':True,'windowStart':left*.02,'windowEnd':right*.02})
                else:
                    start=sum(len(x) for x in row_units[:n]);end=start+len(units)
                    constrained.extend(spans[start:end]);local_details.append({'line':n+1,'applied':False,'reason':'局部窗口未找到完整音节'})
            if len(constrained)==len(voiced):
                spans=constrained
                source['issues']=list(dict.fromkeys(source['issues']+['使用原句时间窗重新对齐重复句，避免副歌跳到前一处']))
        guided_rows=set()
        guide_details=[]
        if all(r['sourceStart'] is not None for r in rows):
            # Existing sentence timestamps locate each phrase, but all token boundaries
            # are newly inferred. This prevents omitted outro vowels and instrumental
            # breaks from dragging CTC's next lyric backward across an entire interlude.
            row_units=[];group=[]
            for item in items:
                if item['orig']=='\n':row_units.append([u for u in group if u.get('pron')]);group=[]
                else:group.append(item)
            offsets=[0]
            for units in row_units:offsets.append(offsets[-1]+len(units))
            selected=set()
            for n,(row,units) in enumerate(zip(rows,row_units)):
                if not units:continue
                current=spans[offsets[n]:offsets[n+1]]
                drift=abs(current[0][0].start*.02-row['sourceStart'])
                duration=current[-1][-1].end*.02-current[0][0].start*.02
                if drift>=4 or duration>=max(16,len(row['original'])*.6):
                    selected.update(range(max(0,n-1),min(len(rows),n+2)))
            blocks=[]
            for n in sorted(selected):
                if blocks and blocks[-1][1]+1==n:blocks[-1][1]=n
                else:blocks.append([n,n])
            for first,last in blocks:
                left,right=offsets[first],offsets[last+1]
                previous_end=spans[left-1][-1].end if left else 0
                next_start=spans[right][0].start if right<len(spans) else len(emission)
                onset=rows[first]['sourceStart'];last_onset=rows[last]['sourceStart']
                next_onset=rows[last+1]['sourceStart'] if last+1<len(rows) else source['duration']
                start_frame=max(previous_end,0,int((onset-1.5)/.02))
                max_duration=max(8,min(20,len(rows[last]['original'])*.35))
                end_frame=min(next_start,len(emission),int(min(next_onset+1,last_onset+max_duration)/.02))
                units=[u for group in row_units[first:last+1] for u in group]
                if end_frame-start_frame<sum(len(u['pron']) for u in units)*2:
                    guide_details.append({'firstLine':first+1,'lastLine':last+1,'applied':False,'reason':'邻句边界内没有足够空间，保留整曲对齐'})
                    continue
                local=align_yohane._align_token_spans(emission[start_frame:end_frame],tokenize([u['pron'] for u in units]),blank=blank)
                shifted=[[type(s)(token=s.token,start=s.start+start_frame,end=s.end+start_frame,score=s.score) for s in group] for group in local]
                oldscore=float(np.mean([s.score for group in spans[left:right] for s in group]))
                newscore=float(np.mean([s.score for group in shifted for s in group]))
                applied=newscore>=oldscore*.8
                guide_details.append({'firstLine':first+1,'lastLine':last+1,'applied':applied,'oldScore':oldscore,'newScore':newscore,'windowStart':start_frame*.02,'windowEnd':end_frame*.02})
                if applied:
                    spans[left:right]=shifted
                    guided_rows.update(range(first,last+1))
        # Whatever produced the spans, a line must not straddle an interlude: if 2 s of silence
        # sits inside a line, try fitting the whole line before it and keep the better score.
        activity=np.convolve(1-emission[:,blank].exp().numpy(),np.ones(7)/7,mode='same')
        cursor=0;group=[]
        for item in items:
            if item['orig']!='\n':group.append(item);continue
            units=[u for u in group if u.get('pron')];group=[]
            current=spans[cursor:cursor+len(units)]
            if units and all(current):
                first,last=current[0][0].start,current[-1][-1].end
                quiet=np.flatnonzero(activity[first:last]<.2)
                silence=next((run for run in np.split(quiet,np.flatnonzero(np.diff(quiet)>1)+1) if len(run)>=100),None)
                if silence is not None:
                    left=max(0,first-5)
                    try:
                        local=align_yohane._align_token_spans(emission[left:first+int(silence[0])+10],tokenize([u['pron'] for u in units]),blank=blank)
                        early=[[type(s)(token=s.token,start=s.start+left,end=s.end+left,score=s.score) for s in g] for g in local]
                    except Exception:
                        early=[]
                    mean=lambda groups:float(np.mean([s.score for g in groups for s in g]))
                    if early and all(early) and mean(early)>mean(current):spans[cursor:cursor+len(units)]=early
            cursor+=len(units)
        scores=[]
        def tag(t):
            ticks=max(0,round(t*100));return f'[{ticks//6000:02}:{ticks//100%60:02}:{ticks%100:02}]'
        for unit,group in zip(voiced,spans):
            if not group:raise ValueError('Untimed sung token')
            unit['start']=tag(group[0].start*.02);unit['end']=tag(group[-1].end*.02)
            unit['score']=float(np.mean([s.score for s in group]));scores.append(unit['score'])
        groups=[];group=[]
        for item in items:
            if item['orig']=='\n':groups.append(group);group=[]
            else:group.append(item)
        if len(groups)!=len(rows):raise ValueError('Lyric line count changed')
        lines=[];ruby=0
        for n,(group,row) in enumerate(zip(groups,rows)):
            if row.get('nonVocal'):
                next_time=rows[n+1]['sourceStart'] if n+1<len(rows) else source['duration']
                end=min(source['duration'],next_time if next_time is not None else source['duration'])
                line={'fullText':row['original'],'translation':row['translation'],'startTime':row['sourceStart'],'endTime':end,'words':[]}
                count=0
            else:
                line,count=build_line(group,row)
                # A first word left stranded seconds before the rest of its line (the model heard
                # its sound in the previous line's tail) is pulled up to just before the second word.
                words=line['words']
                if len(words)>1 and words[1]['startTime']-words[0]['startTime']>2.5 and words[0]['endTime']-words[0]['startTime']<words[1]['startTime']-words[0]['startTime']:
                    new_start=round(max(words[0]['startTime'],words[1]['startTime']-min(.6,.25*max(1,len(words[0]['text'])))),2)
                    def pull(node):
                        for field in ('startTime','endTime'):
                            if isinstance(node.get(field),(int,float)):node[field]=min(max(node[field],new_start),words[1]['startTime'])
                        for field in ('syllables','ruby'):
                            for child in node.get(field) or []:pull(child)
                    pull(words[0]);line['startTime']=new_start
            lines.append(line);ruby+=count
        non_vocal=sum(bool(r.get('nonVocal')) for r in rows)
        if non_vocal:source['issues']=list(dict.fromkeys(source['issues']+[f'{non_vocal}行不是唱出来的内容（如屏幕字幕），保留歌词原来的时间']))
        write_json(folder/'aligned.json',items)
        doc={'format':'folia-lyricdata','version':1,'exportedAt':datetime.now(timezone.utc).isoformat(),'song':{'title':source['title'] or Path(source['audio']).stem,'artist':source['artist'],'durationMs':round(source['duration']*1000)},'source':'local','lyrics':{'lines':lines,'isWordByWord':True}}
        write_json(folder/'aligned.fia',doc)
        shifts=[abs(l['startTime']-r['sourceStart']) for l,r in zip(lines,rows) if r['sourceStart'] is not None]
        problems=[]
        weak=sum(s<.1 for s in scores)/len(scores)
        p75=float(np.percentile(shifts,75)) if shifts else None
        if any(not 0<=l['startTime']<=l['endTime']<=source['duration']+.05 for l in lines):problems.append('输出时间越界')
        if any(b['startTime']<=a['startTime'] for a,b in zip(lines,lines[1:])):problems.append('相邻歌词句首倒序或重合，需检查')
        if any(len(l['fullText'])>5 and l['endTime']-l['startTime']<.08 for l in lines):problems.append('有长句挤在不足80ms内，可能文字错配')
        confidence_warning=float(np.mean(scores))<.2
        if confidence_warning and (weak>.65 or (p75 is not None and p75>2.5)):problems.append('较低声学分数伴随大量弱音节或时间偏差，需试听确认')
        if confidence_warning:source['issues']=list(dict.fromkeys(source['issues']+['声学分数较低，建议重点试听逐字轴']))
        if shifts and np.percentile(shifts,75)>8:problems.append('超过四分之一的句首偏移8秒以上，需核对歌词版本')
        if shifts and max(shifts)>8:problems.append('仍有歌词句首偏移8秒以上，需检查歌词是否缺句/版本不同')
        clipped=sum(n in guided_rows and r['sourceStart'] is not None and r['sourceStart']>1.5 and l['startTime']<=r['sourceStart']-1.45 for n,(l,r) in enumerate(zip(lines,rows)))
        if clipped/max(1,len(rows))>.2:problems.append('大量句首落在定位窗口边缘，原轴可能整体偏移，需重新检查')
        if source.get('suspectTranslationRows',0):problems.append('原文中疑似残留中文译文，需核对后写入')
        result={'key':source['key'],'name':source['name'],'status':'needs_review' if problems else 'aligned','lineCount':len(lines),'rubyGroups':ruby,'meanScore':float(np.mean(scores)),'weakTokenFraction':weak,'confidenceWarning':confidence_warning,'medianStartChange':float(np.median(shifts)) if shifts else None,'p75StartChange':p75,'maximumStartChange':max(shifts) if shifts else None,'guidedRows':guide_details,'issues':source['issues']+problems,'seconds':round(time.monotonic()-start,1),'fia':str(folder/'aligned.fia')}
        if embed and not problems and not source.get('holdEmbedding'):
            language={'ja':'jpn','en':'eng','zh':'zho','ru':'rus','ko':'kor'}.get(source['language'],'und')
            args=['node',str(ROOT/'batch_embed.mjs'),source['audio'],str(folder/'aligned.fia'),str(BACKUPS/source['key']),language,'50']
            if source.get('sidecar'):args.extend([source['sidecar'],source['sidecarSha256']])
            proc=subprocess.run(args,capture_output=True,text=True,encoding='utf-8')
            if proc.returncode:result['status']='embed_error';result['issues'].append(proc.stderr[-1500:])
            else:
                validation=json.loads(proc.stdout);write_json(folder/'embedding-validation.json',validation)
                result['status']='complete';result['backup']=validation['backupPath']
        if embed and source.get('holdEmbedding') and not problems:
            result['status']='held_for_review'
            result['issues'].append('仅生成审查草稿，holdEmbedding=true；确认短版歌词和译文后才写入 MP3')
        write_json(folder/'result.json',result)
        gc.collect();torch.cuda.empty_cache()
        return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int);parser.add_argument('--key');parser.add_argument('--keys',help='Comma-separated keys to process in one model session');parser.add_argument('--draft-only',action='store_true');parser.add_argument('--retry',action='store_true');parser.add_argument('--retry-errors',action='store_true');parser.add_argument('--constrained-lines',action='store_true',help='Use each existing sentence timestamp as a local alignment window')
    args=parser.parse_args()
    sources=json.loads((OUT/'inventory.json').read_text(encoding='utf-8'))
    candidates=[]
    selected_keys=set(args.keys.split(',')) if args.keys else ({args.key} if args.key else None)
    for s in sources:
        result=OUT/s['key']/'result.json'
        if s['status']!='pending' or (selected_keys is not None and s['key'] not in selected_keys):continue
        status=json.loads(result.read_text(encoding='utf-8'))['status'] if result.exists() else None
        if status=='complete':continue
        if args.retry_errors and status not in ('alignment_error','embed_error'):continue
        if not args.retry and not args.retry_errors and result.exists():continue
        candidates.append(s)
    if args.limit:candidates=candidates[:args.limit]
    pipeline=Pipeline()
    for n,source in enumerate(candidates,1):
        try:result=pipeline.process(source,not args.draft_only,constrained_lines=args.constrained_lines)
        except Exception as e:
            result={'key':source['key'],'name':source['name'],'status':'alignment_error','issues':source['issues']+[str(e)],'traceback':traceback.format_exc()}
            write_json(OUT/source['key']/'result.json',result)
            gc.collect();torch.cuda.empty_cache()
        print(json.dumps({'index':n,'total':len(candidates),**result},ensure_ascii=False),flush=True)
        write_json(OUT/'progress.json',{'last':source['name'],'processedThisRun':n,'totalThisRun':len(candidates),'lastStatus':result['status']})

if __name__=='__main__':main()
