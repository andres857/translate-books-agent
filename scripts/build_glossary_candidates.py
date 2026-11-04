#!/usr/bin/env python3
"""Rebuild the preliminary editorial glossary and local occurrence evidence.

No inference, paragraph translation, OCR, or approval. Candidate pairs below are
editorial proposals; frequency is evidence of occurrence, never correctness.
"""
import argparse
import csv
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
from astro_translate.core import atomic_json, book_lock, extract_units, protected, read_extras, sha256

# source|target|category. Ambiguous bare words are deliberately absent.
CANDIDATES = '''Sun|Sol|bodies
Moon|Luna|bodies
Mercury|Mercurio|bodies
Venus|Venus|bodies
Mars|Marte|bodies
Jupiter|Júpiter|bodies
Saturn|Saturno|bodies
Uranus|Urano|bodies
Neptune|Neptuno|bodies
Pluto|Plutón|bodies
Chiron|Quirón|bodies
Aries|Aries|signs
Taurus|Tauro|signs
Gemini|Géminis|signs
Cancer|Cáncer|signs
Leo|Leo|signs
Virgo|Virgo|signs
Libra|Libra|signs
Scorpio|Escorpio|signs
Sagittarius|Sagitario|signs
Capricorn|Capricornio|signs
Aquarius|Acuario|signs
Pisces|Piscis|signs
astrology|astrología|foundations
rising sign|signo ascendente|charts
astrologer|astrólogo|foundations
astrologers|astrólogos|foundations
zodiac|zodíaco|foundations
zodiac sign|signo del zodíaco|foundations
zodiac signs|signos del zodíaco|foundations
horoscope|horóscopo|foundations
birth chart|carta natal|charts
natal chart|carta natal|charts
natal planet|planeta natal|charts
natal planets|planetas natales|charts
planet|planeta|bodies
planets|planetas|bodies
birth time|hora de nacimiento|charts
personal planets|planetas personales|bodies
outer planets|planetas exteriores|bodies
inner planets|planetas interiores|bodies
luminaries|luminarias|bodies
Ascendant|Ascendente|points
Descendant|Descendente|points
Midheaven|Medio Cielo|points
Imum Coeli|Fondo del Cielo|points
North Node|nodo norte|points
South Node|nodo sur|points
lunar nodes|nodos lunares|points
lunar node|nodo lunar|points
ecliptic|eclíptica|points
celestial equator|ecuador celeste|points
house cusp|cúspide de casa|houses
house cusps|cúspides de casas|houses
angular houses|casas angulares|houses
succedent houses|casas sucedentes|houses
cadent houses|casas cadentes|houses
house system|sistema de casas|houses
house systems|sistemas de casas|houses
chart ruler|regente de la carta|rulership
planetary ruler|regente planetario|rulership
ruling planet|planeta regente|rulership
rulership|regencia|rulership
dispositor|dispositor|rulership
essential dignity|dignidad esencial|rulership
essential dignities|dignidades esenciales|rulership
mutual reception|recepción mutua|rulership
detriment|exilio|rulership
exaltation|exaltación|rulership
conjunction|conjunción|aspects
conjunct|en conjunción|aspects
trine|trígono|aspects
sextile|sextil|aspects
quincunx|quincuncio|aspects
semisextile|semisextil|aspects
semisquare|semicuadratura|aspects
sesquiquadrate|sesquicuadratura|aspects
planetary aspect|aspecto planetario|aspects
planetary aspects|aspectos planetarios|aspects
major aspects|aspectos mayores|aspects
minor aspects|aspectos menores|aspects
applying aspect|aspecto aplicativo|aspects
separating aspect|aspecto separativo|aspects
aspect pattern|configuración de aspectos|aspects
aspect patterns|configuraciones de aspectos|aspects
grand trine|gran trígono|aspects
grand cross|gran cruz|aspects
T-square|cuadratura en T|aspects
stellium|estelio|aspects
cardinal signs|signos cardinales|elements
fixed signs|signos fijos|elements
mutable signs|signos mutables|elements
fire signs|signos de fuego|elements
earth signs|signos de tierra|elements
air signs|signos de aire|elements
water signs|signos de agua|elements
modality|modalidad|elements
modalities|modalidades|elements
triplicity|triplicidad|elements
quadruplicity|cuadruplicidad|elements
retrograde motion|movimiento retrógrado|techniques
direct motion|movimiento directo|techniques
planetary transit|tránsito planetario|techniques
planetary transits|tránsitos planetarios|techniques
solar return|revolución solar|techniques
Moon sign|signo lunar|charts
Sun signs|signos solares|charts
Sun sign|signo solar|charts
synastry|sinastría|techniques
Moon signs|signos lunares|charts
ephemeris|efemérides|techniques
ephemerides|efemérides|techniques
lunar phase|fase lunar|techniques
lunar phases|fases lunares|techniques
new moon|Luna nueva|techniques
full moon|Luna llena|techniques
waxing moon|Luna creciente|techniques
waning moon|Luna menguante|techniques'''


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    glossary = root / 'glossary'
    source = args.book / 'source.pdf'
    extras = read_extras(glossary / 'do_not_translate.txt')
    rows = [dict(zip(('source', 'target', 'category'), line.split('|'))) for line in CANDIDATES.splitlines()]
    assert len(rows) == len({row['source'].casefold() for row in rows})
    assert all(not protected(row[key], '', extras) for row in rows for key in ('source', 'target'))
    patterns = [re.compile(r'(?<!\w)' + re.escape(row['source']).replace(r'\ ', r'\s+') + r'(?!\w)', re.I) for row in rows]
    digest = sha256(source)
    counts = [0] * len(rows)
    pages = [set() for _ in rows]
    safe_units = 0
    pages_with_units = set()
    with pymupdf.open(source) as doc:
        total_pages = len(doc)
        for page in doc:
            for unit in extract_units(page, extras):
                safe_units += 1
                pages_with_units.add(page.number + 1)
                for i, pattern in enumerate(patterns):
                    n = len(pattern.findall(unit['source']))
                    counts[i] += n
                    if n:
                        pages[i].add(page.number + 1)
    assert sha256(source) == digest
    inventory = json.loads((args.book / 'work/glossary_candidates.json').read_text())
    assert inventory['source_sha256'] == digest
    inventory_counts = {row['source']: row['count'] for row in inventory['candidates']}
    evidence = []
    for i, row in enumerate(rows):
        evidence.append({**row, 'tgt_lng': 'es', 'safe_span_occurrences': counts[i],
                         'pages': sorted(pages[i]), 'inventory_top500_count': inventory_counts.get(row['source'].casefold()),
                         'basis': 'local_occurrence_and_editorial_proposal' if counts[i] else 'standard_terminology_proposal_not_detected',
                         'approved': False})
    buf = io.StringIO(newline='')
    writer = csv.DictWriter(buf, fieldnames=['source', 'target', 'tgt_lng'], lineterminator='\n')
    writer.writeheader()
    writer.writerows({key: row[key] for key in writer.fieldnames} for row in evidence)
    result = {'source_sha256': digest, 'method': 'local_safe_span_occurrences_and_editorial_curation',
              'approved': False, 'api_calls': 0, 'total_pages': total_pages, 'safe_units': safe_units,
              'pages_with_safe_units': len(pages_with_units), 'terms': evidence,
              'limitations': ['No OCR; image text omitted', 'Entire protected spans excluded',
                              'Within-span matching only; no reconstruction across lines',
                              'Case-insensitive whole-word matching does not resolve semantic ambiguity',
                              'No external API validation; human G3 required']}
    with book_lock(args.book):
        atomic_text(glossary / 'astro_en_es.csv', buf.getvalue())
        result['glossary_sha256'] = sha256(glossary / 'astro_en_es.csv')
        result['style_sha256'] = sha256(glossary / 'style_guide.md')
        result['do_not_translate_sha256'] = sha256(glossary / 'do_not_translate.txt')
        result['candidate_script_sha256'] = sha256(Path(__file__))
        atomic_json(args.book / 'work/glossary_occurrences.json', result)
        cost_path = args.book / 'work/costs.jsonl'
        existing = cost_path.read_text() if cost_path.exists() else ''
        entry = {'operation': 'glossary-build', 'model': None, 'input_tokens': 0, 'output_tokens': 0,
                 'cost_usd': 0, 'api_calls': 0, 'status': 'local_preliminary',
                 'method': result['method'], 'timestamp': datetime.now(timezone.utc).isoformat(),
                 'source_sha256': digest, 'candidate_script_sha256': result['candidate_script_sha256'],
                 'note': 'No external API calls; Codex session costs unavailable and excluded.'}
        if not any(json.loads(line).get('candidate_script_sha256') == entry['candidate_script_sha256']
                   for line in existing.splitlines() if line.strip()):
            atomic_text(cost_path, existing + ('' if not existing or existing.endswith('\n') else '\n') + json.dumps(entry) + '\n')
    print(json.dumps({'terms': len(rows), 'observed_terms': sum(n > 0 for n in counts),
                      'not_detected_terms': sum(n == 0 for n in counts),
                      'safe_units': safe_units, 'pages_with_safe_units': len(pages_with_units),
                      'approved': False, 'api_cost_usd': 0}))


if __name__ == '__main__':
    main()
