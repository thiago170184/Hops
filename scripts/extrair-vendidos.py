#!/usr/bin/env python3
"""Extrai produtos vendidos unicos por evento do index.html.
Gera data/produtos_vendidos_por_evento.json pra alimentar a IA de sugestao."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "index.html"
OUT = ROOT / "data" / "produtos_vendidos_por_evento.json"

html = HTML.read_text(encoding="utf-8")
m = re.search(r"const EVENTOS = (\{.*?\});", html, flags=re.DOTALL)
if not m:
    raise SystemExit("EVENTOS nao encontrado no index.html")

EVENTOS = json.loads(m.group(1))

out = {}
for evt_id, evt in EVENTOS.items():
    produtos = evt.get("data") or []
    vistos = {}
    for p in produtos:
        nome = (p.get("nome") or "").strip().upper()
        cat = (p.get("categoria") or "").strip()
        if not nome:
            continue
        key = nome
        if key not in vistos:
            vistos[key] = {"nome": nome, "categorias_origem": set()}
        if cat:
            vistos[key]["categorias_origem"].add(cat)
    out[evt_id] = {
        "nome": evt.get("nome", evt_id),
        "produtos": sorted(
            [
                {"nome": v["nome"], "categorias_origem": sorted(list(v["categorias_origem"]))}
                for v in vistos.values()
            ],
            key=lambda x: x["nome"],
        ),
    }

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

total = sum(len(e["produtos"]) for e in out.values())
print(f"OK -> {OUT}")
for evt_id, e in out.items():
    print(f"  {evt_id}: {len(e['produtos'])} produtos vendidos")
print(f"  TOTAL: {total}")
