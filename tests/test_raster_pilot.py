import pytest
from PIL import Image,ImageDraw
from astro_translate.core import Blocked
from astro_translate.raster_pilot import clear_reviewed_flat_rectangles


def region(**changes):
    return {'pixel_bbox':[5,5,35,20],'reviewed_text_only':True,'reviewed_by':'local audit','protected_content':False,**changes}


def test_flat_reviewed_region_preserves_artwork_outside_text():
    im=Image.new('RGB',(50,30),(14,56,96));draw=ImageDraw.Draw(im)
    draw.rectangle((10,9,30,15),fill=(180,180,180));draw.rectangle((40,0,49,29),fill='red')
    clean,audit=clear_reviewed_flat_rectangles(im,[region()])
    assert clean.getpixel((15,12))==(14,56,96)
    assert clean.crop((40,0,50,30)).tobytes()==im.crop((40,0,50,30)).tobytes()
    assert im.getpixel((15,12))==(180,180,180)
    assert audit['outside_text_changed_pixels']==0


def test_nonflat_border_or_protected_or_unreviewed_region_blocks():
    im=Image.new('RGB',(50,30),'navy')
    for change in [{'protected_content':True},{'reviewed_text_only':False},{'reviewed_by':''}]:
        with pytest.raises(Blocked):clear_reviewed_flat_rectangles(im,[region(**change)])
    ImageDraw.Draw(im).line((0,10,49,10),fill='red')
    with pytest.raises(Blocked,match='not flat'):clear_reviewed_flat_rectangles(im,[region()])


def test_raster_region_bounds_reject_negative_and_full_image():
    im=Image.new('RGB',(50,30),'navy')
    for box in [[0,0,50,30],[-1,4,20,10],[20,10,10,15]]:
        with pytest.raises(Blocked,match='bounds'):
            clear_reviewed_flat_rectangles(im,[region(pixel_bbox=box)])


def test_mercury_footer_removal_preserves_every_other_content_byte():
    from astro_translate.raster_pilot import remove_mercury_footer_literal
    literal=b'[(486)-630.3(\\201 Mer)6.6(cur)36.6(y )250.9(T)59.4(ables)] TJ'
    table=b'6.615 0 0 6.615 69.1981 486.338 Tm\n(NOV 18 SCO) Tj\n'
    data=table+literal+b'\nET\nQ'
    assert remove_mercury_footer_literal(data)==table+b'[(486)-630.3(\\201 )] TJ\nET\nQ'
    for invalid in [table,data+literal]:
        with pytest.raises(Blocked):remove_mercury_footer_literal(invalid)
