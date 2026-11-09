---
name: audit-pages
description: Lanza el auditor visual book-auditor sobre un rango de páginas de un libro y resume su veredicto.
argument-hint: <slug> <rango> [nombre-lote]
---

Lanza el subagente `book-auditor` con `run_in_background`, pasándole:
- libro `books/$0`;
- páginas `$1`;
- lote `$2`; si no se da, usa `audit-$1`.

Cuando llegue la notificación, lee `work/audit/<lote>.md` y resume para el usuario:
- el G5;
- los defectos bloqueantes por código;
- los defectos que la QA local no detectó.
