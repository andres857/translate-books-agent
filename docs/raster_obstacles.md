# Protección local de ilustraciones rasterizadas

`astro_translate.raster_obstacles.check_overlay` comprueba la tinta real del ensayo de reinserción contra los objetos gráficos del raster original dentro de la caja del párrafo. Complementa la comprobación de vectores y la diferencia de píxeles fuera de las cajas: esas dos comprobaciones no detectaban texto nuevo sobre iconos dentro de una caja editable.

La imagen original se renderiza localmente, sin texto nativo, mediante MuPDF, conservando gestión de color, transformaciones, recortes y grupos. No se extraen datos hacia un LLM. El color dominante cuantizado y la mediana de su entorno estiman el fondo; una tolerancia de 20 niveles RGB admite ruido JPEG. Los píxeles distintos del fondo se dilatan dos píxeles a 110 dpi para proteger los bordes. Se rechazan fondos sin un color plano dominante (menos de 55 %), imágenes con máscaras, transformaciones no admitidas, imágenes inline no auditadas y transparencia raster incierta. Esto es una detección conservadora, no una interpretación semántica de las figuras.

El compositor debe pasar una página de ensayo que solo contiene la tinta nueva sobre transparencia, o su canal alpha. `check_overlay(source_page, overlay_page, box, ink_alpha=alpha)` devuelve `ok`, motivo, píxeles de solape y métricas de fondo. La evaluación se ejecuta aunque no haya vectores. El cache es local al objeto página original, que debe permanecer inmutable.

`native_ink_alpha` permite auditar el candidato real. Esa auditoría incluye también texto original conservado dentro de la caja: no sustituye el ensayo de composición y no debe eximir automáticamente colisiones existentes. En p32, por ejemplo, la caja de prosa contiene parte de letras circulares originales, por lo que el ensayo de tinta nueva es la prueba decisiva.

## Evidencias del piloto

Los snapshots locales viven en `books/astrology-wisdom-stars/work/pilot/raster_guard_regression/`. Aunque el nombre histórico dice `before-guard`, los de p24 y p35 ya incorporaban las correcciones de sangría y caja hechas por otro agente antes de capturarlos; no se presentan como reproducciones del defecto anterior. Sus pruebas negativas insertan explícitamente tinta sintética sobre los iconos reales y lo indican en el nombre del test.

El snapshot de p25 sí conserva dos colisiones reales adicionales: `u0009`, 76 píxeles sobre un saliente morado junto a «Mercurio», y `u0015`, 195 píxeles en el título del ascendente sobre otro saliente. En ambas regiones el original tenía cero píxeles de solape. Las correcciones del compositor se verifican de nuevo con el mismo guard, sin alterar la imagen original.

`tests/test_raster_obstacles.py`: ocho pruebas, incluidas las regresiones locales del libro; las dos dependientes del libro se omiten explícitamente si el PDF privado no está disponible. En este entorno se ejecutaron las ocho, sin omisiones. También se comprueba que una colisión existente en el original no se convierta en una exención automática. No se ha usado ninguna llamada LLM ni se ha cambiado una fuente u original.
