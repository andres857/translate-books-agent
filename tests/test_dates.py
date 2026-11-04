from collections import Counter
import re
import pytest
from astro_translate.dates import translate_date,looks_like_date

@pytest.mark.parametrize('source,target',[
 ('March 21–April 20','21 de marzo–20 de abril'),
 ('21 March – 20 April','21 de marzo–20 de abril'),
 ('MARCH 21 - APRIL 20','21 de marzo–20 de abril'),
 ('December 22–January 19','22 de diciembre–19 de enero'),
 ('Sept. 23–Oct. 22','23 de septiembre–22 de octubre'),
 ('March 21, 2020','21 de marzo de 2020'),
 ('21 March 2020','21 de marzo de 2020'),
])
def test_dates_are_local_and_digits_unchanged(source,target):
    assert translate_date(source)==target
    assert re.findall(r'\d+',source)==re.findall(r'\d+',target)


def test_non_dates_not_rewritten():
    assert translate_date('The Moon rules March in this example.') is None
    assert translate_date('March') is None
    assert not looks_like_date('The Moon rules March in this example.')


def test_invalid_date_fails_closed():
    assert looks_like_date('March 99–April 20')
    with pytest.raises(ValueError):translate_date('March 99–April 20')

@pytest.mark.parametrize('text',['There may be 12 planets.','In March there are 12 days of study.','This begins in March 12 years later.'])
def test_month_words_in_prose_are_not_date_labels(text):
    assert not looks_like_date(text)
    assert translate_date(text) is None
