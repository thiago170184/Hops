#!/usr/bin/env python3
"""
IDH — HOPS · builder de dados
=============================

Lê todos os .xlsx de /Users/thiagomonteiro/Downloads/hops-planilhas/
(baixados manualmente do Drive "Arquivos de Vendas Meep") e injeta os
dados processados em /Users/thiagomonteiro/Hops/index.html:

  - DATA (cardápio unificado)
  - DADOS_POR_DATA (qtds por produto_id por data)
  - OPS_POR_DATA (vendas por operação × produto × data)
  - AMBULANTES_POR_DATA (ranking de terminais ambulantes por data)

Faz deduplicação por PedidoDetalheId (cada linha do xlsx é um item de
pedido único). Se receber uma planilha com linhas já processadas, ignora.

Uso:
    python3 scripts/build-data.py

Futuramente pode ser estendido pra baixar direto do Google Drive via API.
"""

import zipfile, re, json, sys, os
import xml.etree.ElementTree as ET
from collections import defaultdict, Counter
from datetime import date, timedelta
from pathlib import Path

# =============================================================================
# Configuração
# =============================================================================
ROOT = Path(__file__).resolve().parent.parent
PLANILHAS_DIR = Path("/Users/thiagomonteiro/Downloads/hops-planilhas")
HTML_PATH = ROOT / "index.html"

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Normalização de nomes de produtos (alias → canônico)
NORMALIZACOES = {
    "AMSTEL": "CERVEJA AMSTEL",
    "HEINEKEN": "CERVEJA HEINEKEN",
}

# PDV APELIDO → Operação display name (da aba BAR)
MAPA_PDV_OPERACAO = {
    "A1. ATENDENTE.CORP":         "CAMAROTE CORP",
    "BAR CORPORATIVO":            "CAMAROTE CORP",
    "C1.CAIXA.CORP":              "CAMAROTE CORP",
    "A1. ATENDENTENTE INTENSE":   "CAMAROTE INTENSE",
    "BAR INTENSE":                "CAMAROTE INTENSE",
    "C2.2.CAIXA.INTENSE":         "CAMAROTE INTENSE",
    "CERVEJARIA PRAÇA PITBULL":   "CERVEJARIA",
    "CERVEJARIA PRAÇA PITBULL 2": "CERVEJARIA",
    "BEBIDA CAMARÃO":             "CERVEJARIA",
    "GARÇOM FRONT":               "GARÇOM FRONT",
    "B1.BAR.FRONT":               "OPERAÇÃO BAR FRONT",
    "B2.BAR.FRONT":               "OPERAÇÃO BAR FRONT",
    "C1.1.CAIXA.FRONT":           "OPERAÇÃO BAR FRONT",
    "C2.2.CAIXA.FRONT":           "OPERAÇÃO BAR FRONT",
    "C2.3.CAIXA.FRONT":           "OPERAÇÃO BAR FRONT",
    "B3.BAR.PISTA":               "OPERAÇÃO BAR PISTA",
    "CAIXA PISTA":                "OPERAÇÃO BAR PISTA",
    "WHISKERIA":                  "WHISKERIA",
    "WHISKERIA 1":                "WHISKERIA",
    "WHISKERIA 2":                "WHISKERIA",
    "CAIXA MÓVEL WHISKERIA":      "WHISKERIA",
}

# Categorias consideradas BEBIDAS (relatório foca em bebidas)
CATEGORIAS_BEBIDAS = {
    "CERVEJAS", "CERVEJARIA PRAÇA",
    "DRINK", "SOFT", "GARRAFAS",
    "WHISKERIA - DOSES", "WHISKERIA - DRINKS PRONTOS",
    "WHISKERIA - BATIDAS E CAIPIRINHAS", "WHISKERIA - DRINKS COPAO",
    "WHISKERIA - BEBIDAS LATA",
    "COMIDA TROPEIRA - BEBIDAS",
}
# Operações de COMIDA que vendem algumas bebidas mas devem ser excluídas
# do relatório de bebidas (o foco é operações primariamente de bebida).
OPERACOES_EXCLUIDAS = {"COMIDA TROPEIRA", "NOVA ERA"}

# BUFFET PRIME é comida (camarote) — excluído do relatório. Mantemos o PDV
# mapeado em MAPA_PDV_OPERACAO apenas para que a operação apareça quando
# houver bebidas vendidas ali no futuro.


# =============================================================================
# Parser de xlsx
# =============================================================================
def read_sheet(xlsx_path: Path, sheet_name: str):
    """Lê uma aba do xlsx pelo nome. Retorna lista de dicionários com chaves
    = nome da coluna (da primeira linha, o header). A primeira linha (header)
    NÃO é retornada. Tolerante a reordenação de colunas entre planilhas.
    """
    with zipfile.ZipFile(xlsx_path) as z:
        with z.open("xl/workbook.xml") as f:
            wb = ET.parse(f).getroot()
        with z.open("xl/_rels/workbook.xml.rels") as f:
            rels = ET.parse(f).getroot()
        rns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        rid_to_file = {r.get("Id"): r.get("Target") for r in rels.iter(f"{rns}Relationship")}
        name_to_file = {}
        for s in wb.iter(f"{NS}sheet"):
            rid = s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            name_to_file[s.get("name")] = rid_to_file.get(rid)
        sheet_file = name_to_file.get(sheet_name)
        if not sheet_file:
            return []
        if not sheet_file.startswith("xl/"):
            sheet_file = "xl/" + sheet_file.lstrip("/")

        strings = []
        try:
            with z.open("xl/sharedStrings.xml") as f:
                for si in ET.parse(f).getroot().iter(f"{NS}si"):
                    strings.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
        except KeyError:
            pass

        with z.open(sheet_file) as f:
            tree = ET.parse(f)

    def cv(c):
        t = c.get("t")
        if t == "s":
            idx = c.findtext(f"{NS}v")
            return strings[int(idx)] if idx and int(idx) < len(strings) else None
        if t == "inlineStr":
            is_el = c.find(f"{NS}is")
            return "".join(tt.text or "" for tt in is_el.iter(f"{NS}t")) if is_el is not None else None
        return c.findtext(f"{NS}v")

    # Passo 1: ler todas as linhas brutas (letter → valor)
    raw_rows = []
    for row in tree.getroot().iter(f"{NS}row"):
        cells = {}
        for c in row.iter(f"{NS}c"):
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            cells[col] = cv(c)
        if cells:
            raw_rows.append(cells)

    if not raw_rows:
        return []

    # Passo 2: primeira linha = header. Constrói mapa letter → nome.
    header = raw_rows[0]
    letter_to_name = {letter: (name or "").strip() for letter, name in header.items() if name}
    if not letter_to_name:
        return []

    # Passo 3: remapear cada linha por nome de coluna
    out = []
    for raw in raw_rows[1:]:
        out.append({letter_to_name[letter]: val for letter, val in raw.items() if letter in letter_to_name})
    return out


# Colunas esperadas (por nome, tolerante à posição na planilha)
COL_PEDIDO_ID     = "PedidoId"
COL_PEDIDO_DET_ID = "PedidoDetalheId"
COL_DATA_BRASILIA = "DataCriacaoBrasilia"
COL_PDV_APELIDO   = "PDV APELIDO"
COL_CATEGORIA     = "Categoria"
COL_PRODUTO       = "Produto"
COL_QUANTIDADE    = "Quantidade"
COL_VALOR_PRODUTO = "ValorProduto"   # preço unitário do cardápio
COL_EQUIPAMENTO   = "Equipamento"


def normalizar_produto(nome: str) -> str:
    nome = (nome or "").strip().upper()
    return NORMALIZACOES.get(nome, nome)


def categoria_eh_bebida(cat: str) -> bool:
    """Retorna True se a categoria é de bebidas."""
    c = (cat or "").strip().upper()
    return c in {k.upper() for k in CATEGORIAS_BEBIDAS}


# Sessão do evento: 17h do dia X → 08h do dia X+1.
# Chave da sessão = data de INÍCIO (dia X). Retorna None se fora da janela.
SESSOES_VALIDAS = {"2026-04-17", "2026-04-18"}

def sessao_de(datetime_str):
    """Dado 'YYYY-MM-DD HH:MM:SS...', retorna a chave da sessão ou None."""
    if not datetime_str or len(datetime_str) < 13:
        return None
    try:
        d = date.fromisoformat(datetime_str[:10])
        hh = int(datetime_str[11:13])
    except ValueError:
        return None
    if hh >= 17:
        sess = d
    elif hh < 8:
        sess = d - timedelta(days=1)
    else:
        return None  # 08-16h: fora de sessão (gray window)
    key = sess.isoformat()
    return key if key in SESSOES_VALIDAS else None


# =============================================================================
# Processamento
# =============================================================================
def processar(xlsx_files: list[Path]):
    # Set global de IDs processados (dedup)
    ids_vistos: set[str] = set()

    # Estruturas finais
    ops_por_data = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"qtd": 0, "valor": 0, "categoria": "", "unit_hist": []})))
    amb_por_data = defaultdict(lambda: defaultdict(lambda: {"qtd": 0, "valor": 0, "produtos": defaultdict(lambda: {"qtd": 0, "valor": 0})}))
    all_produtos = {}  # (nome_canonico, grupo) → preço (do cardápio, calculado pela média)
    pedidos_por_data = defaultdict(set)  # sessão → set de PedidoId únicos (total)
    pedidos_bar_por_data = defaultdict(set)  # sessão → PedidoIds da aba BAR
    pedidos_amb_por_data = defaultdict(set)  # sessão → PedidoIds da aba AMBULANTE

    total_linhas = 0
    total_dup = 0
    total_nao_bebida = 0

    for xlsx in xlsx_files:
        print(f"📄 Processando: {xlsx.name}")
        for aba, grupo_fixo in [("BAR", None), ("AMBULANTE", "BEBIDAS AMBULANTES")]:
            rows = read_sheet(xlsx, aba)
            if not rows:
                continue
            print(f"   Aba {aba}: {len(rows)} linhas (sem header)")
            for r in rows:
                total_linhas += 1
                pedido_det_id = (r.get(COL_PEDIDO_DET_ID) or "").strip()
                if not pedido_det_id:
                    continue
                if pedido_det_id in ids_vistos:
                    total_dup += 1
                    continue
                ids_vistos.add(pedido_det_id)

                data_iso = sessao_de(r.get(COL_DATA_BRASILIA) or "")
                if not data_iso:
                    continue

                pedido_id = (r.get(COL_PEDIDO_ID) or "").strip()
                pdv = (r.get(COL_PDV_APELIDO) or "").strip()
                cat = (r.get(COL_CATEGORIA) or "").strip()
                produto = normalizar_produto(r.get(COL_PRODUTO))
                try: qtd = float(r.get(COL_QUANTIDADE) or 0)
                except: qtd = 0
                # ValorProduto = preço unitário do cardápio. O total da linha
                # (qtd × unit) é calculado aqui pois a coluna "ValorPedido"
                # vem como #VALUE! na aba AMBULANTE.
                try: unit = float(r.get(COL_VALOR_PRODUTO) or 0)
                except: unit = 0
                terminal = (r.get(COL_EQUIPAMENTO) or "").strip()

                if qtd <= 0 or not produto:
                    continue
                if not categoria_eh_bebida(cat):
                    total_nao_bebida += 1
                    continue

                valor = round(qtd * unit, 2)  # total real da linha

                if aba == "BAR":
                    operacao = MAPA_PDV_OPERACAO.get(pdv, pdv)
                    grupo = "BEBIDAS"
                else:  # AMBULANTE
                    operacao = "AMBULANTES"
                    grupo = "BEBIDAS AMBULANTES"

                if operacao in OPERACOES_EXCLUIDAS:
                    continue

                bucket = ops_por_data[data_iso][operacao][produto]
                bucket["qtd"] += qtd
                bucket["valor"] += valor
                bucket["categoria"] = cat
                # Histórico de preços unitários (para calcular preço "cheio" do cardápio)
                bucket["unit_hist"].append((qtd, round(unit, 2)))

                if pedido_id:
                    pedidos_por_data[data_iso].add(pedido_id)
                    if aba == "BAR":
                        pedidos_bar_por_data[data_iso].add(pedido_id)
                    else:
                        pedidos_amb_por_data[data_iso].add(pedido_id)

                # Cardápio
                key = (produto, grupo)
                if key not in all_produtos:
                    all_produtos[key] = {"categoria": cat, "valor_sum": 0, "qtd_sum": 0, "operacao": operacao}
                all_produtos[key]["valor_sum"] += valor
                all_produtos[key]["qtd_sum"] += qtd

                # Ambulantes: estatísticas por terminal
                if aba == "AMBULANTE" and terminal:
                    at = amb_por_data[data_iso][terminal]
                    at["qtd"] += qtd
                    at["valor"] += valor
                    ap = at["produtos"][produto]
                    ap["qtd"] += qtd
                    ap["valor"] += valor

    print(f"\n📊 Totais processamento:")
    print(f"   Linhas lidas:        {total_linhas}")
    print(f"   Linhas duplicadas:   {total_dup}  (dedup via PedidoDetalheId)")
    print(f"   Ignoradas (não-beb): {total_nao_bebida}")
    print(f"   IDs únicos:          {len(ids_vistos)}")
    print(f"   Sessões:             {sorted(ops_por_data.keys())}")

    # Normaliza estruturas pra JSON
    def calcular_preco_cheio(hist):
        """Preço unitário 'oficial': moda dos units quando qtd=1. Fallback: max."""
        units_qtd1 = [u for q, u in hist if q == 1]
        if units_qtd1:
            return Counter(units_qtd1).most_common(1)[0][0]
        return max(u for _, u in hist)

    ops_out = {}
    for data_iso, por_op in ops_por_data.items():
        ops_out[data_iso] = {}
        for op, prods in por_op.items():
            arr = []
            for prod, d in sorted(prods.items()):
                q = d["qtd"]
                arr.append({
                    "produto": prod,
                    "categoria": d["categoria"],
                    "qtd": int(q) if q == int(q) else q,
                    "valor": round(d["valor"], 2),
                    "preco": calcular_preco_cheio(d["unit_hist"]),
                })
            ops_out[data_iso][op] = arr

    amb_out = {}
    for data_iso, por_term in amb_por_data.items():
        lst = []
        for term, d in por_term.items():
            prods = [
                {
                    "produto": p,
                    "qtd": int(pd["qtd"]) if pd["qtd"] == int(pd["qtd"]) else pd["qtd"],
                    "valor": round(pd["valor"], 2),
                }
                for p, pd in sorted(d["produtos"].items(), key=lambda x: -x[1]["valor"])
            ]
            lst.append({
                "terminal": term,
                "qtd": int(d["qtd"]) if d["qtd"] == int(d["qtd"]) else d["qtd"],
                "valor": round(d["valor"], 2),
                "produtos": prods,
            })
        lst.sort(key=lambda x: -x["valor"])
        amb_out[data_iso] = lst

    # Cardápio: cada (nome, grupo) vira um produto com preço unitário calculado
    data_list = []
    for i, ((nome, grupo), info) in enumerate(sorted(all_produtos.items())):
        preco = round(info["valor_sum"] / info["qtd_sum"], 2) if info["qtd_sum"] > 0 else 0
        data_list.append({
            "id": i,
            "nome": nome,
            "categoria": info["categoria"],
            "operacao": info["operacao"],
            "grupo": grupo,
            "preco": preco,
        })

    # DADOS_POR_DATA: qtds por id por data (agregado de todas as operações)
    dpd = defaultdict(dict)
    id_by_nome_grupo = {(p["nome"], p["grupo"]): p["id"] for p in data_list}
    for data_iso, por_op in ops_por_data.items():
        for op, prods in por_op.items():
            for prod, d in prods.items():
                grupo = "BEBIDAS AMBULANTES" if op == "AMBULANTES" else "BEBIDAS"
                pid = id_by_nome_grupo.get((prod, grupo))
                if pid is None:
                    continue
                q = d["qtd"]
                dpd[data_iso][str(pid)] = dpd[data_iso].get(str(pid), 0) + (int(q) if q == int(q) else q)

    pedidos_out = {d: len(ids) for d, ids in pedidos_por_data.items()}
    pedidos_bar_out = {d: len(ids) for d, ids in pedidos_bar_por_data.items()}
    pedidos_amb_out = {d: len(ids) for d, ids in pedidos_amb_por_data.items()}
    return data_list, dict(dpd), ops_out, amb_out, pedidos_out, pedidos_bar_out, pedidos_amb_out


# =============================================================================
# Injeção no HTML
# =============================================================================
def _sub_const(html, nome, valor):
    """Substitui APENAS a primeira ocorrência de `const NOME = ...;\\n`.
    Usa re.sub com count=1 — não confunde com outras consts grandes."""
    pattern = r"const " + re.escape(nome) + r" = \{.*?\};\n"
    # re.sub com lambda evita interpretar \1, \g etc no replacement
    novo = f"const {nome} = {valor};\n"
    return re.sub(pattern, lambda m: novo, html, count=1, flags=re.DOTALL)


def injetar_no_html(data_list, dpd, ops_out, amb_out, pedidos_out, pedidos_bar_out, pedidos_amb_out):
    html = HTML_PATH.read_text(encoding="utf-8")

    # DATA (lista, começa com [)
    novo = f"const DATA = {json.dumps(data_list, ensure_ascii=False)};"
    html = re.sub(r"const DATA = \[.*?\];", lambda m: novo, html, count=1, flags=re.DOTALL)

    # DADOS_POR_DATA (meta tag)
    html = re.sub(
        r"<meta name=\"dados-por-data\" content='[^']+'",
        lambda m: f"<meta name=\"dados-por-data\" content='{json.dumps(dpd)}'",
        html, count=1,
    )

    html = _sub_const(html, "OPS_POR_DATA", json.dumps(ops_out, ensure_ascii=False))
    html = _sub_const(html, "AMBULANTES_POR_DATA", json.dumps(amb_out, ensure_ascii=False))
    html = _sub_const(html, "PEDIDOS_POR_DATA", json.dumps(pedidos_out))
    html = _sub_const(html, "PEDIDOS_BAR_POR_DATA", json.dumps(pedidos_bar_out))
    html = _sub_const(html, "PEDIDOS_AMB_POR_DATA", json.dumps(pedidos_amb_out))

    HTML_PATH.write_text(html, encoding="utf-8")


# =============================================================================
# Main
# =============================================================================
def main():
    PLANILHAS_DIR.mkdir(parents=True, exist_ok=True)
    xlsx_files = sorted(PLANILHAS_DIR.glob("*.xlsx"))
    if not xlsx_files:
        # Fallback: tenta achar no Downloads
        fallback = Path("/Users/thiagomonteiro/Downloads/Lista_transacao_Cacapava.xlsx")
        if fallback.exists():
            xlsx_files = [fallback]
    if not xlsx_files:
        print("❌ Nenhum xlsx encontrado em", PLANILHAS_DIR)
        sys.exit(1)

    data_list, dpd, ops_out, amb_out, pedidos_out, pedidos_bar_out, pedidos_amb_out = processar(xlsx_files)
    injetar_no_html(data_list, dpd, ops_out, amb_out, pedidos_out, pedidos_bar_out, pedidos_amb_out)
    print(f"   Pedidos únicos:      {sum(pedidos_out.values())} ({pedidos_out})")
    print(f"   Pedidos BAR:         {sum(pedidos_bar_out.values())} ({pedidos_bar_out})")
    print(f"   Pedidos AMB:         {sum(pedidos_amb_out.values())} ({pedidos_amb_out})")
    print(f"\n✅ HTML atualizado com {len(data_list)} produtos, {sum(len(v) for v in ops_out.values())} linhas OPS")


if __name__ == "__main__":
    main()
