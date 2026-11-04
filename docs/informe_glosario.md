# Informe de glosario — propuesta preliminar G3

## Resultado

Se prepararon **118 entradas** EN→ES para *Astrology: Using the Wisdom of the Stars in Your Everyday Life*, de Carole Taylor: **89** tienen coincidencias locales en spans seguros y **29** son propuestas de referencia sin coincidencias detectadas. Hay **14 decisiones** para revisión humana. **G3 pendiente: no se tradujeron páginas ni se registró aprobación.**

Se aplicó `.agents/skills/glossary-build/SKILL.md`. El comando `.venv/bin/python -m astro_translate glossary-build --book books/astrology-wisdom-stars` generó el inventario determinista de las 500 palabras más frecuentes. No había clave API configurada: esta es una curación editorial preliminar de candidatos, no una propuesta validada mediante la API fuerte prevista en el diseño.

`scripts/build_glossary_candidates.py` contiene los pares propuestos y sus categorías. Ejecutarlo con `--book books/astrology-wisdom-stars` reproduce CSV y evidencia de ocurrencias mediante extracción local filtrada. No traduce párrafos, invoca modelos ni escribe aprobaciones. Se emplean reemplazos atómicos y lock del libro. El PDF solo se abrió para lectura y conserva el hash de intake.

## Archivos que debe revisar el humano

- [CSV de equivalencias](../glossary/astro_en_es.csv): cabecera `source,target,tgt_lng`; destino `es`.
- [Lista protegida](../glossary/do_not_translate.txt): todos los tokens del borrador conservados; solo se precisaron comentarios. Complementa detectores de cifras y fuentes simbólicas.
- [Guía de estilo](../glossary/style_guide.md): español neutro latinoamericano, tratamiento de tú, tono fiel, convenciones editoriales y preservación.
- [Dudas y decisiones](../glossary/dudas.md): alternativas concretas para la revisión humana.

La [evidencia de ocurrencias](../books/astrology-wisdom-stars/work/glossary_occurrences.json) incluye por entrada frecuencia, páginas externas, categoría, fundamento y estado sin aprobar; también hashes del PDF, CSV, guía, lista y script. El [inventario original](../books/astrology-wisdom-stars/work/glossary_candidates.json) conserva su método independiente de frecuencia.

## Decisiones propuestas

Exilio para detriment; regente en contexto de regencias; quincuncio; carta natal para birth chart y natal chart; Ascendente, Descendente, Medio Cielo y Fondo del Cielo para puntos. Se proponen también estelio, cuadratura en T y los aspectos menores indicados en el CSV. Todas estas preferencias están pendientes de aprobación.

Chart, ruler, fall, square, opposition, house, cusp, fixed y transit aislados quedan fuera del CSV por ambigüedad. Sus locuciones específicas pueden estar incluidas. Las alternativas astrológicas y sus restricciones están en dudas.md; no se autoriza una sustitución global. La guía distingue términos con mayúscula, concordancia gramatical y preservación literal de todo valor o abreviatura del original.

## Evidencia de frecuencia

Se recorrieron **14.710 spans seguros en 237 de las 258 páginas**. Se buscan palabras o locuciones completas sin distinguir mayúsculas dentro de cada span. No se envió texto del libro a servicios externos ni se guardaron párrafos en este informe.

| Término | Propuesta | Coincidencias locales |
|---|---|---:|
| planets | planetas | 377 |
| Sun | Sol | 236 |
| Moon | Luna | 205 |
| Mars | Marte | 184 |
| Saturn | Saturno | 158 |
| planet | planeta | 157 |
| zodiac | zodíaco | 153 |
| astrology | astrología | 143 |
| Venus | Venus | 135 |
| Jupiter | Júpiter | 122 |
| Uranus | Urano | 103 |
| Mercury | Mercurio | 96 |
| Pluto | Plutón | 96 |
| Neptune | Neptuno | 93 |
| Aries | Aries | 91 |
| Ascendant | Ascendente | 91 |
| birth chart | carta natal | 85 |
| Scorpio | Escorpio | 82 |
| zodiac signs | signos del zodíaco | 80 |
| Gemini | Géminis | 79 |

Las frecuencias de locuciones y palabras incluidas en ellas pueden solaparse: son evidencia de presencia, no número de sustituciones futuras. Pueden diferir del inventario de palabras por el tratamiento de guiones y límites de palabra. La frecuencia no prueba la acepción de cada coincidencia.

Entradas sin coincidencias seguras: `natal planet`, `lunar nodes`, `lunar node`, `celestial equator`, `angular houses`, `succedent houses`, `cadent houses`, `dispositor`, `essential dignity`, `essential dignities`, `mutual reception`, `exaltation`, `planetary aspect`, `planetary aspects`, `applying aspect`, `separating aspect`, `modality`, `modalities`, `triplicity`, `quadruplicity`, `direct motion`, `planetary transit`, `solar return`, `synastry`, `Moon signs`, `ephemerides`, `lunar phase`, `waxing moon`, `waning moon`. Se pueden retirar o conservar como cobertura de referencia; no se afirma que aparezcan en el libro.

## Límites y comprobaciones

No se realizó OCR. No se cubre texto rasterizado, spans enteros descartados por contenido protegido ni locuciones repartidas entre spans o líneas. Las 21 páginas sin spans seguros no equivalen a la clasificación de escaneadas del triaje. Los homónimos siguen requiriendo revisión contextual.

Se comprobó esquema CSV, ausencia de duplicados, destino, exclusión de contenido protegido en los pares, integridad del PDF, hashes editoriales y ausencia de aprobación G3. Son comprobaciones locales de artefactos; no integración API ni QA de páginas traducidas. El pipeline_engineer implementó y probó la selección de la locución coincidente más larga sin solapamientos en traducción y QA: Sun sign usa signo solar sin exigir además Sol en esa misma región. El piloto deberá revisar flexiones, concordancia y resultado editorial.

## Costos y gate pendiente

**0 llamadas API, 0 tokens y 0 USD de API.** `work/costs.jsonl` registra ejecuciones locales con operación `glossary-build`, modelo nulo y estado preliminar. Ese cero corresponde a ausencia de llamadas; los costos de la sesión Codex son desconocidos y están excluidos.

G3 exige revisión humana de los cuatro archivos editoriales y decisión sobre las dudas. No se creó ni modificó `work/glossary_approval.json`. La aprobación humana explícita de estos mismos archivos permite al orquestador registrar directamente sus hashes, sin una segunda confirmación; cualquier cambio posterior los invalida. La aprobación del piloto G4 seguirá siendo necesaria antes de producción.
