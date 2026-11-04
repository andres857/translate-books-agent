"""Auditable original-font coverage and style-equivalent OFL fallback instances."""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
import pymupdf as fitz
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from .core import Blocked,sha256,atomic_json

ROOT=Path(__file__).resolve().parents[2]

def required_characters(text):
    return sorted(set(c for c in text if not c.isspace()))

def covers(font,text):
    return all(font.has_glyph(ord(c),fallback=False) for c in required_characters(text))

def original_font(doc,page,font_name,folder):
    normalized=lambda name:re.sub(r'^[A-Z]{6}\+','',name)
    for entry in page.get_fonts(full=True):
        if normalized(entry[3])!=normalized(font_name):continue
        name,ext,kind,data=doc.extract_font(entry[0])
        if not data:
            try:data=fitz.Font(fontname=normalized(entry[3])).buffer;ext='cff'
            except Exception:continue
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        path=folder/(hashlib.sha256(data).hexdigest()[:16]+'.'+ext)
        if not path.exists():path.write_bytes(data)
        font=fitz.Font(fontbuffer=data)
        return {'path':str(path),'name':name,'sha256':sha256(path),'font':font,'source':'original','extension':ext}
    raise Blocked(f'Cannot extract original font: {font_name}')

def fallback_font(style,folder,reference_font=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    if style['serif']:
        filename='RobotoSerif-Italic[GRAD,opsz,wdth,wght].ttf' if style['italic'] else 'RobotoSerif[GRAD,opsz,wdth,wght].ttf'
        axes={'GRAD':0,'opsz':max(8,min(144,float(style['size']))),'wdth':float(style['width']),'wght':float(style['weight'])}
        family_prefix='AstroSerif-'
    else:
        filename='NotoSans-Italic[wdth,wght].ttf' if style['italic'] else 'NotoSans[wdth,wght].ttf'
        axes={'wdth':max(62.5,min(100,float(style['width']))),'wght':float(style['weight'])}
        family_prefix='AstroSans-'
    base=ROOT/'fonts'/filename
    if not base.exists():raise Blocked('Official variable style-equivalent fallback is missing')
    key=hashlib.sha256((sha256(base)+str(sorted(axes.items()))+'-no-gsub-ligatures-unique-v3').encode()).hexdigest()[:16]
    path=folder/f'RobotoSerif-{key}.ttf'
    if not path.exists():
        font=TTFont(base);static=instantiateVariableFont(font,axes,inplace=False)
        # Story currently emits an incorrect ToUnicode mapping for this font's
        # GSUB fi/fl ligatures. Spanish Latin text needs the original characters,
        # not substitution glyphs; preserve GPOS kerning and all outline styles.
        if 'GSUB' in static:del static['GSUB']
        _rename_static(static,family_prefix+key,style)
        static.save(path);static.close();font.close()
    calibration=None
    chosen=fitz.Font(fontfile=str(path))
    if reference_font is not None:
        reference='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        common=''.join(c for c in reference if reference_font.has_glyph(ord(c),fallback=False) and chosen.has_glyph(ord(c),fallback=False))
        ratio=reference_font.text_length(common,fontsize=1)/chosen.text_length(common,fontsize=1) if common else 1.
        ratio=max(.45,min(1.25,ratio));ratio=round(ratio,6)
        calibrated_key=hashlib.sha256((sha256(path)+str(ratio)+'-horizontal-v2').encode()).hexdigest()[:16]
        calibrated=folder/f'AstroSerif-{calibrated_key}.ttf'
        if not calibrated.exists():
            font=TTFont(path);_scale_horizontal(font,ratio);_rename_static(font,family_prefix+calibrated_key,style);font.save(calibrated);font.close()
        path=calibrated;chosen=fitz.Font(fontfile=str(path))
        calibration={'reference_characters':common,'horizontal_scale':ratio,'source_advance_em':reference_font.text_length(common,fontsize=1),'chosen_advance_em':chosen.text_length(common,fontsize=1)}
    return {'path':str(path),'name':chosen.name,'sha256':sha256(path),'font':chosen,'source':'fallback','axes':axes,'base_sha256':sha256(base),'gsub_disabled_for_pdf_text_integrity':True,'metric_calibration':calibration,'style':style}

def original_text_roundtrip(choice,text):
    """Coverage alone is insufficient for subset CFF ToUnicode mappings."""
    import html,unicodedata
    norm=lambda value:''.join(c for c in unicodedata.normalize('NFKC',value) if not c.isspace() and c!='\u00ad')
    doc=fitz.open();page=doc.new_page(width=3000,height=3000)
    path=Path(choice['path'])
    css=f'@font-face {{font-family:proof;src:url("{path.name}")}} body {{font-family:proof;font-size:8pt;}}'
    try:
        spare,_=page.insert_htmlbox(fitz.Rect(1,1,2999,2999),html.escape(text),css=css,archive=fitz.Archive(str(path.parent)),scale_low=1)
        return spare>=0 and norm(page.get_text())==norm(text)
    finally:doc.close()

def select_group_fonts(doc,page,units,folder):
    """Fallback for the whole sibling group if any member's font lacks a glyph."""
    by_group={}
    for unit in units:by_group.setdefault(unit['scale_group'],[]).append(unit)
    mapping={};audit=[]
    for group,members in by_group.items():
        styles={};originals={};missing={}
        for unit in members:
            for run in unit.get('target_runs',[{'text':unit['target'],'style':unit['style']}]):
                style=run['style'];key=tuple(sorted(style.items()));styles.setdefault(key,{'style':style,'text':''})['text']+=run['text']
        for key,entry in styles.items():
            original=original_font(doc,page,entry['style']['font'],Path(folder)/'original');originals[key]=original
            missing[key]=[c for c in required_characters(entry['text']) if not original['font'].has_glyph(ord(c),fallback=False)]
        roundtrip_failures={key:not original_text_roundtrip(originals[key],entry['text']) for key,entry in styles.items() if not missing[key]}
        use_fallback=any(missing.values()) or any(roundtrip_failures.values()) or any(e['style'].get('force_fallback') for e in styles.values())
        for key,entry in styles.items():
            chosen=fallback_font(entry['style'],Path(folder)/'fallback',originals[key]['font']) if use_fallback else originals[key]
            if not covers(chosen['font'],entry['text']):raise Blocked('Selected font lacks translated characters')
            mapping[(group,key)]=chosen
            audit.append({'scale_group':group,'original_name':entry['style']['font'],'original_sha256':originals[key]['sha256'],'required_chars':required_characters(entry['text']),'original_missing':missing[key],'original_text_roundtrip_failed':roundtrip_failures.get(key,False),'group_fallback':use_fallback,'chosen':{k:v for k,v in chosen.items() if k!='font'},'style':entry['style']})
    return mapping,audit


def _rename_static(font,family,style):
    subfamily=('Bold' if style['weight']>=600 else 'Medium' if style['weight']>=500 else 'Light' if style['weight']<=300 else 'Regular')+('Italic' if style['italic'] else '')
    names={1:family,2:subfamily,3:family+'-'+subfamily,4:family+' '+subfamily,6:family+'-'+subfamily,16:family,17:subfamily}
    for name_id,value in names.items():
        for platform,encoding,language in ((3,1,0x409),(1,0,0)):
            font['name'].setName(value,name_id,platform,encoding,language)
    os2=font['OS/2'];os2.usWeightClass=int(style['weight']);os2.usWidthClass=2 if style['width']<=60 else 3 if style['width']<100 else 5
    os2.fsSelection &= ~((1<<0)|(1<<5)|(1<<6))
    if style['italic']:os2.fsSelection|=1
    if style['weight']>=600:os2.fsSelection|=1<<5
    if not style['italic'] and style['weight']<600:os2.fsSelection|=1<<6
    font['head'].macStyle=(1 if style['weight']>=600 else 0)|(2 if style['italic'] else 0)

def _scale_horizontal(font,ratio):
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    glyphset=font.getGlyphSet();glyphs={}
    for name in font.getGlyphOrder():
        recording=DecomposingRecordingPen(glyphset);glyphset[name].draw(recording)
        pen=TTGlyphPen(None);recording.replay(TransformPen(pen,(ratio,0,0,1,0,0)));glyphs[name]=pen.glyph()
    font['glyf'].glyphs=glyphs
    font['hmtx'].metrics={name:(round(advance*ratio),round(lsb*ratio)) for name,(advance,lsb) in font['hmtx'].metrics.items()}
    for attribute in ('advanceWidthMax','minLeftSideBearing','minRightSideBearing','xMaxExtent'):
        setattr(font['hhea'],attribute,round(getattr(font['hhea'],attribute)*ratio))
    font['OS/2'].xAvgCharWidth=round(font['OS/2'].xAvgCharWidth*ratio)
    visited=set()
    def adjust(obj):
        if id(obj) in visited:return
        visited.add(id(obj))
        if isinstance(obj,list):
            for value in obj:adjust(value)
        elif hasattr(obj,'__dict__'):
            for key,value in vars(obj).items():
                if key in ('XAdvance','XPlacement','XCoordinate') and isinstance(value,(int,float)):setattr(obj,key,round(value*ratio))
                else:adjust(value)
    if 'GPOS' in font:adjust(font['GPOS'].table)
