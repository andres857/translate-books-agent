# Piloto: Ruta C y anexo técnico de Mercurio

## Portada raster real, página externa 1

La portada de *Astrology: Using the Wisdom of the Stars in Your Everyday Life* es una imagen de 2303×2752 píxeles sin texto PDF nativo. Es el caso Ruta C real del piloto. Las otras páginas clasificadas como escaneadas por la heurística son principalmente ilustraciones sin texto o aperturas con títulos nativos; no se usó una página nativa rasterizada artificialmente.

Se ejecutó PaddleOCR 3.7.0 / PP-OCRv6 medium en CPU. El primer intento con MKLDNN encontró una incompatibilidad PIR de Paddle; desactivando MKLDNN y usando cuatro hilos terminó en 111,12 segundos. El resultado real contiene un subtítulo de baja confianza, dos etiquetas curvas no detectadas y un falso reconocimiento “10” dentro de una ilustración. Se corrigió localmente la transcripción inglesa del subtítulo cotejando los píxeles; la traducción procede exclusivamente de `llm.run_task`.

Se tradujeron título y subtítulo, con máscaras rectangulares de fondo plano revisadas localmente y validadas por su contorno. El JPEG original y su perfil ICC permanecen idénticos: solo se superpusieron paneles planos en las cajas de texto permitidas y texto vectorial. La sustitución global JPEG→PNG se descartó porque cambiaba el manejo de color y el muestreo en bordes de ilustraciones; no se entrega ese candidato.

**Resultado parcial, `revisar`:** quedan ocho adjetivos decorativos curvos/rotados en inglés: charismatic, practical, caring, progressive, mystical, courageous, focused y optimistic. No son una excepción aprobada como PLANETS en p7. No se borraron con cajas OCR amplias que pudieran tocar ilustraciones. Hace falta detección/máscara por caracteres y composición curva validada. La familia tipográfica de la portada raster no está incrustada y el reemplazo OFL es una aproximación estilística explícita.

Los dos textos editados caben sin fallos, con escala mínima 0,805. Diferencia de píxeles fuera de las cajas editadas a 110 dpi: **0**. La imagen original conserva exactamente sus bytes. Se inspeccionó el render independiente de Poppler: título, Í y subtítulo completos, sin recorte. No se ha certificado un VLM sobre toda la ilustración.

Artefactos:

- [Plan y OCR auditado](../books/astrology-wisdom-stars/work/pilot/raster/cover-plan.json).
- [Resultado OCR real](../books/astrology-wisdom-stars/work/pilot_selection/cover-ocr.json).
- [Página traducida parcial](../books/astrology-wisdom-stars/work/pilot/raster/p0001-translated.pdf).
- [Comparativa PNG](../books/astrology-wisdom-stars/work/pilot/raster/p0001-comparison.png).
- [Informe y consumo](../books/astrology-wisdom-stars/work/pilot/raster/p0001-report.json).

Consumo real: una llamada válida codex_sdk/gpt-6-luna/low, **8.917 tokens de entrada, 90 de salida, 0 cached_input_tokens, 4,074 s**. Hubo además un rechazo de esquema previo con uso no expuesto; no se interpreta como consumo cero. Las recomposiciones posteriores fueron hits de caché local, sin inferencia nueva.

## Tabla de Mercurio: anexo separado

Fuente: *The Only Astrology Book You’ll Ever Need*, Joanna Martine Woolfolk; página externa 501, impresa 486. Es una tabla de ingresos de Mercurio por signo de 1900–1944, no una efeméride diaria de longitudes. El anexo se guarda en su propio directorio, sin incorporarlo al libro de Carole Taylor.

El original se copió a `books/mercury-technical-annex/source.pdf` con permisos 0444 y SHA-256 idéntico `cbae3b1513511999e919ead880c347462fe6932de27b0a4e6a7f90ab59f1ac01`. La autorización registrada se limita al pie editorial de esta página, conforme a la petición expresa del anexo y al glosario/guía aprobados; no autoriza traducir el resto de Woolfolk.

La tabla completa, meses abreviados, abreviaturas de signos, cifras, número de página y viñeta se conservaron. Solo se envió “Mercury Tables” a `run_task`, toda la parte editorial traducible de la página en una llamada. Se reutilizó la fuente original Minion con cobertura comprobada. Para no normalizar coordenadas de la tabla al redactar, se retiró únicamente el literal exacto del pie en el stream, con validación de hash y de ocurrencia única; el resto de operadores se preservó. Los caracteres, posiciones y estilos de la tabla y el prefijo del pie son idénticos. Diferencia fuera de la etiqueta: **0**; escala **1,00**.

Se inspeccionó la comparativa generada por Poppler. Queda pendiente la revisión visual humana del anexo.

- [Anexo traducido](../books/mercury-technical-annex/out/anexo_mercurio.pdf).
- [Comparativa original | traducción](../books/mercury-technical-annex/out/anexo_mercurio_comparativa.pdf).
- [Informe y consumo](../books/mercury-technical-annex/work/pilot/p0501-report.json).

Consumo real: una llamada válida codex_sdk/gpt-6-luna/low, **8.892 tokens de entrada, 50 de salida, 0 cached_input_tokens, 3,469 s**. Las recomposiciones posteriores usaron caché. No se usó API de pago.

## Implementación y comprobación

`src/astro_translate/raster_pilot.py` contiene las operaciones acotadas. `scripts/raster_pilot.py` permite reanudar portada o anexo con sus gates vigentes. Cuatro tests verifican conservación fuera de cajas, rechazo de límites/zonas protegidas/no revisadas, contorno no plano y preservación byte a byte de los operadores ajenos al pie de Mercurio. Los informes registran el consumo real separado de la última invocación desde caché.
