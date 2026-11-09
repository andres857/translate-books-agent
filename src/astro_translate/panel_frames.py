"""Conservative frame growth inside a measured, flat coloured raster panel.

No geometry is inferred from page numbers or wording. The existing ink guards
still validate every final glyph; this only proposes whitespace below text.
"""
import numpy as np
import pymupdf as fitz


def extend_bottom(page, frame, obstacles, *, em):
    from .raster_obstacles import _raster_state
    if not page.get_images():
        return fitz.Rect(frame)
    dpi=110;scale=dpi/72
    rgba,unsupported=_raster_state(page,dpi)
    frame=fitz.Rect(frame)
    if any(frame.intersects(rect) for rect,_ in unsupported):
        return frame
    x0=max(0,int(frame.x0*scale));x1=min(rgba.shape[1],int(frame.x1*scale))
    y=max(0,min(rgba.shape[0]-1,int((frame.y1-2)*scale)))
    strip=rgba[max(0,y-2):y+1,x0:x1]
    if not strip.size or np.mean(strip[:,:,3]>250)<.99:
        return frame
    colour=np.median(strip[:,:,:3].reshape(-1,3),axis=0)
    if np.min(colour)>240 or np.mean(np.max(abs(strip[:,:,:3].astype(float)-colour),axis=2)<14)<.98:
        return frame
    # End at the first actual colour boundary, reserving one source em of
    # padding. No growth across a puzzle tab or an illustration is inferred.
    stop=min(rgba.shape[0],int(min(page.rect.height,frame.y1+em*6)*scale))
    boundary=y
    for row in range(y+1,stop):
        pixels=rgba[row,x0:x1]
        if np.mean((pixels[:,3]>250)&(np.max(abs(pixels[:,:3].astype(float)-colour),axis=1)<14))<.98:
            break
        boundary=row
    bottom=min(frame.y1+em*6,boundary/scale-em)
    for rect in obstacles:
        rect=fitz.Rect(rect)
        if rect.x0<frame.x1 and rect.x1>frame.x0 and rect.y0>=frame.y1-.01:
            bottom=min(bottom,rect.y0-2)
    if bottom>frame.y1:
        frame.y1=bottom
    return frame


def has_coloured_bottom_boundary(page, frame):
    """Whether a raster panel's flat fill ends at this horizontal frame edge."""
    frame=fitz.Rect(frame)
    if not any(fitz.Rect(item['bbox']).intersects(frame) for item in page.get_image_info()):
        return False
    top=max(0,frame.y1-2);bottom=min(page.rect.height,frame.y1+2)
    if bottom-top<2:return False
    clip=fitz.Rect(frame.x0,top,frame.x1,bottom)
    pix=page.get_pixmap(dpi=144,clip=clip,alpha=False)
    if pix.width<4 or pix.height<4:return False
    pixels=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,pix.n)[...,:3]
    split=max(1,min(pix.height-1,round((frame.y1-top)/(bottom-top)*pix.height)))
    above=pixels[:split].astype(float);below=pixels[split:].astype(float)
    colour=np.median(above.reshape(-1,3),axis=0)
    uniform=np.mean(np.max(abs(above-colour),axis=2)<16)
    below_colour=np.median(below.reshape(-1,3),axis=0)
    return bool(np.max(colour)<238 and uniform>.94 and np.linalg.norm(colour-below_colour)>32)
