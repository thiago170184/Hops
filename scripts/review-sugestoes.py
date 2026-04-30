#!/usr/bin/env python3
"""Imprime resumo amigavel de data/composicoes.sugestoes.json pra revisao humana."""

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUG = ROOT / "data" / "composicoes.sugestoes.json"
EST = ROOT / "data" / "produtos_estoque.json"

estoque = {p["id"]: p for p in json.loads(EST.read_text(encoding="utf-8"))}
sugestoes = json.loads(SUG.read_text(encoding="utf-8"))


def alvo_label(v):
    if v.get("tipo_alvo") == "produto":
        p = estoque.get(v["alvo"])
        return p["nome"] if p else f"???{v['alvo']}???"
    return f"[{v['alvo']}]"


for evt_id, sugs in sugestoes.items():
    print(f"\n{'=' * 78}\n EVENTO: {evt_id} | {len(sugs)} produtos\n{'=' * 78}")

    by_tipo = defaultdict(list)
    for nome, s in sugs.items():
        if not s.get("controla"):
            by_tipo["NAO_CONTROLA"].append((nome, s))
        else:
            by_tipo[s.get("tipo", "?")].append((nome, s))

    for tipo in ["1:1", "1:N", "1:1/N", "categoria", "NAO_CONTROLA"]:
        items = by_tipo.get(tipo, [])
        if not items:
            continue
        print(f"\n--- {tipo} ({len(items)}) ---")
        items.sort(key=lambda x: x[1].get("score", 0))
        for nome, s in items:
            score = s.get("score", 0)
            mark = "??" if score < 0.7 else "  "
            if tipo == "NAO_CONTROLA":
                obs = s.get("obs", "")
                print(f"  {mark} {nome:50}   {obs}")
            else:
                vinculos_str = " + ".join(
                    f"{v.get('fracao', 1)}x {alvo_label(v)}" for v in s.get("vinculos", [])
                )
                obs = s.get("obs", "")
                print(f"  {mark} [{score:.2f}] {nome:45} = {vinculos_str}")
                if obs and score < 0.7:
                    print(f"           obs: {obs}")

    n_baixo = sum(
        1 for _, s in sugs.items() if s.get("controla") and s.get("score", 1) < 0.7
    )
    print(f"\n  >> {n_baixo} sugestoes com score < 0.7 (revisar marcadas com ??)")
