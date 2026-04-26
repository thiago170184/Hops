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
from datetime import date, datetime, timedelta
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
    # Meep
    "AMSTEL": "CERVEJA AMSTEL",
    "HEINEKEN": "CERVEJA HEINEKEN",
    # Zig (Bragança) — variações de nome do mesmo SKU
    "AMESTEL": "CERVEJA AMSTEL",       # typo recorrente no cadastro Zig
    "CERV AMSTEL": "CERVEJA AMSTEL",
    "CERVEJA AMS 350": "CERVEJA AMSTEL",
    "CERVEJA HEI 350": "CERVEJA HEINEKEN",
}

# PDV APELIDO → Operação display name (Caçapava 2026)
# Cada evento define seu próprio mapa em EVENTOS_CONFIG (ver abaixo).
MAPA_PDV_OPERACAO_CACAPAVA = {
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
    # Alimentação: consolida 2 PDVs do Espeto Secretário numa única operação
    "ESPETO SECRETARIO CAIXA":    "ESPETO SECRETARIO",
    "ESPETO SECRETARIO GARCOM":   "ESPETO SECRETARIO",
}

# Categorias consideradas BEBIDAS (relatório foca em bebidas).
# Inclui variantes de nomenclatura entre eventos (Caçapava usa SOFT/DRINK,
# Bragança usa SOFTS/DRINKS) e categorias específicas por ponto de venda
# (ex.: NOVA ERA BEBIDAS, BEBIDAS PIT BUL, MOCHILEIRO de Bragança).
CATEGORIAS_BEBIDAS = {
    # Caçapava 2026
    "CERVEJAS", "CERVEJARIA PRAÇA",
    "DRINK", "SOFT", "GARRAFAS",
    "WHISKERIA - DOSES", "WHISKERIA - DRINKS PRONTOS",
    "WHISKERIA - BATIDAS E CAIPIRINHAS", "WHISKERIA - DRINKS COPAO",
    "WHISKERIA - BEBIDAS LATA",
    "COMIDA TROPEIRA - BEBIDAS",
    # Bragança Paulista 2026 (Meep e Zig — Zig usa singular "BEBIDA")
    "DRINKS", "SOFTS", "BEBIDAS", "BEBIDA",
    "MOCHILEIRO",                 # ambulantes (CAIXA.AMB.*)
    "BEBIDAS PIT BUL",            # P.A. CERVEJA PITBULL
    "NOVA ERA BEBIDAS",           # P.A. LANCHONETE/PASTEL NOVA ERA
    "BEBIDAS DEZINHO",            # P.A. PASTEL DEZINHO
    "BEBIDAS CAFETERIA",          # P.A. CAFETERIA JURA
}
# Operações de ALIMENTAÇÃO. São excluídas do relatório principal de bebidas
# (OPS_POR_DATA, DATA, Vendas, etc.). Se venderem alguma bebida, ela é
# capturada separadamente em ALIMENTACAO_POR_DATA (aba Alimentação — só consulta).
# Operações SEM bebidas aparecem na aba Alimentação como "sem bebidas vendidas".
OPERACOES_ALIMENTACAO_CACAPAVA = {
    # Vendem bebidas misturadas:
    "COMIDA TROPEIRA", "NOVA ERA",
    # Só comida (sem bebidas):
    "DOCE MACIEL", "ESPETINHO JALES", "ESPETO SECRETARIO",
    "HOT DOG JUCA", "KREP SUIÇO", "PASTEL FERNANDO", "PIZZA CONE RAUL",
}

# BUFFET PRIME é comida (camarote) — excluído do relatório. Mantemos o PDV
# mapeado em MAPA_PDV_OPERACAO_CACAPAVA apenas para que a operação apareça quando
# houver bebidas vendidas ali no futuro.

# -----------------------------------------------------------------------------
# Bragança Paulista 2026 — PDVs com nomenclatura nova (prefixos por setor).
# Setores principais derivam do prefixo antes do primeiro ponto.
# Pontos `P.A.`/`A.C`/`A.F` são alimentação (cada um vira sua própria operação
# isolada na aba Alimentação).
# -----------------------------------------------------------------------------
def mapa_pdv_braganca(pdv: str) -> str:
    pu = (pdv or "").upper()
    if pu.startswith("FRONT."):       return "FRONT"
    if pu.startswith("INTENSE."):     return "INTENSE"
    if pu.startswith("CORPORATIVO."): return "CORPORATIVO"
    if pu.startswith("CAIXA.AMB"):    return "AMBULANTES"   # Meep
    if pu.startswith("AMBULANTES."):  return "AMBULANTES"   # Zig
    return pdv  # alimentação e outros: mantém nome do PDV como operação

def eh_alimentacao_braganca(pdv: str) -> bool:
    pu = (pdv or "").upper()
    return (pu.startswith("P.A") or pu.startswith("A.C") or pu.startswith("A.F")  # Meep
            or pu.startswith("ALIMENTACAO."))                                       # Zig

def eh_ambulante_braganca(pdv: str) -> bool:
    pu = (pdv or "").upper()
    return pu.startswith("CAIXA.AMB") or pu.startswith("AMBULANTES.")


# Roteamento SERVIÇOS (Bilheteria/Estacionamento/Parques) — paralelo a Alimentação.
# Aplicado ANTES do filtro de bebidas. Funciona pra MEEP e ZIG (mesmo padrão de PDV).
def classificar_servico(pdv: str, categoria: str, produto: str):
    pu = (pdv or "").upper()
    cu = (categoria or "").upper()
    prodU = (produto or "").upper()
    if pu == "ESTACIONAMENTO" or pu.startswith("ESTACIONAMENTO."):
        return "ESTACIONAMENTO"
    if pu == "PARQUE DIVERSAO" or pu.startswith("PARQUE"):
        return "PARQUES"
    if "INGRESSO" in prodU or "BILHETERIA" in pu or "BILHET" in pu:
        return "BILHETERIA"
    # Categoria Zig "Outros" agrupa PROMOCIONAL/INGRESSO em PDVs operacionais
    if cu == "OUTROS":
        return "BILHETERIA"
    return None


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


# =============================================================================
# Leitor ZIG → formato MEEP
# =============================================================================
# A planilha Zig "Lista de Transações" tem 1 aba só, header em row 14, dados em
# row 15+. Datas vêm como serial Excel (float). Valor é TOTAL da linha (qtd*unit).
# Não há ID único por linha → usa combo (Transação+Produto+Qtd+Valor+Terminal),
# validado como único nas amostras (zero duplicatas em 16k+ linhas).
#
# Devolve dicts com as MESMAS chaves do leitor Meep (PedidoId, PedidoDetalheId,
# DataCriacaoBrasilia, PDV APELIDO, Categoria, Produto, Quantidade, ValorProduto,
# Equipamento) — assim o pipeline de agregação não precisa saber de qual sistema
# veio. A origem fica em `_sistema = "ZIG"`.
ZIG_HEADER_LABELS = {
    "Transação": "tx", "Data Realização": "data", "Operação": "op_tipo",
    "Terminal": "terminal", "Nome Ponto": "pdv", "Categoria Produto": "categoria",
    "Produto": "produto", "Quantidade": "qtd", "Valor": "valor",
    "Status": "status", "Tipo Ponto": "tipo_ponto",
}

def _excel_serial_to_iso(s):
    """Serial Excel (1899-12-30 epoch) → 'YYYY-MM-DD HH:MM:SS'."""
    try:
        f = float(s)
    except (ValueError, TypeError):
        return ""
    dt = datetime(1899, 12, 30) + timedelta(days=f)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _br_decimal(s):
    """'15,00' → 15.00. Tolerante a número já em formato Python e a vazio."""
    if s is None or s == "":
        return 0.0
    s = str(s).strip().replace(".", "").replace(",", ".") if "," in str(s) else str(s)
    try: return float(s)
    except ValueError: return 0.0


def read_xlsx_zig(xlsx_path: Path):
    """Lê XLSX da Zig e devolve dicts no FORMATO MEEP. Filtra:
    - Status != 'Efetivada' (defensivo; hoje todos vêm Efetivada)
    Não filtra Tipo Ponto: Produção também é venda legítima (vimos R$ 8k+ em
    Bragança 25/04 nessas linhas).
    """
    with zipfile.ZipFile(xlsx_path) as z:
        strings = []
        try:
            with z.open("xl/sharedStrings.xml") as f:
                for si in ET.parse(f).getroot().iter(f"{NS}si"):
                    strings.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
        except KeyError:
            pass
        # Zig tem 1 aba só → procura sheet1.xml direto
        sheet_files = [n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d*\.xml$", n)]
        if not sheet_files:
            return []
        with z.open(sheet_files[0]) as f:
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

    # Lê todas as linhas
    raw_rows = []
    for row in tree.getroot().iter(f"{NS}row"):
        cells = {}
        for c in row.iter(f"{NS}c"):
            ref = c.get("r")
            if not ref: continue
            col = re.match(r"[A-Z]+", ref).group(0)
            cells[col] = cv(c)
        raw_rows.append(cells)

    # Encontra header (row com "Transação" e "Data Realização")
    header_idx = None
    header_cells = None
    for i, cells in enumerate(raw_rows):
        vals = set((v or "").strip() for v in cells.values() if v)
        if "Transação" in vals and "Data Realização" in vals:
            header_idx = i
            header_cells = cells
            break
    if header_idx is None:
        return []

    letter_to_field = {}
    for letter, name in header_cells.items():
        key = ZIG_HEADER_LABELS.get((name or "").strip())
        if key:
            letter_to_field[letter] = key

    out = []
    for raw in raw_rows[header_idx + 1:]:
        rec = {letter_to_field[l]: v for l, v in raw.items() if l in letter_to_field}
        if not rec.get("tx"):  # linha vazia
            continue
        if (rec.get("status") or "").strip() != "Efetivada":
            continue

        tx = (rec.get("tx") or "").strip()
        data_iso = _excel_serial_to_iso(rec.get("data"))
        pdv = (rec.get("pdv") or "").strip()
        cat = (rec.get("categoria") or "").strip()
        prod = (rec.get("produto") or "").strip()
        terminal = (rec.get("terminal") or "").strip()
        try: qtd = float(str(rec.get("qtd") or "0").replace(",", "."))
        except ValueError: qtd = 0
        valor_total = _br_decimal(rec.get("valor"))
        unit = round(valor_total / qtd, 4) if qtd not in (0, 0.0) else 0.0

        # ID único composto (validado: 0 dups no arquivo de Bragança)
        det_id = f"ZIG-{tx}-{prod}-{qtd}-{valor_total}-{terminal}"

        out.append({
            "PedidoId":             tx,
            "PedidoDetalheId":      det_id,
            "DataCriacaoBrasilia":  data_iso,
            "PDV APELIDO":          pdv,
            "Categoria":            cat,
            "Produto":              prod,
            "Quantidade":           str(qtd),
            "ValorProduto":         str(unit),
            "Equipamento":          terminal,
            "_sistema":             "ZIG",
        })
    return out


def normalizar_produto(nome: str) -> str:
    nome = (nome or "").strip().upper()
    return NORMALIZACOES.get(nome, nome)


def categoria_eh_bebida(cat: str) -> bool:
    """Retorna True se a categoria é de bebidas."""
    c = (cat or "").strip().upper()
    return c in {k.upper() for k in CATEGORIAS_BEBIDAS}


# Sessão do evento: 17h do dia X → 16h59 do dia X+1 (cobre 24h, sem gap).
# Chave da sessão = data de INÍCIO (dia X). Política: NUNCA descartar dados.
#   - hh ≥ 17: sessão = dia atual (noite começou)
#   - hh < 17: sessão = dia anterior (madrugada e tarde do dia seguinte ainda
#              pertencem à sessão da noite anterior)
# O set de sessões válidas é mutável — cada evento define o seu em main().
SESSOES_VALIDAS: set = set()

def sessao_de(datetime_str):
    """Dado 'YYYY-MM-DD HH:MM:SS...', retorna a chave da sessão (sempre, exceto
    se a string estiver malformada). Sem janela cinza: toda transação válida
    é atribuída a alguma sessão."""
    if not datetime_str or len(datetime_str) < 13:
        return None
    try:
        d = date.fromisoformat(datetime_str[:10])
        hh = int(datetime_str[11:13])
    except ValueError:
        return None
    if hh >= 17:
        sess = d
    else:
        sess = d - timedelta(days=1)
    key = sess.isoformat()
    # Set vazio = sem filtro: aceita qualquer sessão presente nos dados.
    # Isso permite ingestão incremental sem editar config a cada export novo.
    if not SESSOES_VALIDAS:
        return key
    return key if key in SESSOES_VALIDAS else None


# =============================================================================
# Eventos
# =============================================================================
# Cada evento tem: id (URL-safe), nome (display), sessões válidas (YYYY-MM-DD)
# e uma pasta com os xlsx. Pastas ficam em PLANILHAS_DIR/<pasta>/.
# Fallback: se a pasta do evento padrão não existir, usa xlsx soltos em
# PLANILHAS_DIR (compat com instalação atual de Caçapava).
EVENTOS_CONFIG: dict[str, dict] = {
    "cacapava-2026": {
        "nome": "Rodeio de Caçapava 2026",
        # `sessoes` opcional: vazio = aceita qualquer sessão presente nos dados.
        # Política do sistema: ingestão é incremental — todo xlsx novo entra,
        # dedup global por PedidoDetalheId descarta duplicatas, e novas datas
        # entram automaticamente sem precisar editar config.
        "sessoes": set(),
        "pasta": "cacapava-2026",
        # 2 abas dedicadas: BAR (operação via mapa) + AMBULANTE (operação fixa).
        # Aliases por aba: GERAL_CACAPAVA.xlsx usa "BAR"/"AMBULANTE";
        # Lista_transacao_Braganca_PARCIAL.xlsx (que também contém Caçapava)
        # usa "CAÇAPAVA BAR"/"caçapava ambulante".
        "abas": [(["BAR", "CAÇAPAVA BAR"], None, "bar"),
                 (["AMBULANTE", "caçapava ambulante"], "BEBIDAS AMBULANTES", "amb")],
        "mapa_pdv": lambda pdv: MAPA_PDV_OPERACAO_CACAPAVA.get(pdv, pdv),
        "eh_alimentacao_op": lambda op, pdv: op in OPERACOES_ALIMENTACAO_CACAPAVA,
        "alimentacao_canon": OPERACOES_ALIMENTACAO_CACAPAVA,  # pré-registra mesmo sem vendas
    },
    "braganca-paulista-2026": {
        "nome": "Rodeio de Bragança Paulista 2026",
        # `sessoes` vazio = auto-descobre nos dados (ver cacapava-2026 acima).
        "sessoes": set(),
        "pasta": "braganca-paulista-2026",
        # Multi-sistema: 25/04 começou MEEP (subpasta meep/), trocou pra ZIG em 26/04
        # (subpasta zig/). Cada subpasta tem seu leitor e abas próprias.
        "subpastas": [
            {"sub": "meep", "sistema": "MEEP", "leitor": "meep",
             "abas": [("BRAGANÇA", None, "auto")]},
            {"sub": "zig",  "sistema": "ZIG",  "leitor": "zig",
             "abas": [(None, None, "auto")]},  # Zig: 1 aba só, lê direto
        ],
        # Fallback (compat): se subpastas não existirem, usa xlsx soltos como Meep
        "abas": [("BRAGANÇA", None, "auto")],
        "mapa_pdv": mapa_pdv_braganca,
        "eh_alimentacao_op": lambda op, pdv: eh_alimentacao_braganca(pdv),
        "eh_ambulante_pdv": eh_ambulante_braganca,  # usado quando aba_tipo="auto"
        "alimentacao_canon": set(),  # descobre dinamicamente da planilha
    },
}

EVENTO_PADRAO = "cacapava-2026"


# =============================================================================
# Processamento
# =============================================================================
def processar(fontes: list, cfg: dict):
    """Processa lista de fontes (cada uma = (xlsx_files, sistema, leitor, abas_spec)).
    Sistema/leitor permitem misturar MEEP e ZIG num mesmo evento.
    cfg = entrada do EVENTOS_CONFIG (mapa_pdv, eh_alimentacao_op, etc)."""
    mapa_pdv = cfg["mapa_pdv"]                              # callable: pdv -> operacao
    eh_alimentacao = cfg["eh_alimentacao_op"]               # callable: (op, pdv) -> bool
    eh_amb_pdv = cfg.get("eh_ambulante_pdv", lambda p: False)  # usado em aba auto
    alimentacao_canon = cfg.get("alimentacao_canon", set())    # ops alimentação pré-registradas

    # Set global de IDs processados (dedup)
    ids_vistos: set[str] = set()

    # Estruturas finais
    ops_por_data = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"qtd": 0, "valor": 0, "categoria": "", "unit_hist": []})))
    amb_por_data = defaultdict(lambda: defaultdict(lambda: {"qtd": 0, "valor": 0, "produtos": defaultdict(lambda: {"qtd": 0, "valor": 0})}))
    all_produtos = {}  # (nome_canonico, grupo) → preço (do cardápio, calculado pela média)
    pedidos_por_data = defaultdict(set)  # sessão → set de PedidoId únicos (total)
    pedidos_bar_por_data = defaultdict(set)  # sessão → PedidoIds da aba BAR
    pedidos_amb_por_data = defaultdict(set)  # sessão → PedidoIds da aba AMBULANTE
    pedidos_alim_por_data = defaultdict(set)  # sessão → PedidoIds de bebidas em PDV de alimentação
    # timeline por hora: sessão → hora_str → {"bar", "amb": valor R$; "bar_qtd", "amb_qtd": unidades}
    vendas_hora = defaultdict(lambda: defaultdict(lambda: {"bar": 0.0, "amb": 0.0, "bar_qtd": 0, "amb_qtd": 0}))
    # vendas por minuto (para calcular janela de pico com precisão): sessão → minuto_abs (0 = 17:00) → valor total
    vendas_min = defaultdict(lambda: defaultdict(float))
    # Ritmo de Vendas: sessão → op → produto → minuto_abs → qtd (pra calc de antes/pico/pós dinâmico)
    vendas_min_op_prod = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float))))
    # Terminais únicos por minuto: sessão → minuto_abs → set(idx_terminal)
    # Terminais enumerados em `terminal_idx` pra reduzir payload do JSON.
    terminal_idx: dict[str, int] = {}
    terminais_por_min = defaultdict(lambda: defaultdict(set))
    # Alimentação (bebidas vendidas em pontos de alimentação): bucket ISOLADO.
    # Esses valores NÃO entram em ops_por_data, all_produtos, vendas_min, etc.
    # São usados APENAS pela aba Alimentação (visual/consulta).
    alimentacao_por_data = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"qtd": 0, "valor": 0.0, "categoria": ""})))
    # Serviços (Bilheteria/Estacionamento/Parques): bucket ISOLADO, paralelo a Alimentação.
    # Inclui produtos que não são bebida nem comida (ingressos, vagas, brinquedos).
    servicos_por_data = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"qtd": 0, "valor": 0.0, "categoria": "", "pdv": ""})))
    # Sistema(s) usados em cada sessão: data_iso → set("MEEP","ZIG"). Vai pro frontend.
    sistemas_por_sessao = defaultdict(set)

    total_linhas = 0
    total_dup = 0
    total_nao_bebida = 0
    total_servicos = 0
    # Timestamp da última transação processada (qualquer linha válida com data)
    ultima_atualizacao = ""

    # Coleta todas as linhas (formato Meep) de todas as fontes, carimbadas com sistema.
    # Leitor "zig" produz dicts já no formato Meep + _sistema=ZIG (read_xlsx_zig).
    # Leitor "meep" usa read_sheet por aba; aba_spec define grupo_fixo e tipo.
    for xlsx_files, sistema, leitor, abas_spec in fontes:
      for xlsx in xlsx_files:
        print(f"📄 [{sistema}] Processando: {xlsx.name}")
        if leitor == "zig":
            zig_rows = read_xlsx_zig(xlsx)
            print(f"   1 aba Zig: {len(zig_rows)} linhas válidas (Efetivada/Operacional)")
            iter_abas = [(zig_rows, None, "auto")]
        else:
            iter_abas = []
            for aba_spec_name, grupo_fixo, aba_tipo in abas_spec:
                nomes = [aba_spec_name] if isinstance(aba_spec_name, str) else list(aba_spec_name)
                rows = []
                aba = None
                for n in nomes:
                    rows = read_sheet(xlsx, n)
                    if rows:
                        aba = n
                        break
                if not rows:
                    continue
                # Carimba sistema MEEP em cada linha (Zig já vem carimbado)
                for r in rows:
                    r["_sistema"] = "MEEP"
                print(f"   Aba {aba} ({aba_tipo}): {len(rows)} linhas (sem header)")
                iter_abas.append((rows, grupo_fixo, aba_tipo))

        for rows, grupo_fixo, aba_tipo in iter_abas:
            for r in rows:
                total_linhas += 1
                pedido_det_id = (r.get(COL_PEDIDO_DET_ID) or "").strip()
                if not pedido_det_id:
                    continue
                if pedido_det_id in ids_vistos:
                    total_dup += 1
                    continue
                ids_vistos.add(pedido_det_id)

                datetime_str = r.get(COL_DATA_BRASILIA) or ""
                data_iso = sessao_de(datetime_str)
                if not data_iso:
                    continue
                # Carimba sistema usado nesta sessão (vai pro frontend)
                sistemas_por_sessao[data_iso].add(r.get("_sistema", "MEEP"))
                hora_str = datetime_str[11:13] if len(datetime_str) >= 13 else None
                # Atualiza timestamp da última transação válida (string compare é seguro
                # porque datetime_str vem em ISO `YYYY-MM-DD HH:MM:SS...`).
                if datetime_str > ultima_atualizacao:
                    ultima_atualizacao = datetime_str

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

                # SERVIÇOS: roteamento ANTES do filtro de bebida (captura ingressos,
                # estacionamento, parques que não são nem bebida nem comida).
                # Cancelamentos (qtd<0) entram normais e zeram naturalmente na soma.
                if qtd != 0 and produto:
                    grupo_servico = classificar_servico(pdv, cat, produto)
                    if grupo_servico:
                        valor_serv = round(qtd * unit, 2)
                        sb = servicos_por_data[data_iso][grupo_servico][produto]
                        sb["qtd"] += qtd
                        sb["valor"] += valor_serv
                        sb["categoria"] = cat
                        sb["pdv"] = pdv
                        total_servicos += 1
                        continue

                if qtd <= 0 or not produto:
                    continue

                valor = round(qtd * unit, 2)  # total real da linha

                # Determina se a linha é BAR ou AMB (efetivo).
                # - aba_tipo "bar"/"amb": vem fixo da aba
                # - aba_tipo "auto": classifica pelo PDV via eh_amb_pdv()
                if aba_tipo == "auto":
                    is_amb = eh_amb_pdv(pdv)
                else:
                    is_amb = (aba_tipo == "amb")

                if is_amb:
                    operacao = "AMBULANTES"
                    grupo = "BEBIDAS AMBULANTES"
                else:
                    operacao = mapa_pdv(pdv)
                    grupo = "BEBIDAS"

                # Escopo da aba Alimentação: SÓ BEBIDAS vendidas em pontos de alimentação.
                # - Categorias de comida: descartadas (comida não entra no relatório).
                # - Bebidas em op de bar: fluxo normal (ops_por_data).
                # - Bebidas em op de alimentação (COMIDA TROPEIRA, NOVA ERA): vão pra
                #   alimentacao_por_data isoladamente.
                if not categoria_eh_bebida(cat):
                    total_nao_bebida += 1
                    continue

                if eh_alimentacao(operacao, pdv):
                    ali = alimentacao_por_data[data_iso][operacao][produto]
                    ali["qtd"] += qtd
                    ali["valor"] += valor
                    ali["categoria"] = cat
                    if pedido_id:
                        pedidos_alim_por_data[data_iso].add(pedido_id)
                    continue

                bucket = ops_por_data[data_iso][operacao][produto]
                bucket["qtd"] += qtd
                bucket["valor"] += valor
                bucket["categoria"] = cat
                # Histórico de preços unitários (para calcular preço "cheio" do cardápio)
                bucket["unit_hist"].append((qtd, round(unit, 2)))

                if pedido_id:
                    pedidos_por_data[data_iso].add(pedido_id)
                    if is_amb:
                        pedidos_amb_por_data[data_iso].add(pedido_id)
                    else:
                        pedidos_bar_por_data[data_iso].add(pedido_id)

                # Timeline por hora (valor R$ e quantidade)
                if hora_str is not None:
                    bucket_h = vendas_hora[data_iso][hora_str]
                    if is_amb:
                        bucket_h["amb"] += valor
                        bucket_h["amb_qtd"] += qtd
                    else:
                        bucket_h["bar"] += valor
                        bucket_h["bar_qtd"] += qtd
                    # Minuto absoluto desde 17:00 da sessão (mm_abs).
                    # Sessão cobre 24h (17h até 16h59 do dia seguinte) → range 0..1439.
                    # hh >= 17 → (hh - 17)*60 + mm    (17h-23h59 → 0..419)
                    # hh < 17  → (hh + 7)*60 + mm     (0h-16h59  → 420..1439)
                    if len(datetime_str) >= 16:
                        try:
                            hh = int(datetime_str[11:13])
                            mm = int(datetime_str[14:16])
                            if hh >= 17:
                                mm_abs = (hh - 17) * 60 + mm
                            else:
                                mm_abs = (hh + 7) * 60 + mm
                            vendas_min[data_iso][mm_abs] += valor
                            # Ritmo de Vendas: qtd por (op × produto × minuto)
                            vendas_min_op_prod[data_iso][operacao][produto][mm_abs] += qtd
                            # Terminais ativos por minuto (enumerados)
                            if terminal:
                                if terminal not in terminal_idx:
                                    terminal_idx[terminal] = len(terminal_idx)
                                terminais_por_min[data_iso][mm_abs].add(terminal_idx[terminal])
                        except ValueError:
                            pass

                # Cardápio
                key = (produto, grupo)
                if key not in all_produtos:
                    all_produtos[key] = {"categoria": cat, "valor_sum": 0, "qtd_sum": 0, "operacao": operacao}
                all_produtos[key]["valor_sum"] += valor
                all_produtos[key]["qtd_sum"] += qtd

                # Ambulantes: estatísticas por terminal
                if is_amb and terminal:
                    at = amb_por_data[data_iso][terminal]
                    at["qtd"] += qtd
                    at["valor"] += valor
                    ap = at["produtos"][produto]
                    ap["qtd"] += qtd
                    ap["valor"] += valor

    print(f"\n📊 Totais processamento:")
    print(f"   Linhas lidas:        {total_linhas}")
    print(f"   Linhas duplicadas:   {total_dup}  (dedup via PedidoDetalheId)")
    print(f"   Serviços (rota):     {total_servicos}  (Bilheteria/Estacionamento/Parques)")
    print(f"   Ignoradas (não-beb): {total_nao_bebida}")
    print(f"   IDs únicos:          {len(ids_vistos)}")
    print(f"   Sessões:             {sorted(ops_por_data.keys())}")
    print(f"   Sistemas/sessão:     " + ", ".join(f"{k}={sorted(v)}" for k,v in sorted(sistemas_por_sessao.items())))

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
    pedidos_alim_out = {d: len(ids) for d, ids in pedidos_alim_por_data.items()}
    # Timeline: arredonda valores
    vendas_hora_out = {
        sess: {
            h: {
                "bar": round(v["bar"], 2),
                "amb": round(v["amb"], 2),
                "bar_qtd": int(v["bar_qtd"]) if v["bar_qtd"] == int(v["bar_qtd"]) else v["bar_qtd"],
                "amb_qtd": int(v["amb_qtd"]) if v["amb_qtd"] == int(v["amb_qtd"]) else v["amb_qtd"],
            } for h, v in horas.items()
        }
        for sess, horas in vendas_hora.items()
    }
    # Por minuto (só minutos com venda > 0): usado pra calcular janela de pico
    vendas_min_out = {
        sess: {str(m): round(v, 2) for m, v in mins.items() if v > 0}
        for sess, mins in vendas_min.items()
    }
    # Ritmo de Vendas: qtd por (sessão × op × produto × minuto) — só minutos com venda
    vendas_min_op_prod_out = {}
    for sess, por_op in vendas_min_op_prod.items():
        vendas_min_op_prod_out[sess] = {}
        for op, por_prod in por_op.items():
            vendas_min_op_prod_out[sess][op] = {}
            for prod, por_min in por_prod.items():
                vendas_min_op_prod_out[sess][op][prod] = {
                    str(m): (int(q) if q == int(q) else round(q, 2))
                    for m, q in por_min.items() if q > 0
                }
    # Terminais por minuto (enumerados): sessão → minuto → [idx_terminal, ...]
    terminais_por_min_out = {
        sess: {str(m): sorted(list(s)) for m, s in mins.items()}
        for sess, mins in terminais_por_min.items()
    }
    # Pré-registra TODAS as operações de alimentação conhecidas em cada sessão ativa,
    # mesmo as que não venderam bebidas — pra aparecerem na aba Alimentação como
    # "sem bebidas vendidas" (visibilidade operacional completa).
    # Para eventos com `alimentacao_canon` vazio (ex.: Bragança), só aparecem as ops
    # efetivamente presentes na planilha.
    sessoes_ativas = set(alimentacao_por_data.keys()) | set(ops_por_data.keys())
    for sess in sessoes_ativas:
        for op in alimentacao_canon:
            if op not in alimentacao_por_data[sess]:
                alimentacao_por_data[sess][op]  # força criação via defaultdict (dict vazio)

    # Alimentação: bebidas vendidas em pontos de alimentação (bucket isolado)
    alimentacao_out = {}
    for data_iso, por_op in alimentacao_por_data.items():
        alimentacao_out[data_iso] = {}
        for op, prods in por_op.items():
            arr = []
            for prod, d in sorted(prods.items()):
                q = d["qtd"]
                arr.append({
                    "produto": prod,
                    "categoria": d["categoria"],
                    "qtd": int(q) if q == int(q) else q,
                    "valor": round(d["valor"], 2),
                })
            alimentacao_out[data_iso][op] = arr

    # Serviços (Bilheteria/Estacionamento/Parques): bucket isolado, paralelo a Alimentação
    servicos_out = {}
    for data_iso, por_grp in servicos_por_data.items():
        servicos_out[data_iso] = {}
        for grp, prods in por_grp.items():
            arr = []
            for prod, d in sorted(prods.items()):
                q = d["qtd"]
                arr.append({
                    "produto": prod,
                    "categoria": d["categoria"],
                    "pdv": d["pdv"],
                    "qtd": int(q) if q == int(q) else q,
                    "valor": round(d["valor"], 2),
                })
            servicos_out[data_iso][grp] = arr

    # Sistema(s) por sessão: vira sorted list pra ser JSON-serializável
    sistemas_out = {sess: sorted(list(s)) for sess, s in sistemas_por_sessao.items()}

    return (data_list, dict(dpd), ops_out, amb_out, pedidos_out, pedidos_bar_out,
            pedidos_amb_out, pedidos_alim_out, vendas_hora_out, vendas_min_out,
            vendas_min_op_prod_out, terminais_por_min_out, alimentacao_out,
            servicos_out, sistemas_out, ultima_atualizacao)


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


def injetar_no_html(eventos_out: dict):
    """Injeta um único `const EVENTOS = {...}` no HTML, substituindo o placeholder.

    Cada chave de eventos_out é um eventoId. Cada valor é um dict com:
      nome, sessoes, data, dpd, ops, amb, pedidos, pedidos_bar, pedidos_amb,
      vendas_hora, vendas_min, vendas_min_op_prod, terminais_min, alimentacao
    """
    html = HTML_PATH.read_text(encoding="utf-8")

    payload = json.dumps(eventos_out, ensure_ascii=False)
    novo = f"const EVENTOS = {payload};"
    # Substitui o placeholder (qualquer conteúdo entre `const EVENTOS = ` e `;`)
    pattern = r"const EVENTOS = \{.*?\};"
    if re.search(pattern, html, flags=re.DOTALL):
        html = re.sub(pattern, lambda m: novo, html, count=1, flags=re.DOTALL)
    else:
        print("⚠️  placeholder `const EVENTOS = {};` não encontrado — adicione em index.html")
        return

    HTML_PATH.write_text(html, encoding="utf-8")


# =============================================================================
# Main
# =============================================================================
def _evento_vazio(nome: str, sessoes: list) -> dict:
    """Estrutura de evento sem dados (placeholder até a planilha chegar)."""
    return {
        "nome": nome,
        "sessoes": sorted(sessoes),
        "ultima_atualizacao": "",
        "data": [],
        "dpd": {},
        "ops": {},
        "amb": {},
        "pedidos": {},
        "pedidos_bar": {},
        "pedidos_amb": {},
        "pedidos_alim": {},
        "vendas_hora": {},
        "vendas_min": {},
        "vendas_min_op_prod": {},
        "terminais_min": {},
        "alimentacao": {},
        "servicos": {},
        "sistemas": {},
    }


def _coletar_fontes(cfg: dict, evt_id: str) -> list:
    """Devolve lista de tuplas (xlsx_files, sistema, leitor, abas) pra um evento.
    - Se evento tem `subpastas`: usa cada subpasta com seu sistema/leitor/abas.
    - Senão: usa pasta principal como Meep (compat).
    - Fallback Caçapava: xlsx soltos em PLANILHAS_DIR.
    """
    pasta = PLANILHAS_DIR / cfg["pasta"]
    fontes = []
    if "subpastas" in cfg:
        for sub in cfg["subpastas"]:
            p = pasta / sub["sub"]
            if p.exists():
                xlsxs = sorted(p.glob("*.xlsx"))
                if xlsxs:
                    fontes.append((xlsxs, sub["sistema"], sub["leitor"], sub["abas"]))
        if fontes:
            return fontes
    # Sem subpastas (ou subpastas vazias): tenta pasta principal como Meep
    if pasta.exists():
        xlsxs = sorted(pasta.glob("*.xlsx"))
        if xlsxs:
            return [(xlsxs, "MEEP", "meep", cfg["abas"])]
    # Fallback compat (Caçapava antigo)
    if evt_id == EVENTO_PADRAO:
        xlsxs = sorted(PLANILHAS_DIR.glob("*.xlsx"))
        if xlsxs:
            return [(xlsxs, "MEEP", "meep", cfg["abas"])]
    return []


def main():
    PLANILHAS_DIR.mkdir(parents=True, exist_ok=True)
    global SESSOES_VALIDAS

    eventos_out = {}
    for evt_id, cfg in EVENTOS_CONFIG.items():
        fontes = _coletar_fontes(cfg, evt_id)
        if not fontes:
            print(f"⏭️  {evt_id}: sem planilhas em {PLANILHAS_DIR / cfg['pasta']} — placeholder vazio")
            eventos_out[evt_id] = _evento_vazio(cfg["nome"], cfg["sessoes"])
            continue

        print(f"\n🎪 Processando evento: {cfg['nome']} ({evt_id})")
        for xlsxs, sistema, leitor, _abas in fontes:
            print(f"   🔌 Fonte: {sistema} ({leitor}) — {len(xlsxs)} arquivo(s)")
        SESSOES_VALIDAS = set(cfg["sessoes"])
        (data_list, dpd, ops_out, amb_out, pedidos_out, pedidos_bar_out,
         pedidos_amb_out, pedidos_alim_out, vendas_hora_out, vendas_min_out,
         vendas_min_op_prod_out, terminais_por_min_out, alimentacao_out,
         servicos_out, sistemas_out, ultima_atualizacao) = processar(fontes, cfg)
        print(f"   Pedidos únicos:      {sum(pedidos_out.values())} ({pedidos_out})")
        print(f"   Pedidos BAR:         {sum(pedidos_bar_out.values())} ({pedidos_bar_out})")
        print(f"   Pedidos AMB:         {sum(pedidos_amb_out.values())} ({pedidos_amb_out})")
        print(f"   Pedidos ALIM:        {sum(pedidos_alim_out.values())} ({pedidos_alim_out})")
        print(f"   Produtos:            {len(data_list)}  · OPS: {sum(len(v) for v in ops_out.values())} linhas")
        print(f"   Serviços (sessão):   {[(s, list(g.keys())) for s,g in servicos_out.items()]}")
        print(f"   Última transação:    {ultima_atualizacao}")
        # Sessões: união do que está na config (filtro) com o que apareceu nos
        # dados (auto-descoberta). Garante que ingestão incremental sem editar
        # `sessoes` na config ainda liste os dias no frontend.
        sessoes_descobertas = set(cfg["sessoes"]) | set(pedidos_out.keys()) | set(servicos_out.keys())
        eventos_out[evt_id] = {
            "nome": cfg["nome"],
            "sessoes": sorted(sessoes_descobertas),
            "ultima_atualizacao": ultima_atualizacao,
            "data": data_list,
            "dpd": dpd,
            "ops": ops_out,
            "amb": amb_out,
            "pedidos": pedidos_out,
            "pedidos_bar": pedidos_bar_out,
            "pedidos_amb": pedidos_amb_out,
            "pedidos_alim": pedidos_alim_out,
            "vendas_hora": vendas_hora_out,
            "vendas_min": vendas_min_out,
            "vendas_min_op_prod": vendas_min_op_prod_out,
            "terminais_min": terminais_por_min_out,
            "alimentacao": alimentacao_out,
            "servicos": servicos_out,
            "sistemas": sistemas_out,
        }

    injetar_no_html(eventos_out)
    ativos = [eid for eid, e in eventos_out.items() if e["data"]]
    vazios = [eid for eid, e in eventos_out.items() if not e["data"]]
    print(f"\n✅ HTML atualizado. Eventos com dados: {ativos or '—'}. Placeholders: {vazios or '—'}")


if __name__ == "__main__":
    main()
