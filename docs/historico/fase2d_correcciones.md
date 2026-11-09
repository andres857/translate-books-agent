# Fase 2D — aperturas nativas, regresión hermética y créditos

Implementación offline del encargo de 2026-11-07. No se ejecutó el comando
`produce`, ni inferencia LLM/VLM, ni commits. El lote concurrente, sus archivos
`work/production/` y `work/manifest.json` no se modificaron. Las respuestas
existentes se leyeron una vez para congelar fixtures de pruebas. Los originales,
glosarios, configuración, prompts, schemas y archivos de aprobación permanecen
fuera del alcance de edición.

## Causa y corrección general

En p11 la cifra nativa usa SohoPro-ExtraLight a **190 pt**. Su caja nominal ocupa
aproximadamente y=251,844–441,844 pt, pero la caja de métricas completas que usa
MuPDF para borrar texto ocupa y=192,576–453,636 pt. Esta última engloba varios
caracteres de `CHAPTER`, cuya caja completa ocupa y=235,924–258,177 pt. Las tintas
están separadas; las cajas tipográficas no. Ni una banda estrecha ni la búsqueda
de puntos individuales pueden borrar esas letras sin borrar también la cifra.
No es un fallo del modelo, de ajuste de escala o exclusivo de una página.

`native_redaction.py` mantiene la ruta de rectángulos existente y añade un
fallback para perfiles de composición `patterns`: el filtro de texto nativo de
MuPDF elimina los operadores de los caracteres seleccionados. Primero identifica
cada span por texto, fuente y caja; exige orígenes de caracteres no ambiguos.
El callback considera las matrices de texto, contenido y página, exige un único
carácter por operador y contabiliza cada eliminación. Después compara todos los
caracteres retenidos: valor, fuente, tamaño, color, origen y caja. No reescribe ni
rasteriza las cifras. Identidad ambigua o procedencia alterada bloquean la copia
de trabajo. Las imágenes y los vectores no se seleccionan para borrado.

El resultado de composición registra `redaction_proofs` cuando usa el filtro.
No se añadieron números de página ni nombres de capítulos al compositor.

## Cobertura offline

- **p11:** recomposición completa con traducción guardada, ajuste satisfactorio y
  `local_fidelity_ok=true`; números, estilos, imágenes, vectores y diferencia
  fuera del texto verificados por el QA real.
- **p25, p85, p137 y p167:** aperturas nativas confirmadas; dry-run del mismo
  borrado, sobre copias en memoria, sin inferencia. En las cinco aperturas
  queda solamente la cifra original tras borrar las unidades editoriales;
  identidad de caracteres protegidos, dibujos e imagen fuera del texto iguales
  a 220 dpi. Este dry-run no afirma ajuste de traducciones todavía no probadas.
- **p24, p84, p136 y p166:** sin texto nativo; son las hojas previas, no las
  aperturas que necesitan el fallback.
- **p4:** recomposición guardada con QA local satisfactorio. Excepción exacta
  `Copyright ©`, optativa por libro, página 4 y contexto de créditos. Se prueba
  también que el perfil por defecto, otra página y «Copyright» fuera de la frase
  no reciben la excepción. Decisión recibida registrada en `docs/decisiones.md`.
- **p9:** fixtures congeladas de la respuesta fragmentada y su corrección real.
  Con corrección, QA local satisfactorio; sin ella, sigue fallando con
  `fragmented_display_quote`. Se conserva la prueba del problema histórico sin
  depender del contenido mutable de `work/production/`.

`tests/test_phase2c.py` lee ahora todas sus respuestas desde
`tests/fixtures/phase2c/`, con manifiesto de hashes. Su corrección se copia a un
directorio temporal y se pasa explícitamente al compositor; las máscaras
se generan también en ese temporal. `update_manifest=False` ya no toma ni abre
el lock del libro: solo persiste el QA en el directorio de artefactos indicado.
Las nuevas regresiones de p4/p11 excluyen explícitamente correcciones de producción.

## Archivos cambiados por este encargo

- `src/astro_translate/native_redaction.py`: fallback nativo y pruebas de conservación.
- `src/astro_translate/fidelity.py`: integración, evidencia y QA sin lock cuando no actualiza estado.
- `src/astro_translate/profiles.py`: lectura y validación de excepciones lingüísticas por perfil.
- `books/the-secrets-of-astrology/work/profile.toml`: excepción exacta de créditos.
- `tests/test_phase2c.py`, `tests/test_phase2d.py`, `tests/fixtures/phase2c/`: regresiones herméticas y datos congelados.
- `docs/decisiones.md` y este informe.

## TDD y validación

Primero se ejecutaron las dos regresiones reales: **2 fallidas en 23,34 s**.
La de p11 reprodujo `Blocked: No isolated point for selected source glyph`;
la de p4 falló por residual `Copyright ©`. Tras implementar ambas correcciones,
las mismas pruebas pasaron: **2 pasadas en 23,86 s**.

La prueba ampliada de dibujos se ajustó para comparar todos sus atributos salvo
`seqno`: borrar operadores de texto cambia ese índice de orden, sin alterar el
dibujo. La prueba de píxeles fuera de texto a 220 dpi se mantiene estricta.
Los cinco dry-runs reales pasaron: **5 pasadas en 1,80 s**.
La suite hermética de Fase 2C pasó: **30 pasadas en 35,05 s**.

Ejecuciones completas solicitadas, ambas con código de salida 0:

```text
$ .venv/bin/python -m pytest -q
454 passed in 389.70s (0:06:29)

$ .venv/bin/python -m pytest -q books/the-secrets-of-astrology/work/pilot books/the-secrets-of-astrology/work/backend_smoke
39 passed in 0.67s
```

Logs de esta sesión: `/tmp/phase2d-red.log`, `/tmp/phase2d-pytest.log` y
`/tmp/phase2d-book-tests.log`. `git diff --check` también satisfactorio.

Las comprobaciones locales no constituyen juicio VLM ni aprobación visual final.
Los tests de orquestación de la suite usan libros temporales y backends simulados;
no son ejecuciones de producción del libro real.

Comparativas temporales p4/p11 inspeccionadas. Resumen del QA real:

| Página | Escala mínima | QA local | Diff fuera del texto | Números/inmutables/imágenes/vectores |
|---|---:|---|---:|---|
| 4 | 0,85 | satisfactorio | 0 | conservados |
| 11 | 0,83 | satisfactorio | 0 | conservados |

El filtro nativo de p11 elimina 7 caracteres de `u0000` y verifica los otros
20 caracteres antes de continuar con la reinserción. Las comparativas y los
JSON se generan en temporales de pytest; no sustituyen los candidatos activos.

## Recomposición posterior, no ejecutada

Desde la raíz del repositorio, una vez terminado el escritor concurrente:

```bash
.venv/bin/python -m astro_translate produce --book books/the-secrets-of-astrology --pages 4,11 --resume --recompose
```

Reutiliza las traducciones guardadas y recompone únicamente p4/p11. La ruta de
producción puede invocar su juez VLM; por ello se entrega el comando sin ejecutarlo.
