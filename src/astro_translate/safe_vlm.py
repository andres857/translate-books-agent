"""Conservative native-text-only diagnostic images for the visual QA model.

Raster and vector content is removed from an in-memory PDF copy before rendering.
Protected glyphs are masked in pixels using actual font ink bounds and full
PDF transformation matrices, without altering native text operations. Nearby editorial text can also be hidden;
coverage and excluded layers are explicit.
These images cannot certify artwork, symbol preservation, or original colors.
"""
from __future__ import annotations
import hashlib
import io
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
import pymupdf as fitz
from PIL import Image,ImageDraw
from .core import Blocked,PROTECTED,atomic_json,sha256,read_extras,require_approval
from .llm import run_task,ROOT

MASK_RGB=(192,192,192)
VERSION='native-only-mupdf-glyph-ink-v4'

@dataclass
class SafePair:
    images:tuple[Path,Path]
    manifest:Path
    report:dict


def _raw(page):
    return page.get_text('rawdict',flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)


def _characters(page):
    for block in _raw(page)['blocks']:
        for line in block.get('lines',[]):
            for span in line['spans']:
                for char in span['chars']:yield char,span,line


def _native_signature(page):
    values=[(c['c'],s['font'],round(s['size'],3),tuple(round(v,3) for v in c['bbox'])) for c,s,_ in _characters(page)]
    return hashlib.sha256(json.dumps(sorted(values),ensure_ascii=False).encode()).hexdigest()


def _native_ink_fingerprint(page,dpi):
    """Render original native text operations directly, excluding other layers.

    Borrowed callback pointers are forwarded to MuPDF without ownership wrappers.
    The comparison is internal only; these unmasked pixels are never persisted.
    """
    m=fitz.mupdf
    pix=fitz.Pixmap(fitz.csRGB,(page.rect*fitz.Matrix(dpi/72,dpi/72)).irect,True);pix.clear_with()
    draw=m.fz_new_draw_device(m.FzMatrix(dpi/72,0,0,dpi/72,0,0),pix.this)
    class NativeDevice(m.FzDevice2):
        def __init__(self):super().__init__();self.use_virtual_fill_text();self.use_virtual_stroke_text()
        def fill_text(self,ctx,text,ctm,colorspace,color,alpha,params):
            m.ll_fz_fill_text(draw.m_internal,text,ctm,m.ll_fz_device_gray(),(0,),alpha,params)
        def stroke_text(self,ctx,text,stroke,ctm,colorspace,color,alpha,params):
            m.ll_fz_stroke_text(draw.m_internal,text,stroke,ctm,m.ll_fz_device_gray(),(0,),alpha,params)
    device=NativeDevice()
    try:m.fz_run_page_contents(page.this,device,m.FzMatrix(),m.FzCookie())
    finally:m.fz_close_device(device);m.fz_close_device(draw)
    return hashlib.sha256(pix.samples).hexdigest()


def _verify_native_geometry(source,candidate,dpi):
    if _native_signature(source)==_native_signature(candidate):return {'mode':'exact_signature','max_coordinate_delta_pt':0}
    a=[(c,s) for c,s,_ in _characters(source)];b=[(c,s) for c,s,_ in _characters(candidate)]
    if len(a)!=len(b):raise Blocked('Diagnostic stripping changed native text or glyph geometry')
    delta=0.0
    for (ca,sa),(cb,sb) in zip(a,b):
        if (ca['c'],sa['font'],sa['size'])!=(cb['c'],sb['font'],sb['size']):raise Blocked('Diagnostic stripping changed native text or glyph geometry')
        delta=max(delta,max(abs(x-y) for x,y in zip(ca['bbox'],cb['bbox'])))
    # A round-to-three-decimals boundary can amplify one float32 ULP into a
    # signature mismatch. Accept it only if the complete native ink raster is
    # byte-identical at the exact QA resolution, and movement is <0.0001 pt.
    if delta>0.0001:raise Blocked('Diagnostic stripping changed native text or glyph geometry')
    before=_native_ink_fingerprint(source,dpi);after=_native_ink_fingerprint(candidate,dpi)
    if before!=after:raise Blocked('Diagnostic stripping changed rendered native ink pixels')
    return {'mode':'pixel_identical_float_roundoff','max_coordinate_delta_pt':delta,'native_ink_sha256':before,'dpi':dpi}


def _font_name(name):return re.sub(r'[^a-z0-9]','',re.sub(r'^[A-Z]{6}\+','',name).casefold())

def _glyph_sensitive(char):
    category=unicodedata.category(char)
    return (category.startswith(('S','N')) or category in ('Co','Cn') or char=='\ufffd'
            or (category.startswith('L') and 'LATIN' not in unicodedata.name(char,''))
            or (category=='Cc' and not char.isspace()))


def _visible_keys(page):
    keys=set()
    for span in page.get_texttrace():
        if span.get('type') not in (0,1,2) or span.get('opacity',1)<=0:continue
        for code,_,origin,_ in span['chars']:keys.add((code,round(origin[0],2),round(origin[1],2)))
    return keys


def _protected_rects(page,extras,protected_fonts,trusted_font_hashes):
    visible=_visible_keys(page);records=[];whole_fonts={_font_name(n) for n in protected_fonts};trusted_names=set()
    for font in page.get_fonts(full=True):
        if str(font[2]).lower()=='type3':whole_fonts.add(_font_name(font[3]))
        elif font[0]>0:
            try:font_bytes=page.parent.extract_font(font[0])[3]
            except Exception:font_bytes=b''
            if font_bytes and hashlib.sha256(font_bytes).hexdigest() in trusted_font_hashes:
                trusted_names.add(_font_name(font[3]))
                # Raw extraction may use a name-table alias instead of BaseFont.
                # All aliases come from exactly the SHA-verified embedded bytes.
                try:
                    from fontTools.ttLib import TTFont
                    parsed=TTFont(io.BytesIO(font_bytes))
                    trusted_names.update(_font_name(n.toUnicode()) for n in parsed['name'].names if n.nameID in (1,4,6,16))
                    parsed.close()
                except Exception:pass
    extra='|'.join(re.escape(term) for term in sorted(extras,key=len,reverse=True) if term)
    pattern=re.compile(PROTECTED.pattern+(r'|(?<!\w)(?:'+extra+r')(?!\w)' if extra else ''))
    for block in _raw(page)['blocks']:
        for line in block.get('lines',[]):
            line_records=[];line_text='';line_positions=[]
            for span in line['spans']:
                span_text=''.join(c['c'] for c in span['chars'])
                symbol_name=bool(re.search(r'zodiac|symbol|dingbat|wingding',span['font'],re.I) or ('astro' in span['font'].casefold() and _font_name(span['font']) not in trusted_names))
                whole=bool(symbol_name or _font_name(span['font']) in whole_fonts or any(unicodedata.category(c) in ('Co','Cn') or c=='\ufffd' for c in span_text))
                local_sensitive=set()
                for match in pattern.finditer(span_text):local_sensitive.update(range(match.start(),match.end()))
                # Separate distant text spans, while still detecting a protected
                # token split across adjacent font runs (e.g. M + C).
                if line_records and span['chars']:
                    gap=span['chars'][0]['bbox'][0]-line_records[-1]['bbox'][2]
                    if gap>span['size']*.2:line_text+=' ';line_positions.append(None)
                for index,char in enumerate(span['chars']):
                    origin=char['origin'];record={'bbox':fitz.Rect(char['bbox']),'origin':tuple(origin),'char':char['c'],'visible':(ord(char['c']),round(origin[0],2),round(origin[1],2)) in visible,'protected':whole or index in local_sensitive or _glyph_sensitive(char['c'])}
                    records.append(record);line_records.append(record);line_text+=char['c'];line_positions.append(record)
            for match in pattern.finditer(line_text):
                for item in line_positions[match.start():match.end()]:
                    if item is not None:item['protected']=True
    masks=[record['bbox'] for record in records if record['protected'] and record['visible'] and not record['char'].isspace()]
    unmatched=sum(rect.is_empty or rect.is_infinite for rect in masks)
    unique={tuple(round(v,4) for v in rect):rect for rect in masks}
    return list(unique.values()),records,unmatched,sorted(trusted_names)


def _glyph_ink_bounds(page):
    """Actual MuPDF glyph bounds using original glyph IDs and full PDF matrices.

    This reads the very same font objects used by the renderer. It does not
    approximate ink from advance widths, Unicode cmap or nominal extraction boxes.
    Unsupported strokes/Type3 glyphs require whole-operation masking.
    """
    m=fitz.mupdf
    type3={_font_name(font[3]) for font in page.get_fonts(full=True) if str(font[2]).lower()=='type3'}
    class InkDevice(m.FzDevice2):
        def __init__(self):
            super().__init__();self.bounds={};self.unsupported=set();self.failed=False
            self.use_virtual_fill_text();self.use_virtual_stroke_text()
        def collect(self,text,ctm,alpha,stroke=False):
            if alpha<=0:return
            try:
                span=text.head;ctm=m.FzMatrix(ctm)
                while span:
                    wrapped=m.FzTextSpan(span);font=wrapped.font()
                    unsupported_font=_font_name(fitz.JM_font_name(font)) in type3
                    for index in range(wrapped.m_internal.len):
                        item=wrapped.items(index)
                        origin=m.fz_transform_point(m.fz_make_point(item.x,item.y),ctm)
                        key=(item.ucs,round(origin.x,2),round(origin.y,2))
                        if stroke or unsupported_font or item.gid<0:self.unsupported.add(key);continue
                        matrix=wrapped.trm();matrix.e=item.x;matrix.f=item.y
                        matrix=m.fz_concat(matrix,ctm)
                        bound=m.fz_bound_glyph(font,item.gid,matrix)
                        rect=fitz.Rect(bound.x0,bound.y0,bound.x1,bound.y1)
                        if rect.is_empty or rect.is_infinite or any(not math.isfinite(v) for v in rect):
                            self.unsupported.add(key);continue
                        self.bounds.setdefault(key,[]).append(rect)
                    span=span.next
            except Exception:self.failed=True
        def fill_text(self,ctx,text,ctm,colorspace,color,alpha,params):self.collect(text,ctm,alpha)
        def stroke_text(self,ctx,text,stroke,ctm,colorspace,color,alpha,params):self.collect(text,ctm,alpha,True)
    device=InkDevice();rotation=page.rotation
    try:
        if rotation:page.set_rotation(0)
        m.fz_run_page(page.this,device,m.FzMatrix(),m.FzCookie())
        m.fz_close_device(device)
    except Exception:device.failed=True
    finally:
        if rotation:page.set_rotation(rotation)
    return device.bounds,device.unsupported,device.failed


def _pixel_rect(rect,page,dpi,padding,width,height):
    displayed=fitz.Rect(rect)*page.rotation_matrix
    scale=dpi/72
    return [max(0,math.floor(displayed.x0*scale)-padding),max(0,math.floor(displayed.y0*scale)-padding),min(width,math.ceil(displayed.x1*scale)+padding),min(height,math.ceil(displayed.y1*scale)+padding)]


def _safe_native_image(path,page_number,extras,protected_fonts,trusted_font_hashes,dpi,padding):
    with fitz.open(path) as original:
        if not 1<=page_number<=len(original):raise Blocked('Safe VLM page index is out of bounds')
        source=original[page_number-1];signature=_native_signature(source)
        masks,records,unmatched,trusted_names=_protected_rects(source,extras,protected_fonts,trusted_font_hashes)
        images=source.get_image_info();drawings=source.get_drawings();ignored_annotations=sum(1 for _ in (source.annots() or ()))
        copy=fitz.open();copy.insert_pdf(original,from_page=page_number-1,to_page=page_number-1,links=False,annots=False,widgets=False)
        try:
            page=copy[0];whole=page.rect*page.derotation_matrix
            # Printing marks may surround CropBox and survive a crop-sized
            # redaction. Include every paint bound, not only visible pixels.
            for _,bounds,*_ in page.get_bboxlog():
                if any(not math.isfinite(v) or abs(v)>1_000_000 for v in bounds):raise Blocked('Unbounded PDF paint geometry requires manual review')
                whole|=fitz.Rect(bounds)
            whole+=(-2,-2,2,2)
            page.add_redact_annot(whole,fill=False)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,text=fitz.PDF_REDACT_TEXT_NONE)
            geometry_proof=_verify_native_geometry(source,page,dpi)
            stripped_signature=_native_signature(page)
            remaining=[kind for kind,*_ in page.get_bboxlog() if kind not in ('fill-text','stroke-text','ignore-text')]
            if page.get_image_info() or page.get_drawings() or remaining:raise Blocked('Unclassified paint content remains after diagnostic stripping')
            try:ink,unsupported,ink_failed=_glyph_ink_bounds(page)
            except Exception:ink,unsupported,ink_failed={},set(),True
            text_paints=[fitz.Rect(bounds) for kind,bounds,*_ in page.get_bboxlog() if kind in ('fill-text','stroke-text')]
            verified_masks=[];ink_count=fallback_count=0
            for record in records:
                if not record['protected'] or not record['visible'] or record['char'].isspace():continue
                origin=record['origin'];key=(ord(record['char']),round(origin[0],2),round(origin[1],2))
                if not ink_failed and key in ink and key not in unsupported:
                    verified_masks.extend(ink[key]);ink_count+=1
                else:
                    matched=[bound for bound in text_paints if bound.intersects(record['bbox'])]
                    if not matched:raise Blocked('No verified text ink bound for a protected glyph')
                    verified_masks.extend(matched);fallback_count+=1
            masks=list({tuple(r):r for r in verified_masks}.values())
            if _native_signature(page)!=stripped_signature:raise Blocked('Glyph tracing changed native geometry')
            pix=page.get_pixmap(dpi=dpi,alpha=True,annots=False)
            rgba=Image.frombytes('RGBA',(pix.width,pix.height),pix.samples)
            image=Image.new('RGB',rgba.size,'white');image.paste(Image.new('RGB',rgba.size,'black'),mask=rgba.getchannel('A'))
            boxes=[_pixel_rect(rect,page,dpi,padding,image.width,image.height) for rect in masks]
            boxes=[b for b in boxes if b[0]<b[2] and b[1]<b[3]]
            draw=ImageDraw.Draw(image)
            for box in boxes:draw.rectangle((box[0],box[1],box[2]-1,box[3]-1),fill=MASK_RGB)
            editorial=visible=protected_count=0
            for item in records:
                if not item['visible'] or item['char'].isspace():continue
                if item['protected']:protected_count+=1;continue
                editorial+=1
                bounds=fitz.Rect(item['bbox']);origin=item['origin']
                for bound in ink.get((ord(item['char']),round(origin[0],2),round(origin[1],2)),[]):bounds|=bound
                rect=_pixel_rect(bounds,page,dpi,0,image.width,image.height)
                if not any(fitz.Rect(rect).intersects(fitz.Rect(box)) for box in boxes):visible+=1
            report={'source_pdf_sha256':sha256(path),'page_index':page_number,'rotation':page.rotation,'width_px':image.width,'height_px':image.height,'removed_raster_instances':len(images),'removed_vector_paths':len(drawings),'ignored_annotations':ignored_annotations,'native_text_preserved':True,'native_geometry_preserved_before_protection':True,'native_geometry_proof':geometry_proof,'protected_glyph_operations_removed':False,'mask_strategy':'verified_glyph_ink' if not fallback_count else 'verified_glyph_ink_with_paint_fallback','protected_chars_ink_bound':ink_count,'protected_chars_paint_fallback':fallback_count,'remaining_protected_visible_chars':0,'trusted_embedded_text_fonts':trusted_names,'protected_visible_chars':protected_count,'protected_chars_without_paint_bounds':unmatched,'editorial_chars_total':editorial,'editorial_chars_visible':visible,'editorial_chars_removed_by_protective_redaction':0,'editorial_chars_hidden_by_protective_masks':editorial-visible,'editorial_char_coverage':visible/editorial if editorial else 0,'mask_pixel_rects':boxes,'native_signature':signature,'remaining_non_text_paint_operations':remaining}
            return image,report
        finally:copy.close()


def build_safe_pair(book,source_pdf,candidate_pdf,source_page,candidate_page,*,protected_terms=(),protected_fonts=(),trusted_text_font_files=(),dpi=110,padding_px=4,min_visible_chars=8):
    """Prepare safe artifacts without inference or changing either input PDF."""
    book=Path(book).resolve();source_pdf=Path(source_pdf).resolve();candidate_pdf=Path(candidate_pdf).resolve()
    if not 72<=dpi<=220 or padding_px<2:raise Blocked('Unsafe diagnostic render settings')
    trusted_hashes={sha256(Path(path)) for path in trusted_text_font_files}
    before,br=_safe_native_image(source_pdf,source_page,protected_terms,protected_fonts,trusted_hashes,dpi,padding_px)
    after,ar=_safe_native_image(candidate_pdf,candidate_page,protected_terms,protected_fonts,trusted_hashes,dpi,padding_px)
    if before.size!=after.size:raise Blocked('Source/candidate displayed page geometry differs')
    policy={'version':VERSION,'source':br['source_pdf_sha256'],'candidate':ar['source_pdf_sha256'],'source_page':source_page,'candidate_page':candidate_page,'protected_terms':sorted(protected_terms),'protected_fonts':sorted(protected_fonts),'trusted_text_font_hashes':sorted(trusted_hashes),'dpi':dpi,'padding_px':padding_px,'min_visible_chars':min_visible_chars}
    key=hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()[:20]
    folder=book/'work/qa_safe'/f'p{source_page:04d}'/key;folder.mkdir(parents=True,exist_ok=True)
    paths=(folder/'before.png',folder/'after.png');before.save(paths[0]);after.save(paths[1])
    report={'version':VERSION,'page':source_page,'scope':'native editorial text only; diagnostic layer removal','ready_for_native_judge':all(r['editorial_chars_visible']>=min_visible_chars and r['protected_chars_without_paint_bounds']==0 for r in (br,ar)),'raster_assessed':False,'vectors_assessed':False,'protected_content_assessed':False,'colors_assessed':False,'before':br,'after':ar,'image_paths':[str(p) for p in paths],'image_sha256':[sha256(p) for p in paths],'limitations':['All raster artwork and vector paths were removed before rendering, including full-page backgrounds.','Protected glyph ink bounds come from actual MuPDF font objects, glyph IDs and full transformation matrices; unsupported glyphs use entire text-paint ink bounds. Coverage reports collateral hidden text.','Colors are normalized to black on white; color fidelity and background contrast are not judged.','Image/vector occlusion, symbol preservation and masked text require local checks and human graphics review.']}
    manifest=folder/'manifest.json';atomic_json(manifest,report)
    return SafePair(paths,manifest,report)


def verify_safe_pair(pair):
    report=json.loads(pair.manifest.read_text())
    if report.get('version')!=VERSION:raise Blocked('Unsupported safe VLM policy version')
    if [str(p) for p in pair.images]!=report.get('image_paths'):raise Blocked('Safe VLM artifact paths differ from manifest')
    if [sha256(p) for p in pair.images]!=report.get('image_sha256'):raise Blocked('Safe VLM image hash mismatch')
    if not report.get('ready_for_native_judge'):raise Blocked('Insufficient verified native text for visual judging')
    return report


def judge_safe_pair(book,pair,config_path=None,*,phase='pilot',logging_phase=None):
    """Explicit inference entry point; checks approvals and image hashes first."""
    import tomllib
    from .adapters import require_backend_approval
    report=verify_safe_pair(pair);book=Path(book).resolve();config_path=Path(config_path or ROOT/'config/pipeline.toml').resolve();config=tomllib.loads(config_path.read_text());paths=config['paths']
    def path(value):
        p=Path(value);return p if p.is_absolute() else ROOT/p
    require_approval(book,path(paths['glossary']),path(paths['style_guide']),path(paths['do_not_translate']))
    require_backend_approval(book,config_path,phase)
    result=run_task('qa_judge',{'_book':str(book),'_config':str(config_path),'_page':report['page'],'_phase':logging_phase or phase,'_images':[str(p) for p in pair.images],'_images_verified':True,'scope':'Estas copias contienen solamente texto PDF nativo visible, con colores normalizados. Raster y vectores fueron retirados; zonas grises ocultan contenido protegido y posiblemente texto adyacente. Evalúa únicamente disposición, solapes entre textos visibles, cortes y posible inglés editorial residual. No juzgues imágenes, diagramas, símbolos, cifras ocultas ni color; no infieras nada sobre zonas retiradas.'})
    output={'page':report['page'],'scope':report['scope'],'native_vlm':result.data,'coverage':{'before':report['before']['editorial_char_coverage'],'after':report['after']['editorial_char_coverage']},'raster_assessed':False,'vectors_assessed':False,'protected_content_assessed':False,'colors_assessed':False,'whole_page_qa_complete':False,'requires_other_checks':report['limitations'],'manifest':str(pair.manifest),'backend':result.backend,'model':result.model,'input_tokens':result.input_tokens,'cached_input_tokens':result.cached_input_tokens,'output_tokens':result.output_tokens,'latency_s':result.latency_s,'cached':result.cached}
    atomic_json(pair.manifest.parent/'judge.json',output);return output
