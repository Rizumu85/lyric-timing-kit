"""Create actionable Chinese reports from the final per-song batch records."""
import collections
import csv
import json
import re
from pathlib import Path
from batch_inventory import OUT, language, KANA, HAN, ZH_HINT

LANGS={'ja':'日语','en':'英语','zh':'中文','mixed':'中外语混合','ko':'韩语','ru':'俄语','unknown':'未知'}
STATUS={'complete':'已写入 MP3','already_complete':'此前已完成','needs_review':'已有草稿，需复核','alignment_error':'需修正歌词/读音','embed_error':'歌词格式需处理','needs_lyrics':'缺演唱歌词','read_error':'文件读取异常','pending':'尚未处理','reparse_required':'等待重跑'}

def meaningful(text):
    if re.fullmatch(r'[\s・ー－—.\-]+',text):return False
    if re.search(r'[\u3400-\u9fffА-Яа-яЁё가-힣]',text):return True
    clean=re.sub(r'[^A-Za-zぁ-ヿ]','',text).lower()
    if not clean:return False
    if re.fullmatch(r'(?:ah+|oh+|ooh+|woah+|yeah+|la+|na+|da+|ba+|ra+|ha+|hm+|wow+|hey+|yo+)+',clean):return False
    kana=''.join(chr(ord(c)-96) if 'ァ'<=c<='ヶ' else c for c in clean).replace('ー','')
    if len(kana)>=2 and re.fullmatch(r'(?:ら+|る+|あ+|お+|う+)',kana):return False
    return True

def foreign_rows(source):
    lang=source.get('language')
    return [(n,r) for n,r in enumerate(source.get('rows',[]),1) if meaningful(r['original']) and (lang in ('ja','en','ru','ko') or (lang=='mixed' and language(r['original']) not in ('zh','unknown')))]

def main():
    sources=json.loads((OUT/'inventory.json').read_text(encoding='utf-8'))
    all_rows=[];missing=[];timing_attention=[];no_translation=[];partial=[];attention=[];not_written=[];low_confidence=[];counts=collections.Counter()
    for source in sources:
        folder=OUT/source['key'];resultfile=folder/'result.json'
        result=json.loads(resultfile.read_text(encoding='utf-8')) if resultfile.exists() else {}
        status=result.get('status',source['status'])
        counts[status]+=1
        foreign=foreign_rows(source);untranslated=[(n,r) for n,r in foreign if not r['translation']]
        known_translations=collections.defaultdict(set)
        for row in source.get('rows',[]):
            if row['translation']:known_translations[row['original']].add(row['translation'])
        aligned_lines=[]
        if result.get('fia') and Path(result['fia']).exists():aligned_lines=json.loads(Path(result['fia']).read_text(encoding='utf-8'))['lyrics']['lines']
        if status=='needs_review':
            for n,(row,line) in enumerate(zip(source.get('rows',[]),aligned_lines),1):
                delta=line['startTime']-row['sourceStart'] if row['sourceStart'] is not None else None
                if delta is not None and abs(delta)<=3:continue
                timing_attention.append({'歌曲':source['name'],'行号':n,'处理前时间秒':row['sourceStart'],'对齐草稿时间秒':line['startTime'],'变化秒':round(delta,3) if delta is not None else '', '原文':line['fullText'],'中文译文':line.get('translation',''),'对齐草稿':result['fia']})
        if untranslated:
            target=no_translation if len(untranslated)==len(foreign) else partial
            target.append((source,len(untranslated),len(foreign)))
            for n,row in untranslated:
                current_time=max(0,round(aligned_lines[n-1]['startTime']-.05,2)) if status=='complete' and n<=len(aligned_lines) else row['sourceStart']
                choices=known_translations[row['original']]
                reusable=next(iter(choices)) if len(choices)==1 else ''
                missing.append({'歌曲':source['name'],'语言':LANGS.get(source.get('language'),'未知'),'行号':n,'当前MP3歌词时间秒':current_time,'处理前时间秒':row['sourceStart'],'演唱原文':row['original'],'同曲重复句已有译文':reusable,'处理结果':STATUS.get(status,status)})
        issues=list(dict.fromkeys(source.get('issues',[])+result.get('issues',[])))
        issues=[issue for issue in issues if not issue.startswith('外语歌词缺中文翻译：')]
        if untranslated:issues.insert(0,f'有意义的外语正文缺中文翻译 {len(untranslated)}/{len(foreign)} 行')
        score=result.get('meanScore')
        if score is not None and score<.2:
            low_confidence.append((source,result));issues.append('建议重点试听逐字轴：声学分数较低')
        if source.get('language')=='ja' and any(re.search(r'_[A-Za-z]|[A-Za-z]_',r['original']) for r in source.get('rows',[])):
            issues.append('原文含下划线占位，需补全实际演唱文字')
        if any(re.search(r'_[A-Za-z]|[A-Za-z]_',r['original']) for r in source.get('rows',[])):
            issues.append('歌词含下划线占位，建议补全实际演唱文字')
        if any(re.fullmatch(r'end|fin|終わり',r['original'],re.I) for r in source.get('rows',[])):
            issues.append('歌词中有 end/终止标记，需确认它是否实际唱出')
        if status not in ('complete','already_complete','needs_lyrics'):
            not_written.append((source,result,issues))
        if issues:attention.append((source,status,issues))
        all_rows.append({'歌曲':source['name'],'语言':LANGS.get(source.get('language'),'未知'),'处理结果':STATUS.get(status,status),'正文行数':len(source.get('rows',[])),'缺翻译正文行数':len(untranslated),'有意义外语正文行数':len(foreign),'假名组数':result.get('rubyGroups',''),'声学参考分数':round(score,3) if score is not None else '', '原轴句首变化中位数秒':round(result['medianStartChange'],3) if result.get('medianStartChange') is not None else '', '注意事项':'；'.join(dict.fromkeys(issues)),'原 MP3 备份':result.get('backup',''),'对齐草稿':result.get('fia','')})
    for filename,rows,fields in [('全库处理结果.csv',all_rows,list(all_rows[0])),('缺译文的具体歌词行.csv',missing,['歌曲','语言','行号','当前MP3歌词时间秒','处理前时间秒','演唱原文','同曲重复句已有译文','处理结果']),('需复核的具体歌词行.csv',timing_attention,['歌曲','行号','处理前时间秒','对齐草稿时间秒','变化秒','原文','中文译文','对齐草稿'])]:
        with (OUT/filename).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    lines=['# Music MP3 打轴与补充清单','',f'共检查 {len(sources)} 首 MP3。本批已写入 {counts["complete"]} 首，此前完成 {counts["already_complete"]} 首，缺歌词 {counts["needs_lyrics"]} 首，需要复核或补充原文/读音 {len(not_written)} 首。','',
        '写入后的普通 LRC 保留原有中文翻译；有日语汉字注音的歌曲另嵌入 TimeTag-Ruby LRC。50 ms 提前量已写进这两份歌词时间戳。原有明确读音优先，其他注音由词典生成，特殊唱法仍应试听核对。音频未重编码，原 MP3 备份在实验目录。','',
        f'有 {len(no_translation)} 首外语歌完全缺中文译文，{len(partial)} 首缺部分译文。纯 Ah/Oh/La 等哼唱、单独数字不计入缺译正文。中外语混合歌曲只统计外语段落。','',
        '## 需要提供演唱原文','']
    for source in sources:
        if source['status']=='needs_lyrics':lines.append(f'- {source["name"]}')
    lines+=['','上面若有纯音乐，请标记为纯音乐，无需补歌词。','', '## 未写入 Music，需补充或复核','']
    for source,result,issues in not_written:
        lines.append(f'- **{source["name"]}**：'+ '；'.join(i.split('file:///')[0].strip() or 'Ruby 格式往返校验未通过' for i in dict.fromkeys(issues)))
        error=next((i.split(': ',1)[1] for i in issues if i.startswith('Cannot determine reading: ')),None)
        if error:
            examples=[r for r in source.get('rows',[]) if error in r['original']]
            for row in examples[:2]:lines.append(f'  - 需确认读音/文字：{row["sourceStart"]} 秒「{row["original"]}」')
        if any('保留字符' in issue for issue in issues):
            for row in [r for r in source.get('rows',[]) if re.search(r'[{}\[\]]',r['original'])][:3]:
                lines.append(f'  - 需核对原文：{row["sourceStart"]} 秒「{row["original"]}」')
        if source.get('suspectTranslationRows'):
            examples=[r for r in source.get('rows',[]) if not KANA.search(r['original']) and len(HAN.findall(r['original']))>4 and (len(ZH_HINT.findall(r['original']))>=2 or re.search('[的了呢呀啦啊吧你它]',r['original']))]
            for row in examples[:3]:lines.append(f'  - 需确认是中文演唱还是未配对译文：{row["sourceStart"]} 秒「{row["original"]}」；如果是译文，需要补实际演唱原文。')
    lines+=['','句首变化超过 3 秒的草稿原文、旧轴和新轴在同目录「需复核的具体歌词行.csv」。较大偏差只能说明需要核对，不能单凭此断定原歌词有误；可能需要补漏句、确认音频版本或人工修轴。','', '## 完全没有中文译文','']
    lines.extend(f'- {s["name"]}（{LANGS.get(s.get("language"),"未知")}，{n} 行）' for s,n,total in no_translation)
    lines+=['','## 中文译文不全','']
    lines.extend(f'- {s["name"]}（缺 {n}/{total} 行）' for s,n,total in partial)
    reusable_count=sum(bool(row['同曲重复句已有译文']) for row in missing)
    lines+=['',f'缺译的具体原文、行号、当前时间在同目录「缺译文的具体歌词行.csv」。其中 {reusable_count} 行在同曲内有完全相同原句的唯一现成译文，已放入「同曲重复句已有译文」列供参考，尚未自动填入 MP3。补译文时沿用当前 MP3 原文的时间戳。','', '## 已写入但建议重点试听','']
    lines.extend(f'- {s["name"]}（参考分数 {r.get("meanScore",0):.3f}）' for s,r in low_confidence if r['status']=='complete')
    lines+=['','## 其他注意事项','']
    for source,status,issues in attention:
        extra=[i for i in issues if not any(t in i for t in ['缺中文翻译','分数较低','声学对齐','整体声学']) and not i.startswith('file:///')]
        if extra and status=='complete':lines.append(f'- {source["name"]}：'+ '；'.join(extra))
    lines+=['','Folia 若仍显示旧轴，请刷新本地音乐扫描/歌词缓存。Music 中未新增批量外置歌词文件；已有同名 LRC 在成功写入对应 MP3 后同步为普通兼容歌词。','']
    (OUT/'需要补充的歌词与翻译.md').write_text('\n'.join(lines),encoding='utf-8')
    summary={'total':len(sources),'statuses':counts,'noTranslation':len(no_translation),'partialTranslation':len(partial),'missingTranslationLines':len(missing),'reusableSameSongTranslationLines':reusable_count,'notWritten':len(not_written),'lowConfidenceWritten':sum(r['status']=='complete' for _,r in low_confidence)}
    (OUT/'final-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
