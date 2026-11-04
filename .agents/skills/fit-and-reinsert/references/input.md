# Entrada de reinserción

El contrato formal vive en `schemas/blocks.json` en la raíz del repositorio.

```json
{"page":1,"blocks":[{"bbox":[72,72,220,96],"source":"Ascendant","target":"Ascendente","vector":true,"rotate":0}]}
```

Este ejemplo describe datos del script, no autoriza una traducción sin G3. `source` debe coincidir con el texto extraído; el bbox no puede afectar a contenido protegido. La página externa empieza en 1. El script debe registrar la región final real usada para que QA no excluya áreas arbitrarias del diff.
