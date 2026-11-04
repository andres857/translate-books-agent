# Suplemento de etiquetas rotadas del piloto

Se compusieron las etiquetas nativas que el extractor de párrafos había omitido en las páginas externas 63 y 69. El suplemento recibe el candidato nativo existente, escribe otro PDF en `work/pilot/rotated/` y devuelve bloques y firmas de spans para incorporar al QA completo. No altera el original.

- **Página 63:** Urano, Neptuno y Plutón verticales a 90°. Las traducciones proceden directamente del CSV G3 aprobado, sin LLM. La fuente incrustada SohoPro-Condensed no cubre `ó`, por lo que las tres etiquetas usan el mismo reemplazo serif condensado con métricas calibradas. Escala de grupo: **1,00**.
- **Página 69:** cuatro etiquetas de la leyenda sobre una curva suave. Se reconstruyó la línea base a partir de los orígenes reales de los caracteres, con una curva cuadrática y rotación por carácter según su tangente. Se reutilizó Futura original, con cobertura completa. Las barras separadoras originales permanecen intactas. Se amplió únicamente hacia el espacio blanco contiguo dentro de la leyenda; escala común **1,00**.

Las traducciones de p69 se agruparon en una llamada a `run_task("translate_labels", ...)`: codex_sdk/gpt-6-luna/low, **8.514 tokens de entrada, 83 de salida, cached_input_tokens=0, 3,568 s**. La respuesta y el uso están en `work/pilot/rotated/p0069-translation.json`. No se escribieron traducciones manuales.

En ambas páginas, el suplemento confirmó conservación de imágenes y del conjunto de valores protegidos, texto destino realmente extraíble, color de cada carácter y diferencia de píxeles fuera del texto suplementario **0** respecto al candidato recibido. Se inspeccionaron renders independientes de Poppler. Estas verificaciones no sustituyen el QA combinado original→candidato final que ejecuta el orquestador.

Cada reporte `pNNNN-report.json` contiene `verified_rotated_spans` con texto, bbox, dirección y fuente exactos del original, el hash del original, el del candidato final, los ids de destino y si el span se tradujo o es un separador conservado. El QA no debe dar por cubierta una región usando solo un bbox genérico.

Las páginas **32 y 33** conservan incidencias de rótulos sobre círculos: 102 y 99 fragmentos rotados, respectivamente, en `work/pilot/rotated/unresolved_arcs.json`. No se sustituyeron por texto horizontal ni se declararon traducidas esas zonas. El extractor tampoco envió letras horizontales aisladas de esos arcos como si fueran frases.

Implementación: `src/astro_translate/rotated_pilot.py`; CLI `scripts/apply_rotated_pilot.py`. Cinco tests verifican redacción vertical sin tocar números/dibujos próximos, reconstrucción de la curva, rechazo de geometría irregular y orientación real por operador PDF. `rawdict` puede agrupar varias tangentes bajo una sola dirección de línea; el test geométrico usa `get_texttrace` para verificar la transformación efectiva de cada carácter.

Las tres barras de p69 tienen además una prueba por operador real en `preserved_separator_proofs`: origen, bbox, dirección, fuente, tamaño, color, opacidad e identificador de glifo son exactamente iguales al original. Las firmas de span de `rawdict` pueden variar cuando se redibuja el texto vecino, por su agrupación de líneas; esa agrupación no se acepta como prueba suficiente de modificación ni de preservación. El quinto test comprueba conservación tras redactar letras vecinas y rechaza una barra duplicada.
