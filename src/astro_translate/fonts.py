"""Auditable original-font coverage and style-equivalent licensed fallbacks."""
from __future__ import annotations
import hashlib
import json
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

# Normalized PostScript-name prefixes of families whose PDF serif flag is
# unreliable. Consulted by name-based classification (survey, and fallback
# selection for books that opt in with [fonts].classify_slab).
FAMILY_CLASSES=(
    ('serifa','slab'),('zillaslab','slab'),('bitter','slab'),('clarendon','slab'),('archer','slab'),
    ('avenir','sans'),('futura','sans'),('frutiger','sans'),('dinnext','sans'),('helvetica','sans'),
    ('arial','sans'),('gillsans','sans'),('univers','sans'),('myriad','sans'),('gotham','sans'),
    ('proximanova','sans'),('opensans','sans'),('notosans','sans'),('sourcesans','sans'),
    ('lilitaone','display'),
)
# Display families replaced by the same libre design (exact family, OFL).
DISPLAY_FILES={'lilitaone':'LilitaOne-Regular.ttf'}
# Base files whose OFL Reserved Font Name must not survive in derived fonts.
RESERVED_NAME_BASES=frozenset({'Bitter-Italic[wght].ttf','LilitaOne-Regular.ttf'})
BASE_CLASSES={'RobotoSlab[wght].ttf':'slab','Bitter-Italic[wght].ttf':'slab',
              'RobotoSerif[GRAD,opsz,wdth,wght].ttf':'serif','RobotoSerif-Italic[GRAD,opsz,wdth,wght].ttf':'serif',
              'NotoSans[wdth,wght].ttf':'sans','NotoSans-Italic[wdth,wght].ttf':'sans',
              'LilitaOne-Regular.ttf':'display'}
WEIGHT_NAMES=(('extralight',200),('ultralight',200),('semibold',600),('demibold',600),('extrabold',800),
              ('ultrabold',800),('hairline',100),('thin',100),('light',300),('medium',500),('heavy',900),
              ('black',900),('bold',700),('book',400),('regular',400))
STEM_CHARACTERS='lIHTnhmi'
REFERENCE_CHARACTERS='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'

def _normalized_name(style):
    name=re.sub(r'^[A-Z]{6}\+','',style['font'])
    return re.sub(r'[^a-z0-9]','',name.lower())

def fallback_class(style,families=None,*,known=True):
    """Known slab families, independent of unreliable PDF serif flags.

    Normalize subset prefixes/spaces so Soho Pro, Rockwell, Museo Slab and
    Roboto Slab variants share a class. Soho Gothic is explicitly sans.
    ``known`` adds FAMILY_CLASSES (Avenir/Futura sans, Serifa slab, Lilita
    display); fallback selection passes the book's classify_slab opt-in so
    legacy books keep their validated flag-based choice.
    Other families retain the caller's existing serif/sans classification.
    """
    name=_normalized_name(style)
    for prefix,family in (families or {}).items():
        if name.startswith(prefix):return family
    if name.startswith('soho') and 'gothic' in name:return 'sans'
    if name.startswith(('soho','rockwell','museoslab','robotoslab')):return 'slab'
    if known:
        for prefix,family in FAMILY_CLASSES:
            if name.startswith(prefix):return family
    return 'serif' if style['serif'] else 'sans'

def effective_weight(style):
    """CSS weight from the style part of the PostScript name (ExtraLight=200, Heavy=900)."""
    name=re.sub(r'^[A-Z]{6}\+','',style['font'])
    part=re.sub(r'[^a-z]','',(name.rsplit('-',1)[-1] if '-' in name else name).lower())
    for token,weight in WEIGHT_NAMES:
        if token in part:return weight
    return style['weight']

def _stems(font,chars,*,size=400,dpi=144):
    """First vertical-stem run (em) at 35 % of each glyph's ink height."""
    import numpy as np
    result={}
    for c in chars:
        if not font.has_glyph(ord(c),fallback=False):continue
        doc=fitz.open()
        try:
            page=doc.new_page(width=size*2,height=size*2)
            writer=fitz.TextWriter(page.rect);writer.append((size*.5,size*1.5),c,font=font,fontsize=size);writer.write_text(page)
            pix=page.get_pixmap(dpi=dpi,colorspace=fitz.csGRAY)
        finally:doc.close()
        ink=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width)<128
        rows=np.where(ink.any(axis=1))[0]
        if not rows.size:continue
        line=ink[int(round(rows.max()-.35*(rows.max()-rows.min())))]
        xs=np.where(line)[0]
        if not xs.size:continue
        end=xs[0]
        while end+1<line.size and line[end+1]:end+=1
        result[c]=(end-xs[0]+1)*72/dpi/size
    return result

def stem_width_em(font,chars):
    """Median stem width in em over ``chars`` (None if no measurable glyph)."""
    import statistics
    stems=_stems(font,chars)
    return statistics.median(stems.values()) if stems else None

def _calibrated_weight(base,axes,reference_font,nominal):
    """wght whose stems, after horizontal width calibration, match the source.

    Narrowing a regular-width design to Compressed/Condensed advances thins
    its vertical stems by the same factor; Light Compressed then reads as
    ExtraLight. Measure the source subset's stems (rendered, any font format)
    and bisect wght on a small subset instance of the variable base.
    """
    import io,statistics
    from fontTools import subset
    source=_stems(reference_font,STEM_CHARACTERS)
    font=TTFont(base);cmap=font.getBestCmap()
    chars=''.join(c for c in source if ord(c) in cmap)
    common=''.join(c for c in REFERENCE_CHARACTERS if reference_font.has_glyph(ord(c),fallback=False) and ord(c) in cmap)
    if len(chars)<2 or not common:
        font.close();return None
    options=subset.Options();options.layout_features=[];options.name_IDs=['*'];options.notdef_outline=True
    subsetter=subset.Subsetter(options);subsetter.populate(text=chars+common);subsetter.subset(font)
    buffer=io.BytesIO();font.save(buffer);font.close();data=buffer.getvalue()
    target=statistics.median(source[c] for c in chars)
    reference=reference_font.text_length(common,fontsize=1)
    axis=next(a for a in TTFont(io.BytesIO(data))['fvar'].axes if a.axisTag=='wght')
    lo=max(axis.minValue,nominal-400);hi=min(axis.maxValue,nominal+400)
    def measure(weight):
        out=io.BytesIO();instantiateVariableFont(TTFont(io.BytesIO(data)),{**axes,'wght':weight},inplace=False).save(out)
        chosen=fitz.Font(fontbuffer=out.getvalue())
        ratio=max(.45,min(1.25,reference/chosen.text_length(common,fontsize=1)))
        stems=_stems(chosen,chars)
        return ratio*statistics.median(stems[c] for c in chars)
    low,high=measure(lo),measure(hi)
    if target<=low:weight,clamped=lo,True
    elif target>=high:weight,clamped=hi,True
    else:
        a,b=lo,hi
        for _ in range(8):
            mid=(a+b)/2
            if measure(mid)<target:a=mid
            else:b=mid
        weight,clamped=round((a+b)/2),False
    return {'nominal':nominal,'chosen':float(weight),'clamped':clamped,'characters':chars,
            'source_stem_em':round(target,5),'method':'rendered first-stem width at 35% ink height, after horizontal calibration'}

def fallback_font(style,folder,reference_font=None,*,classify_slab=False,families=None,calibrate_weight=False,calibrate_space=False):
    """Slab is opt-in per book; preserve existing wisdom-stars instances.

    RobotoSlab only has wght: condensation uses the reference-font horizontal
    calibration below. Slab italics use Bitter Italic (OFL, wght axis); the
    Lilita One display face is replaced by its own libre release. With
    ``calibrate_weight`` the wght axis is tuned so stems survive narrowing;
    with ``calibrate_space`` the word space takes the source advance (the
    uniform horizontal scale only matches letters and digits).
    """
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    family=fallback_class(style,families,known=classify_slab) if classify_slab or families else ('serif' if style['serif'] else 'sans')
    weight=float(effective_weight(style) if calibrate_weight else style['weight'])
    display=next((f for prefix,f in DISPLAY_FILES.items() if _normalized_name(style).startswith(prefix)),None) if family=='display' else None
    if family=='display' and display is None:family='serif' if style['serif'] else 'sans'
    if family=='display':
        filename=display;axes={};family_prefix='AstroDisplay-'
    elif family=='slab':
        filename='Bitter-Italic[wght].ttf' if style['italic'] else 'RobotoSlab[wght].ttf'
        axes={'wght':weight}
        family_prefix='AstroSlab-'
    elif family=='serif':
        filename='RobotoSerif-Italic[GRAD,opsz,wdth,wght].ttf' if style['italic'] else 'RobotoSerif[GRAD,opsz,wdth,wght].ttf'
        axes={'GRAD':0,'opsz':max(8,min(144,float(style['size']))),'wdth':float(style['width']),'wght':weight}
        family_prefix='AstroSerif-'
    else:
        filename='NotoSans-Italic[wdth,wght].ttf' if style['italic'] else 'NotoSans[wdth,wght].ttf'
        axes={'wdth':max(62.5,min(100,float(style['width']))),'wght':weight}
        family_prefix='AstroSans-'
    base=ROOT/'fonts'/filename
    if not base.exists():raise Blocked('Official variable style-equivalent fallback is missing')
    weight_calibration=None
    if calibrate_weight and reference_font is not None and 'wght' in axes:
        record=folder/('weight-'+hashlib.sha256((sha256(base)+str(sorted(axes.items()))+_font_digest(reference_font)+'-stem-v1').encode()).hexdigest()[:16]+'.json')
        if record.exists():weight_calibration=json.loads(record.read_text()) or None
        else:
            weight_calibration=_calibrated_weight(base,axes,reference_font,weight)
            atomic_json(record,weight_calibration or {})
        if weight_calibration:axes['wght']=weight_calibration['chosen']
    key=hashlib.sha256((sha256(base)+str(sorted(axes.items()))+'-no-gsub-ligatures-unique-v3').encode()).hexdigest()[:16]
    stem={'AstroSlab-':'RobotoSlab','AstroDisplay-':'Display'}.get(family_prefix,'RobotoSerif')
    path=folder/f'{stem}-{key}.ttf'
    if not path.exists():
        # recalcTimestamp=False: the build time in head.modified would change the
        # file hash, hence the calibrated family name embedded in every candidate.
        font=TTFont(base,recalcTimestamp=False);static=instantiateVariableFont(font,axes,inplace=False) if axes else font
        # Story currently emits an incorrect ToUnicode mapping for this font's
        # GSUB fi/fl ligatures. Spanish Latin text needs the original characters,
        # not substitution glyphs; preserve GPOS kerning and all outline styles.
        if 'GSUB' in static:del static['GSUB']
        _rename_static(static,family_prefix+key,style)
        if filename in RESERVED_NAME_BASES:_strip_reserved_names(static)
        static.save(path);static.close();font.close()
    calibration=None
    chosen=fitz.Font(fontfile=str(path))
    if reference_font is not None:
        reference=REFERENCE_CHARACTERS
        common=''.join(c for c in reference if reference_font.has_glyph(ord(c),fallback=False) and chosen.has_glyph(ord(c),fallback=False))
        ratio=reference_font.text_length(common,fontsize=1)/chosen.text_length(common,fontsize=1) if common else 1.
        ratio=max(.45,min(1.25,ratio));ratio=round(ratio,6)
        space=round(reference_font.text_length(' ',fontsize=1),4) if calibrate_space and reference_font.has_glyph(32,fallback=False) and reference_font.text_length(' ',fontsize=1)>0 else None
        calibrated_key=hashlib.sha256((sha256(path)+str(ratio)+'-horizontal-v2'+(f'-space{space}' if space else '')).encode()).hexdigest()[:16]
        calibrated=folder/f'{family_prefix}{calibrated_key}.ttf'
        if not calibrated.exists():
            font=TTFont(path,recalcTimestamp=False);_scale_horizontal(font,ratio)
            if space:_set_space_advance(font,space)
            _rename_static(font,family_prefix+calibrated_key,style);font.save(calibrated);font.close()
        path=calibrated;chosen=fitz.Font(fontfile=str(path))
        calibration={'reference_characters':common,'horizontal_scale':ratio,'source_advance_em':reference_font.text_length(common,fontsize=1),'chosen_advance_em':chosen.text_length(common,fontsize=1)}
        if space:calibration.update(source_space_em=space,chosen_space_em=chosen.text_length(' ',fontsize=1))
    result={'path':str(path),'name':chosen.name,'sha256':sha256(path),'font':chosen,'source':'fallback','axes':axes,'base_sha256':sha256(base),'gsub_disabled_for_pdf_text_integrity':True,'metric_calibration':calibration,'style':style}
    result['base_file']=filename
    if weight_calibration:result['weight_calibration']=weight_calibration
    return result

def _font_digest(font):
    try:return hashlib.sha256(font.buffer).hexdigest()
    except Exception:return font.name

def _set_space_advance(font,space_em):
    """Word space (and no-break space) advance = source space, in em."""
    upm=font['head'].unitsPerEm;cmap=font.getBestCmap()
    for codepoint in (0x20,0xA0):
        name=cmap.get(codepoint)
        if name:font['hmtx'][name]=(round(space_em*upm),font['hmtx'][name][1])

def _strip_reserved_names(font):
    """OFL Reserved Font Names: drop typographic/variation names left by the base."""
    for name_id in (18,21,22,25):font['name'].removeNames(nameID=name_id)

def base_file(choice):
    """Base file of a fallback choice (recorded name, else SHA-256 lookup)."""
    if choice.get('base_file'):return choice['base_file']
    return next((name for name in BASE_CLASSES if (ROOT/'fonts'/name).exists() and sha256(ROOT/'fonts'/name)==choice.get('base_sha256')),None)

def verify_fallback(choice,style,text,*,classify_slab=False,families=None):
    """Style-equivalence evidence: coverage with ink, class, italic, width, weight."""
    if choice.get('source')!='fallback':return {'ok':True,'checks':{'source':choice.get('source')},'problems':[]}
    problems=[];checks={}
    checks['coverage']=covers(choice['font'],text) and glyph_ink_coverage(choice,text)
    if not checks['coverage']:problems.append('coverage')
    expected=fallback_class(style,families,known=classify_slab) if classify_slab or families else ('serif' if style['serif'] else 'sans')
    name=base_file(choice);checks['base']=name;checks['class']=BASE_CLASSES.get(name);checks['expected_class']=expected
    if checks['class']!=expected:problems.append('class')
    checks['italic']=bool(name and 'Italic' in name)==bool(style['italic'])
    if not checks['italic']:problems.append('italic')
    calibration=choice.get('metric_calibration') or {}
    if style['width']<100:
        checks['width']=calibration.get('horizontal_scale',1)<1 or choice.get('axes',{}).get('wdth',100)<100
        if not checks['width']:problems.append('width')
    if 'wght' in choice.get('axes',{}):checks['weight']=choice['axes']['wght']
    elif checks['class']!='display':problems.append('weight')
    return {'ok':not problems,'checks':checks,'problems':problems}

def glyph_ink_coverage(choice,text):
    """Subset TrueType cmaps may advertise glyphs whose outlines were removed."""
    from fontTools.ttLib import TTFont, TTLibError
    from fontTools.pens.boundsPen import BoundsPen
    try:
        font=TTFont(choice['path'])
    except TTLibError:
        return True  # Raw CFF coverage is checked by MuPDF and text roundtrip.
    try:
        cmap=font.getBestCmap() or {};glyphs=font.getGlyphSet()
        for c in required_characters(text):
            name=cmap.get(ord(c))
            if name is None:return False
            pen=BoundsPen(glyphs);glyphs[name].draw(pen)
            if pen.bounds is None:return False
        return True
    finally:font.close()


def original_text_roundtrip(choice,text,*,size=8):
    """Coverage alone is insufficient for subset CFF ToUnicode mappings."""
    import html,unicodedata
    norm=lambda value:''.join(c for c in unicodedata.normalize('NFKC',value) if not c.isspace() and c!='\u00ad')
    doc=fitz.open();page=doc.new_page(width=3000,height=3000)
    path=Path(choice['path'])
    css=f'@font-face {{font-family:proof;src:url("{path.name}")}} body,div,p {{font-family:proof;font-size:{size}pt;margin:0;padding:0;line-height:1.05}}'
    try:
        spare,_=page.insert_htmlbox(fitz.Rect(1,1,2999,2999),'<div><p>'+html.escape(text)+'</p></div>',css=css,archive=fitz.Archive(str(path.parent)),scale_low=1)
        return spare>=0 and norm(page.get_text())==norm(text)
    finally:doc.close()

def profile_font_options(page,*,classify_slab=None):
    """fallback_font options declared by the book profile ([fonts]).

    Every route that builds a fallback (blocks, rotated and vertical labels)
    must use them, or e.g. Soho Pro labels fall to Roboto Serif instead of
    the profile's slab class.
    """
    from .profiles import profile_for_page
    fonts=profile_for_page(page).data.get('fonts',{})
    if classify_slab is None:classify_slab=fonts.get('classify_slab',False)
    options={'classify_slab':classify_slab,'families':fonts.get('classification')}
    for flag in ('calibrate_weight','calibrate_space'):
        if fonts.get(flag):options[flag]=True
    return options

def select_group_fonts(doc,page,units,folder,*,classify_slab=None):
    from .profiles import profile_for_page
    profile=profile_for_page(page)
    options=profile_font_options(page,classify_slab=classify_slab)
    if profile.data.get('editorial',{}).get('ordinals'):
        from .native_ordinals import select_heading_fonts
        return select_heading_fonts(_select_group_fonts,doc,page,units,folder,**options)
    return _select_group_fonts(doc,page,units,folder,**options)


def _select_group_fonts(doc,page,units,folder,*,classify_slab=False,families=None,calibrate_weight=False,calibrate_space=False):
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
        ink_failures={key:not glyph_ink_coverage(originals[key],entry['text']) for key,entry in styles.items() if not missing[key]}
        roundtrip_failures={key:not original_text_roundtrip(originals[key],entry['text'],size=entry['style']['size']) for key,entry in styles.items() if not missing[key]}
        use_fallback=any(missing.values()) or any(ink_failures.values()) or any(roundtrip_failures.values()) or any(e['style'].get('force_fallback') for e in styles.values())
        for key,entry in styles.items():
            chosen=fallback_font(entry['style'],Path(folder)/'fallback',originals[key]['font'],classify_slab=classify_slab,families=families,calibrate_weight=calibrate_weight,calibrate_space=calibrate_space) if use_fallback else originals[key]
            if not covers(chosen['font'],entry['text']):raise Blocked('Selected font lacks translated characters')
            mapping[(group,key)]=chosen
            verification={'fallback_verification':verify_fallback(chosen,entry['style'],entry['text'],classify_slab=classify_slab,families=families)} if use_fallback else {}
            audit.append({**verification,'scale_group':group,'original_name':entry['style']['font'],'original_sha256':originals[key]['sha256'],'required_chars':required_characters(entry['text']),'original_missing':missing[key],'original_empty_glyph_outline':ink_failures.get(key,False),'original_text_roundtrip_failed':roundtrip_failures.get(key,False),'group_fallback':use_fallback,'chosen':{k:v for k,v in chosen.items() if k!='font'},'style':entry['style']})
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


INK_HEADROOM_EM=.6

def insert_htmlbox_headroom(page,box,content,css,archive,headroom):
    """insert_htmlbox without clipping accents of the first line.

    MuPDF Story puts the first baseline 0.8 em x line-height below the top of
    its rectangle (whatever the font ascender) and clips all ink to that
    rectangle; accented capitals of the fallbacks rise ~0.9 em, so Ó/Í/É lose
    their marks (p45 «JUGUETÓN»). The rectangle is enlarged by ``headroom``
    above and on both sides and the body padded by the same amounts: content
    box, line breaks, baselines and spare height are unchanged; only the
    clip moves outward. The bottom is not padded (overflow must still fail).
    """
    box=fitz.Rect(box);h=float(headroom)
    if h<=0:return page.insert_htmlbox(box,content,css=css,archive=archive,scale_low=1)
    rect=fitz.Rect(box.x0-h,box.y0-h,box.x1+h,box.y1)
    css=css+f' body {{padding:{h:.3f}pt {h:.3f}pt 0 {h:.3f}pt}}'
    return page.insert_htmlbox(rect,content,css=css,archive=archive,scale_low=1)

def unit_headroom(unit,factor):
    """Headroom (pt) for the largest run of a unit at the given scale."""
    sizes=[r['style']['size'] for r in unit.get('target_runs',[])] or [unit['style']['size']]
    return INK_HEADROOM_EM*max(sizes)*factor

def ink_rect(page,dpi=110):
    """Pixel ink bounds (pt) of a transparent overlay page, with its alpha."""
    import numpy as np
    pix=page.get_pixmap(dpi=dpi,alpha=True,colorspace=fitz.csRGB)
    alpha=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,4)[:,:,3]
    ys,xs=np.where(alpha>80)
    if not ys.size:return None,alpha
    k=72/dpi
    return fitz.Rect(xs.min()*k,ys.min()*k,(xs.max()+1)*k,(ys.max()+1)*k),alpha
