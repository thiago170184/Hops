#!/usr/bin/env python3
"""HOPS · Sugestor de composicoes via Anthropic.

Le:
  - data/produtos_estoque.json (catalogo global de produtos de estoque)
  - data/produtos_vendidos_por_evento.json (produtos vendidos por evento)

Pra cada evento, chama a API da Anthropic uma vez passando o contexto inteiro
e pedindo um JSON estruturado com a sugestao de vinculo PDV->estoque.

Saida:
  - data/composicoes.sugestoes.json (proposta da IA, pra usuario revisar)

Schema da sugestao por produto vendido:
  {
    "controla": bool,                # entra no controle de estoque?
    "tipo": "1:1" | "1:N" | "1:1/N" | "categoria",
    "vinculos": [
      {"alvo": <id_estoque|categoria_nome>, "tipo_alvo": "produto"|"categoria", "fracao": float}
    ],
    "score": 0.0..1.0,
    "obs": "explicacao curta (opcional)"
  }

Uso:
  python3 scripts/sugerir-composicoes.py
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ENV_FILE = ROOT / ".env"

PROD_ESTOQUE = DATA / "produtos_estoque.json"
PROD_VENDIDOS = DATA / "produtos_vendidos_por_evento.json"
OUT = DATA / "composicoes.sugestoes.json"

MODEL = "claude-sonnet-4-6"
API_URL = "https://api.anthropic.com/v1/messages"
MAX_TOKENS = 32000


def carregar_env():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        if k.strip() and k.strip() not in os.environ:
            os.environ[k.strip()] = v


def montar_prompt(produtos_estoque, evento_nome, produtos_vendidos):
    estoque_compacto = [
        {"id": p["id"], "categoria": p["categoria"], "nome": p["nome"]}
        for p in produtos_estoque
    ]
    vendidos_compacto = [
        {"nome": p["nome"], "categorias_origem": p["categorias_origem"]}
        for p in produtos_vendidos
    ]
    categorias_estoque = sorted({p["categoria"] for p in produtos_estoque})

    system = """Voce e um assistente especialista em mapear SKUs de PDV (sistemas Zig/MEEP) para produtos de estoque (comprados direto na industria) em eventos de A&B.

REGRAS DE NEGOCIO:

1. Pra cada produto vendido no PDV, decida se ele entra no controle de estoque (`controla: true`) ou nao (`controla: false`).
   - Entra: bebidas, comidas que tem composicao clara de estoque
   - NAO entra: ingressos, vagas, brinquedos, servicos, taxas, descontos

2. Se entra, classifique o tipo de relacao:
   - `1:1`: 1 venda = 1 unidade de estoque (ex: 1 cerveja Amstel = 1 lata Amstel 350ml)
   - `1:N`: 1 venda = N unidades de varios estoques (combos, baldes)
   - `1:1/N`: 1 venda = fracao de estoque (doses de destilado)
   - `categoria`: cadastro do PDV e generico (ex: "REFRIGERANTE", "CERVEJA"), agrega tudo numa categoria de estoque

3. Padroes de fracao pra doses (1L = unidade base de garrafa):
   - Dose padrao 60ml: fracao = 0.0588 (1/17)
   - Dose 50ml: fracao = 0.0500 (1/20)
   - Combo balde com varias unidades: fracao inteira (ex: 5)
   - Garrafa inteira vendida: fracao = 1

4. Combos comuns:
   - "ABSOLUT & RED BULL" = 1 dose vodka (0.0588 de Absolut 1L) + 1 Red Bull
   - "BALDE 6 HEINEKEN" = 6 unidades de Heineken 350ml
   - "CB VODKA + RED BULL" = 1 garrafa vodka + N red bull (combo de mesa)

5. REGRA DA CATEGORIA: se o nome do produto vendido for SO categoria generica (ex: "REFRIGERANTE", "AGUA", "CERVEJA" sem marca), use tipo "categoria" com `alvo` igual ao nome da categoria de estoque (ex: "REFRIGERANTE"). Quando isso acontecer pra um produto, marque tambem produtos da MESMA categoria (mesmo com marca correta) como tipo "categoria" no MESMO evento, ja que perdemos granularidade.

6. Se o produto vendido nao bate com nenhum produto de estoque do catalogo (ex: "CAIPIRINHA", "AGUA DE COCO"), e voce nao consegue propor um vinculo razoavel, retorne `controla: false` e obs explicando.

7. Score de confianca:
   - 0.9-1.0: match direto obvio (mesmo nome ou abreviacao clara)
   - 0.6-0.9: match razoavel mas com ambiguidade
   - <0.6: chute, precisa revisao manual

FORMATO DE SAIDA: APENAS JSON valido, sem markdown, sem texto antes/depois. Schema:
{
  "<NOME EXATO DO PRODUTO VENDIDO>": {
    "controla": true|false,
    "tipo": "1:1"|"1:N"|"1:1/N"|"categoria",
    "vinculos": [
      {"alvo": "<id_estoque|nome_categoria>", "tipo_alvo": "produto"|"categoria", "fracao": <float>}
    ],
    "score": <float>,
    "obs": "<opcional, curto>"
  },
  ...
}

Se controla=false, vinculos pode ser [] e tipo pode ser "1:1" (ignorado)."""

    user = f"""CATALOGO DE PRODUTOS DE ESTOQUE (use os ids desta lista pra `alvo`):
```json
{json.dumps(estoque_compacto, ensure_ascii=False, indent=2)}
```

CATEGORIAS DE ESTOQUE (use estes nomes pra `alvo` quando tipo=categoria):
{json.dumps(categorias_estoque, ensure_ascii=False)}

EVENTO: {evento_nome}

PRODUTOS VENDIDOS NESTE EVENTO ({len(vendidos_compacto)} unicos):
```json
{json.dumps(vendidos_compacto, ensure_ascii=False, indent=2)}
```

Retorne o JSON com a sugestao pra TODOS os {len(vendidos_compacto)} produtos vendidos."""

    return system, user


def chamar_anthropic(system, user, api_key):
    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} ao chamar Anthropic: {err_body}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Erro de rede: {e}")

    data = json.loads(body)
    if data.get("type") == "error":
        raise SystemExit(f"Erro Anthropic: {data}")

    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    usage = data.get("usage", {})
    stop = data.get("stop_reason")
    if stop == "max_tokens":
        print(f"  !! resposta truncada (stop_reason=max_tokens). Aumente MAX_TOKENS.", flush=True)
    return text, usage, stop


def parse_json_resposta(text):
    s = text.strip()
    if s.startswith("```"):
        # Tira a cerca de abertura (```json ou ```)
        s = s[3:]
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.lstrip("\n")
        # Tira a cerca de fechamento
        if s.endswith("```"):
            s = s[:-3]
    return json.loads(s.strip())


def main():
    carregar_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY nao definida (em ~/Hops/.env ou env)")

    if not PROD_ESTOQUE.exists():
        raise SystemExit(f"Faltando: {PROD_ESTOQUE}")
    if not PROD_VENDIDOS.exists():
        raise SystemExit(f"Faltando: {PROD_VENDIDOS} (rode scripts/extrair-vendidos.py)")

    produtos_estoque = json.loads(PROD_ESTOQUE.read_text(encoding="utf-8"))
    vendidos_por_evento = json.loads(PROD_VENDIDOS.read_text(encoding="utf-8"))

    saida = {}
    total_in, total_out = 0, 0
    t0 = time.time()
    for evt_id, evt in vendidos_por_evento.items():
        nome = evt.get("nome", evt_id)
        produtos_vendidos = evt.get("produtos") or []
        if not produtos_vendidos:
            saida[evt_id] = {}
            continue
        print(f"[{evt_id}] {len(produtos_vendidos)} produtos vendidos -> chamando Anthropic ({MODEL})...", flush=True)
        system, user = montar_prompt(produtos_estoque, nome, produtos_vendidos)
        text, usage, stop = chamar_anthropic(system, user, api_key)
        try:
            sugestoes = parse_json_resposta(text)
        except json.JSONDecodeError as e:
            print(f"  !! JSON invalido na resposta: {e}", flush=True)
            print(f"  Resposta crua salva em data/_raw_{evt_id}.txt", flush=True)
            (DATA / f"_raw_{evt_id}.txt").write_text(text, encoding="utf-8")
            continue
        saida[evt_id] = sugestoes
        in_tk = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        out_tk = usage.get("output_tokens", 0)
        total_in += in_tk
        total_out += out_tk
        controla = sum(1 for s in sugestoes.values() if s.get("controla"))
        print(f"  OK {len(sugestoes)} sugestoes ({controla} controladas) | tokens: in={in_tk} out={out_tk}", flush=True)

    OUT.write_text(json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8")
    dt = time.time() - t0
    print(f"\nOK -> {OUT}")
    print(f"Tempo total: {dt:.1f}s | tokens: in={total_in} out={total_out}")
    custo_in = total_in * 3.0 / 1_000_000
    custo_out = total_out * 15.0 / 1_000_000
    print(f"Custo estimado (Sonnet 4.6): ~USD {custo_in + custo_out:.4f}")


if __name__ == "__main__":
    main()
