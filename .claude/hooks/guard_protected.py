#!/usr/bin/env python3
"""PreToolUse: deniega Edit/Write sobre rutas inmutables o gateadas (AGENTS.md)."""
import json, re, sys
d = json.load(sys.stdin)
p = (d.get("tool_input") or {}).get("file_path") or (d.get("tool_input") or {}).get("notebook_path") or ""
rules = [
    (r"/books/[^/]+/source\.pdf$", "source.pdf es inmutable"),
    (r"/glossary/", "el glosario G3 solo se amplía con un suplemento versionado aprobado"),
    (r"^(?!.*/schemas/).*approval[^/]*\.json$", "los registros de aprobación no se editan a mano"),
    (r"/prompts/[^/]+\.md$", "los prompts de la capa B forman parte de la aprobación G3/G1b"),
]
for pat, why in rules:
    if re.search(pat, p):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
              "permissionDecision": "deny",
              "permissionDecisionReason": f"Ruta protegida ({why}): {p}"}}))
        sys.exit(0)
sys.exit(0)
