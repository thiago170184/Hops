#!/usr/bin/env python3
"""Promove data/composicoes.sugestoes.json -> data/composicoes.json com correcoes:

  1. Aplica regra "categoria contamina": se algum produto vendido na categoria X
     foi mapeado como tipo=categoria, FORCA todos da mesma categoria de estoque
     a virarem tipo=categoria tambem (perdemos granularidade no relatorio).
  2. Patches manuais (override caso a caso) declarados no PATCHES dict.

Uso:
  python3 scripts/promover-sugestoes.py [evento_id]
  Sem arg: promove todos os eventos da sugestoes.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SUG = DATA / "composicoes.sugestoes.json"
COMP = DATA / "composicoes.json"
EST = DATA / "produtos_estoque.json"


# Patches manuais aplicados depois da regra automatica (override por evento+nome).
# Use quando a IA esqueceu algo obvio.
PATCHES = {
    "braganca-paulista-2026": {
        # KETEL ONE & COCA COLA: IA esqueceu o refrigerante
        "KETEL ONE & COCA COLA": {
            "controla": True,
            "tipo": "1:N",
            "vinculos": [
                {"alvo": "ketel-one-1l", "tipo_alvo": "produto", "fracao": 1.0},
                {"alvo": "REFRIGERANTE", "tipo_alvo": "categoria", "fracao": 1.0},
            ],
            "score": 0.85,
            "obs": "patch manual: incluido refrigerante (IA omitiu)",
        },
    },
}


def carregar():
    sug = json.loads(SUG.read_text(encoding="utf-8"))
    estoque = json.loads(EST.read_text(encoding="utf-8"))
    cat_por_id = {p["id"]: p["categoria"] for p in estoque}
    return sug, estoque, cat_por_id


def aplicar_regra_categoria(sugs_evento, cat_por_id):
    """Pra cada categoria de estoque, se houver pelo menos 1 produto controlado
    com tipo=categoria, forca TODOS produtos daquela categoria a virarem tipo=categoria."""
    categorias_contaminadas = set()
    for nome, s in sugs_evento.items():
        if not s.get("controla"):
            continue
        if s.get("tipo") != "categoria":
            continue
        for v in s.get("vinculos", []):
            if v.get("tipo_alvo") == "categoria":
                categorias_contaminadas.add(v["alvo"])

    if not categorias_contaminadas:
        return sugs_evento, []

    afetados = []
    for nome, s in sugs_evento.items():
        if not s.get("controla"):
            continue
        if s.get("tipo") == "categoria":
            continue
        # Pra cada vinculo apontando pra produto cuja categoria foi contaminada,
        # converte produto -> categoria
        cats_atingidas = set()
        for v in s.get("vinculos", []):
            if v.get("tipo_alvo") == "produto":
                cat = cat_por_id.get(v["alvo"])
                if cat and cat in categorias_contaminadas:
                    cats_atingidas.add(cat)
        if not cats_atingidas:
            continue

        # Reescreve vinculos: produto da categoria contaminada vira [categoria]
        novos = []
        for v in s.get("vinculos", []):
            if v.get("tipo_alvo") == "produto":
                cat = cat_por_id.get(v["alvo"])
                if cat in categorias_contaminadas:
                    # Mantem fracao (ex: 4x Red Bull -> 4x [ENERGETICO])
                    novos.append({
                        "alvo": cat,
                        "tipo_alvo": "categoria",
                        "fracao": v.get("fracao", 1),
                    })
                    continue
            novos.append(v)

        # Dedup: se virarem 2 vinculos pra mesma categoria, soma fracao
        agrupados = {}
        for v in novos:
            key = (v["tipo_alvo"], v["alvo"])
            if key in agrupados:
                agrupados[key]["fracao"] += v.get("fracao", 1)
            else:
                agrupados[key] = dict(v)
        novos = list(agrupados.values())

        s["vinculos"] = novos
        s["obs"] = (s.get("obs", "") + " | regra-categoria aplicada: " + ",".join(sorted(cats_atingidas))).strip(" |")
        # Se sobrou so 1 vinculo apontando pra categoria, tipo vira "categoria"
        if len(novos) == 1 and novos[0]["tipo_alvo"] == "categoria":
            s["tipo"] = "categoria"
        afetados.append((nome, sorted(cats_atingidas)))
    return sugs_evento, afetados


def aplicar_patches(sugs_evento, evt_id):
    patches = PATCHES.get(evt_id, {})
    aplicados = []
    for nome, novo in patches.items():
        if nome in sugs_evento:
            sugs_evento[nome] = novo
            aplicados.append(nome)
    return sugs_evento, aplicados


def main():
    sug, estoque, cat_por_id = carregar()
    eventos_alvo = [sys.argv[1]] if len(sys.argv) > 1 else list(sug.keys())

    # Carrega composicoes.json existente (preserva eventos nao alvo)
    if COMP.exists():
        atual = json.loads(COMP.read_text(encoding="utf-8"))
    else:
        atual = {}

    for evt_id in eventos_alvo:
        if evt_id not in sug:
            print(f"!! evento {evt_id} nao esta nas sugestoes")
            continue
        sugs_evento = json.loads(json.dumps(sug[evt_id]))  # deep copy
        sugs_evento, afetados = aplicar_regra_categoria(sugs_evento, cat_por_id)
        sugs_evento, aplicados = aplicar_patches(sugs_evento, evt_id)

        atual[evt_id] = sugs_evento
        print(f"\n[{evt_id}] {len(sugs_evento)} produtos promovidos")
        if afetados:
            print(f"  regra-categoria contaminou {len(afetados)} produtos:")
            for nome, cats in afetados:
                print(f"    - {nome}  ({','.join(cats)})")
        if aplicados:
            print(f"  patches manuais aplicados: {len(aplicados)}")
            for nome in aplicados:
                print(f"    - {nome}")

    COMP.write_text(json.dumps(atual, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nOK -> {COMP}")


if __name__ == "__main__":
    main()
