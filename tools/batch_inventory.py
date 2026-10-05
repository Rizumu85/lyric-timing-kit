"""Read-only inventory and conservative lyric preparation for the Music batch."""
import collections
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from opencc import OpenCC
from kit_config import MUSIC, OUT, SINGER_ALIASES

ROOT = Path(__file__).resolve().parent
TIME = re.compile(r'\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]')
KANA = re.compile(r'[ぁ-ヿ]')
HAN = re.compile(r'[\u3400-\u9fff]')
CYR = re.compile(r'[А-Яа-яЁё]')
LATIN = re.compile(r'[A-Za-z]')
HANGUL = re.compile(r'[가-힣]')
ZH_HINT = re.compile(r'[这们让为没还从说对过会给着与却将爱梦里边时头间开来见听谁么样无体后别么吗总]')
ZH_INLINE = re.compile(r'[的了呢呀啦啊吧你我它]|(?:甜美|深吻|最喜|抱歉|扼杀|夏天|冬天|何处|在此|在彼|不断|孑然|自我|真是|所以|一直|展翅|翱翔)')
S2T = OpenCC('s2t')
T2JP = OpenCC('t2jp')
CREDIT = re.compile(r'^(?:作[词詞曲]|词|詞|曲|编曲|編曲|制作人|製作人|制作|製作|混音|录音|錄音|母带|母帶|出品|策划|企划|监制|音乐|音樂|Producer|Composer|Lyricist|Lyrics|Vocal|Guitar|Bass|Drum|Piano|Special Thanks|Copyright|演唱|人声|和声|和聲|键盘|吉他|主唱|音响|贝斯|鼓|鸣谢|发行|统筹|视频|封面|设计|发布|音频|扒带|曲绘|调教|調教|曲编|作成|歌唱|翻唱|原唱|原曲|来源|翻译|翻譯|字幕|后期|伴奏|协力|录制|合成|扒谱|填词|中文填词|Original|Mix|Mastering|Recording|Arrang|Written|Produced|Performed|All instruments)\s*[:：/／\-]', re.I)

def language(text):
    if KANA.search(text): return 'ja'
    if CYR.search(text): return 'ru'
    if HANGUL.search(text): return 'ko'
    first_han=HAN.search(text)
    if first_han and len(re.findall(r'[A-Za-z]{2,}',text[:first_han.start()]))>=2: return 'en'
    if HAN.search(text): return 'zh'
    if LATIN.search(text): return 'en'
    return 'unknown'

def parse_text(text):
    rows=[]
    for raw in text.replace('\ufeff','').splitlines():
        matches=list(TIME.finditer(raw))
        if not matches:
            if raw.strip() and not re.match(r'^\[\w+:|^@',raw): rows.append({'sourceStart':None,'text':raw.strip()})
            continue
        body=TIME.sub('',raw).strip()
        if not body: continue
        # Expand repeated leading timestamps; enhanced timing within a line is not repetition.
        starts=[]
        for m in matches:
            if raw[matches[0].end():m.start()].strip(): break
            starts.append(int(m[1])*60+int(m[2])+(int(m[3])/10**len(m[3]) if m[3] else 0))
        for start in starts or [0]: rows.append({'sourceStart':start,'text':body})
    return rows

def split_inline(text, expected_language=None):
    for separator in [' / ', ' ／ ', '｜', ' | ', '\t']:
        if separator not in text: continue
        left,right=text.split(separator,1)
        if language(left) in ('ja','en','ru','ko') and language(right)=='zh': return left.strip(),right.strip(),'delimiter'
    first_han=HAN.search(text)
    if first_han and not KANA.search(text) and not HANGUL.search(text) and not CYR.search(text) and language(text)=='en':
        return text[:first_han.start()].strip(),text[first_han.start():].strip(),'script-boundary'
    if KANA.search(text):
        for m in re.finditer(r'\s+',text):
            left,right=text[:m.start()],text[m.end():]
            chinese_hint=ZH_HINT.search(right) or ZH_INLINE.search(right) or T2JP.convert(S2T.convert(right))!=right
            if KANA.search(left) and not KANA.search(right) and len(HAN.findall(right))>=2 and chinese_hint:
                return left.strip(),right.strip(),'space-inferred'
    elif expected_language=='ja':
        for m in re.finditer(r'\s+',text):
            left,right=text[:m.start()],text[m.end():]
            first=right.split()[0] if right.split() else ''
            hint=ZH_INLINE.search(first) or T2JP.convert(S2T.convert(first))!=first
            if HAN.search(left) and len(HAN.findall(right))>=2 and hint:
                return left.strip(),right.strip(),'space-inferred'
    return text,'',None

def prepare(text, filename):
    parsed=parse_text(text)
    kept=[]; discarded=[]
    artist_name=unicodedata.normalize('NFC',filename.split(' - ')[0]).casefold()
    # short singer labels used in duet lyric sheets ("c:" for one singer), from the configuration
    aliases=SINGER_ALIASES
    singer_labels=collections.Counter()
    labels={r['text'][:-1] for r in parsed if re.fullmatch(r'[^:：\s]{1,8}[:：]',r['text'])}
    for row in parsed:
        body=row['text']
        if re.search(r'未经.*(?:许可|授权)|不得.*(?:翻唱|翻录|使用)|版权所有|版权归|禁止.*(?:转载|商业)|all rights reserved|^\s*[©℗]',body,re.I):discarded.append(row);continue
        if not re.search(r'[\wぁ-ヿ]',body) or re.fullmatch(r'\[[^\]]+\]',body):discarded.append(row);continue
        if body.endswith((':','：')) and body[:-1] in labels:discarded.append(row);continue
        for label in labels:
            body=re.sub(r'^'+re.escape(label)+r'[:：]\s*','',body)
        row={**row,'text':body}
        prefix=re.split(r'[:：]',body,1)[0]
        credit_field=bool(re.search(r'作[詞词曲]|[编編]曲|制[作片]|[录錄]音|混音|母[带帶]|[监監]制|[总總][监監]|和[声聲音]|配唱|人[声聲]|[乐樂]器|[乐樂]团|吉他|鼓手|弦[乐樂]|[发發]行|[词詞]作者|曲作者|音[频頻]|[编編][辑輯]|后期|策[划劃]|[统統][筹籌]|出品|[鸣鳴][谢謝]|(?:^|\s)(?:OP|SP|Program|Published|Vocals?|Music|Lyric|Compos|Mix|Master|Record|Arrang|Produce|Guitar|Drums?|Bass|Piano|Chorus|Keyboard|Instrument)|唄|歌手',prefix,re.I))
        if CREDIT.search(body) or re.match(r'^(?:作[詞词曲]|編曲|编曲|制作人|製作人|混音|母带|录音)\s+\S',body) or (len(prefix)<45 and prefix!=body and credit_field) or re.match(r'^(?:Music|Lyrics?|Vocals?|Composed|Written|Produced|Performed|Arranged|Mixed|Mastered)\s+by\b',body,re.I): discarded.append(row); continue
        # A leading title + artist credit, using a literal artist/title portion of the filename.
        filename_artist=filename.split(' - ')[0]
        if row['sourceStart'] is not None and row['sourceStart']<8 and re.search(r'\S\s*[-－]\s*\S',body) and filename_artist in body:
            discarded.append(row);continue
        if row['sourceStart'] is not None and row['sourceStart']<5 and ' - ' in body:
            discarded.append(row); continue
        cue=unicodedata.normalize('NFC',re.sub(r'[【】\[\]（）()\s]','',prefix)).casefold()
        if prefix!=body and 0<len(cue)<=12 and (cue in artist_name or (cue in aliases and aliases[cue] in artist_name) or cue in ('合','合唱','齐','齐唱','全','全员','全体','众','all','chorus')):
            body=re.sub(r'^'+re.escape(prefix)+r'[:：]+\s*','',body)
            row={**row,'text':body};singer_labels[prefix]+=1
        kept.append(row)
    groups=collections.OrderedDict()
    for n,row in enumerate(kept): groups.setdefault(row['sourceStart'] if row['sourceStart'] is not None else f'untimed:{n}',[]).append(row)
    starts=sorted(k for k in groups if isinstance(k,(int,float)))
    for left,right in zip(starts,starts[1:]):
        if left not in groups or right not in groups or right-left>.03:continue
        combined=groups[left]+groups[right]
        langs={language(r['text']) for r in combined}
        if 'zh' in langs and langs.intersection({'ja','en','ru','ko'}):
            groups[left].extend(groups.pop(right))
    votes=collections.Counter(language(row['text']) for row in kept)
    lang=max(votes,key=votes.get) if votes else 'unknown'
    for foreign in ('ja','ru','ko'):
        if votes[foreign]>=3: lang=foreign;break
    if lang=='zh' and votes['en']>=max(3,votes['zh']*.6): lang='en'
    output=[]; issues=[]; inferred=0
    for group in groups.values():
        texts=list(dict.fromkeys(row['text'] for row in group))
        if lang=='ja': original=next((text for text in texts if KANA.search(text)),texts[0])
        elif lang=='ru': original=next((text for text in texts if CYR.search(text)),texts[0])
        elif lang=='en': original=next((text for text in texts if LATIN.search(text)),texts[0])
        else: original=texts[0]
        original,inline,kind=split_inline(original,lang)
        translation=inline or next((text for text in texts if text!=original and language(text)=='zh' and lang!='zh'), '')
        if kind=='space-inferred': inferred+=1
        if len(texts)>2: issues.append('同时间戳有三行或更多，需检查和声/罗马音/翻译分配')
        output.append({'sourceStart':group[0]['sourceStart'],'original':original,'translation':translation})
    # Untimed Chinese after otherwise timed foreign lyrics is not sung input.
    if lang!='zh' and any(r['sourceStart'] is not None for r in output):
        output=[r for r in output if not (r['sourceStart'] is None and language(r['original'])=='zh')]
    if any(r['sourceStart'] is None for r in output) and any(r['sourceStart'] is not None for r in output):
        issues.append('带时间歌词中还有未打轴文字，需要确认是否正文')
    if output and all(r['sourceStart'] is not None for r in output): output.sort(key=lambda r:r['sourceStart'])
    row_langs=collections.Counter(language(r['original']) for r in output)
    if lang not in ('ja','ru','ko') and row_langs['zh']>=3 and row_langs['en']>=3:lang='mixed'
    if inferred: issues.append(f'{inferred}行原文/译文按空格和文字特征拆分，需复核')
    if not output: issues.append('没有可用的歌词正文')
    foreign=[r for r in output if lang in ('ja','ru','ko','en') or (lang=='mixed' and language(r['original'])!='zh')]
    missing=sum(not r['translation'] for r in foreign)
    if missing: issues.append(f'外语歌词缺中文翻译：{missing}/{len(foreign)}行')
    suspect=[r for r in output if lang=='ja' and not KANA.search(r['original']) and len(HAN.findall(r['original']))>4 and (len(ZH_HINT.findall(r['original']))>=2 or re.search('[的了呢呀啦啊吧你它]',r['original']))]
    if suspect:issues.append(f'{len(suspect)}行疑似未配对的中文译文，需确认演唱原文')
    return {'language':lang,'rows':output,'foreignLines':len(foreign),'missingTranslationLines':missing,'suspectTranslationRows':len(suspect),'discardedCredits':discarded,'removedSingerLabels':dict(singer_labels),'issues':list(dict.fromkeys(issues))}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    result=[]
    sidecars={unicodedata.normalize('NFC',p.stem).casefold():p for p in MUSIC.glob('*.lrc')}
    for path in sorted(MUSIC.glob('*.mp3')):
        key=hashlib.sha256(str(path).encode()).hexdigest()[:12]
        previous=OUT/key/'result.json'
        if previous.exists() and json.loads(previous.read_text(encoding='utf-8'))['status']=='complete':
            result.append(json.loads((OUT/key/'source.json').read_text(encoding='utf-8')));continue
        item={'key':key,'audio':str(path),'name':path.name,'status':'pending','issues':[]}
        try:
            audio=MP3(path);tags=audio.tags
            item['duration']=audio.info.length
            item['title']=str(tags.get('TIT2','')) if tags else ''
            item['artist']=str(tags.get('TPE1','')) if tags else ''
            uslt=tags.getall('USLT') if tags else []
            item['lyricVersions']=[{'language':t.lang,'descriptor':t.desc,'characters':len(t.text)} for t in uslt]
            plain=[t for t in uslt if '@Ruby' not in t.text]
            source=next((t for t in plain if not t.desc),None) or next(iter(plain),None)
            text=source.text if source else ''
            item['source']='embedded' if text else None
            sidecar=sidecars.get(unicodedata.normalize('NFC',path.stem).casefold())
            if sidecar:
                item['sidecar']=str(sidecar)
                item['sidecarSha256']=hashlib.sha256(sidecar.read_bytes()).hexdigest()
                if not text:
                    raw=sidecar.read_bytes()
                    for enc in ['utf-8-sig','utf-16','gb18030']:
                        try: text=raw.decode(enc);break
                        except UnicodeError: pass
                    item['source']='sidecar'
                else: item['issues'].append('同时有外置歌词，写入内嵌轴后需处理 Folia 的歌词来源优先级')
            if any('@Ruby' in t.text for t in uslt):  # already carries word-timed lyrics with readings
                item['status']='already_complete';item['language']='ja'
            elif text:
                info=prepare(text,path.name)
                item.update({k:v for k,v in info.items() if k!='issues'})
                item['issues'].extend(info['issues'])
                if not info['rows']: item['status']='needs_lyrics'
            else:
                item['status']='needs_lyrics';item['language']='unknown';item['rows']=[];item['issues'].append('没有内嵌歌词或同名 LRC，请提供演唱原文')
            folder=OUT/key;folder.mkdir(exist_ok=True)
            (folder/'original-lyrics.txt').write_text(text,encoding='utf-8')
            (folder/'source.json').write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as e:
            item['status']='read_error';item['issues'].append(f'{type(e).__name__}: {e}')
        result.append(item)
    (OUT/'inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    counts=collections.Counter(x['status'] for x in result)
    languages=collections.Counter(x.get('language','unknown') for x in result)
    translations=collections.Counter('none' if x.get('missingTranslationLines')==x.get('foreignLines') else 'partial' if x.get('missingTranslationLines',0)>0 else 'complete' for x in result if x.get('foreignLines',0)>0 and x['status']=='pending')
    print(json.dumps({'total':len(result),'statuses':counts,'languages':languages,'foreignTranslations':translations},ensure_ascii=False,indent=2))
    print('MISSING LYRICS:')
    for item in result:
        if item['status'] in ('needs_lyrics','read_error'): print(item['name'],item['issues'])

if __name__=='__main__': main()
