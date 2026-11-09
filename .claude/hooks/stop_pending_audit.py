#!/usr/bin/env python3
"""Stop: impide cerrar el turno si un lote cerrado reciente no tiene informe del auditor posterior a su cierre."""
import json, sys, time
from datetime import datetime
from pathlib import Path
d = json.load(sys.stdin)
if d.get("stop_hook_active"):
    sys.exit(0)
root = Path(d.get("cwd") or ".")
now, missing = time.time(), []
def ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None
for b in root.glob("books/*/work/production/batch-*.json"):
    work = b.parents[1]
    legacy = work / "audit" / "legacy_batches.json"
    try:
        if b.stem in json.loads(legacy.read_text()).get("batches", []):
            continue  # lotes obsoletos documentados (p. ej. luna/Codex)
    except Exception:
        pass
    try:
        batch = json.loads(b.read_text())
    except Exception:
        continue
    if batch.get("status") in ("running", "paused"):
        continue  # solo los lotes cerrados pueden auditarse
    closed = ts(batch.get("closed_at")) or b.stat().st_mtime
    if now - closed > 12 * 3600:
        continue
    audit = work / "audit" / b.name
    if audit.with_name(b.stem + ".inprogress").exists():
        continue  # auditor lanzado; su notificación despierta al orquestador
    if not audit.exists() or audit.stat().st_mtime < closed:
        missing.append(str(b.relative_to(root)))
if missing:
    print(json.dumps({"decision": "block", "reason":
        "Lotes sin auditoría vigente (lanza book-auditor o justifica en docs/decisiones.md): " + ", ".join(sorted(missing))}))
sys.exit(0)
