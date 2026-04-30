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
PLANILHAS_DIR = Path("/Users/thiagomonteiro/Documents/hops-planilhas")
HTML_PATH = ROOT / "index.html"
DATA_DIR = ROOT / "data"
PRODUTOS_ESTOQUE_PATH = DATA_DIR / "produtos_estoque.json"
COMPOSICOES_PATH = DATA_DIR / "composicoes.json"

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
    "CERVEJA HEI Z": "CERVEJA HEINEKEN",
    # Lanche com bacon e cheddar — Zig truncou a ordem dos ingredientes
    "BATATA BAC CHE": "BATATA CHE BAC",
}

# =============================================================================
# Overrides de categoria por keyword no NOME DO PRODUTO
# =============================================================================
# Política agnóstica de sistema (Zig, Meep, Dpen, futuros): se o nome do produto
# contém uma keyword conhecida, força a categoria correta — sobrescrevendo o que
# a fonte cadastrou. Necessário porque cadastros de PDV são frequentemente
# errados (ex: SMIRNOFF cadastrado como "Comida" no Zig de Bragança).
#
# A skill /th-auditor-categorias lista produtos suspeitos pra (a) corrigir na
# fonte e (b) sugerir keywords novas pra este dict.
OVERRIDES_CATEGORIA_PRODUTO = {
    "Bebida": [
        # Cervejas
        "AMSTEL", "AMESTEL", "HEINEKEN", "BRAHMA", "SKOL", "ANTARCTICA",
        "CORONA", "STELLA", "BUDWEISER", "CERV ", "CERVEJA ", "BARRIL",
        "CHOPP",
        # Destilados/Premium
        "ABSOLUT", "SMIRNOFF", "BACARDI", "JACK ", "JACK D", "BALLANTINES",
        "JOHNNY", "JW ", "TANQUERAY", "OLD PA", "BELEZA", "OLD PARR",
        "WHISKY", "WHISKEY", "VODKA", "TEQUILA", "GIN ", "RUM ", "CACHACA",
        "CAIPIRINHA", "CAIPIROSKA",
        # Refrigerantes/Energéticos/Não-alcoólicos
        "RED BULL", "REDBULL", "MONSTER", "COCA", "GUARANA", "PEPSI",
        "REFRIGERANTE", "REFRI", "AGUA TONICA", "TONICA",
        # Drinks genéricos
        "DRINK", "DRINKSSI", "DOSE",
    ],
    "Outros": [
        # Bilheteria/serviço
        "INGRESSO", "BILHETE", "CORTESIA", "PROMOCIONAL", "PCD",
        # Estacionamento (vagas vendidas como "Comida" no Zig de Bragança)
        "CARRO", "MOTO", "VAN",
    ],
}

# Pré-compila keywords ordenadas (maior primeiro pra match guloso correto)
_OVERRIDE_BUCKETS = [
    (categoria, sorted([kw.upper() for kw in kws], key=lambda s: -len(s)))
    for categoria, kws in OVERRIDES_CATEGORIA_PRODUTO.items()
]
# Contador global de overrides aplicados (zera a cada processar())
_overrides_aplicados = Counter()

def corrigir_categoria(categoria_origem: str, produto: str) -> str:
    """Se o nome do produto bate com keyword de OVERRIDES_CATEGORIA_PRODUTO,
    retorna a categoria correta — independente do que veio da fonte. Senão
    retorna a categoria original. Funciona pra qualquer sistema (Zig, Meep, Dpen)."""
    if not produto:
        return categoria_origem
    pu = produto.upper().strip()
    # Remove prefixo (C) de cancelamento pra match
    if pu.startswith("(C)"):
        pu = pu[3:].strip()
    for cat_correta, kws in _OVERRIDE_BUCKETS:
        for kw in kws:
            if kw in pu:
                if categoria_origem != cat_correta:
                    _overrides_aplicados[(produto, categoria_origem, cat_correta)] += 1
                return cat_correta
    return categoria_origem

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

# PDVs específicos do Bragança Zig que vendem comida sem prefixo padrão.
# Detectados no CSV de 26/04: cadastro inconsistente da Zig — esses pontos não
# têm prefixo ALIMENTACAO./ALIM./ALI., mas operacionalmente são lanchonetes.
PDVS_ALIMENTACAO_BRAGANCA_EXTRA = {
    "CREPE ISONEY", "GREGO", "GARCOM LANCHONETE HAMILTON", "GARCOM NOVA ERA",
    "GARCOM CIA DO LANCHE", "BUFFET PRIME EMPRESARIAL", "GARCOM MAZZA CAMAROTE",
    "GELO",
}

def eh_alimentacao_braganca(pdv: str) -> bool:
    pu = (pdv or "").upper()
    if pu.startswith(("P.A", "A.C", "A.F")):                  # Meep
        return True
    if pu.startswith(("ALIMENTACAO.", "ALIM.", "ALI.")):      # Zig (3 variações de prefixo)
        return True
    if pu in PDVS_ALIMENTACAO_BRAGANCA_EXTRA:                  # Zig sem prefixo
        return True
    return False

def eh_ambulante_braganca(pdv: str) -> bool:
    pu = (pdv or "").upper()
    return pu.startswith("CAIXA.AMB") or pu.startswith("AMBULANTES.")


# Roteamento SERVIÇOS (Bilheteria/Estacionamento/Parques) — paralelo a Alimentação.
# Aplicado ANTES do filtro de bebidas. Funciona pra MEEP e ZIG.
# Detecção é feita por PDV E nome do produto (não por categoria — Outros é
# poluído por cadastro errado, ex: MINI BURGUER cadastrado como Outros no Zig).
def classificar_servico(pdv: str, categoria: str, produto: str):
    pu = (pdv or "").upper()
    prodU = (produto or "").upper()
    if "ESTACIONAMENTO" in pu:                # Meep: P.A. ESTACIONAMENTO; Zig: ESTACIONAMENTO
        return "ESTACIONAMENTO"
    if "PARQUE" in pu and "DIVERS" in pu:     # Meep: P.A. PARQUE DIVERSAO; Zig: PARQUE DIVERSAO
        return "PARQUES"
    if pu.startswith("BILHETERIA") or "BILHET" in pu:   # PDV de bilheteria → tudo é ingresso
        return "BILHETERIA"
    # Produto explicitamente ingresso/cortesia/promocional (tipos de "entrada no evento")
    if any(kw in prodU for kw in ("INGRESSO","BILHETE","PROMOCIONAL","CORTESIA")):
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


def read_zig(path: Path):
    """Dispatcher: lê arquivo da Zig (xlsx OU csv) e devolve dicts no formato Meep.
    Detecta pela extensão. Centralizado pra que o pipeline chame um nome só."""
    ext = path.suffix.lower()
    if ext == ".csv":
        return _read_zig_csv(path)
    return _read_zig_xlsx(path)


def _read_zig_csv(path: Path):
    """CSV Zig: ISO-8859-1, separador `;`, valores com prefixo `=" "` (Excel quote),
    decimal vírgula BR, datas dd/mm/yyyy hh:mm:ss. Header em linha que tem
    'Transação' e 'Data Realização' (geralmente linha 14, mas detecta sozinho).
    Filtra Status != 'Efetivada'.
    """
    import csv as csvmod
    with open(path, encoding="iso-8859-1", newline="") as f:
        raw_lines = f.readlines()

    header_idx = None
    for i, line in enumerate(raw_lines[:50]):
        if "Transação" in line and "Data Realização" in line:
            header_idx = i
            break
    if header_idx is None:
        return []

    reader = csvmod.reader(raw_lines[header_idx:], delimiter=";")
    headers = [h.strip() for h in next(reader)]
    hmap = {h: i for i, h in enumerate(headers)}
    needed = ["Transação", "Data Realização", "Operação", "Terminal", "Nome Ponto",
              "Categoria Produto", "Produto", "Quantidade", "Valor", "Status", "Tipo Ponto"]
    if any(c not in hmap for c in needed):
        print(f"⚠️  CSV Zig: colunas faltando. Headers: {headers}")
        return []

    def cell(row, name):
        v = row[hmap[name]] if hmap[name] < len(row) else ""
        v = v.strip()
        # Remove Excel quote `="..."`
        if v.startswith('="') and v.endswith('"'):
            v = v[2:-1]
        return v.strip()

    out = []
    for row in reader:
        if not any(c.strip() for c in row):
            continue
        if cell(row, "Status") != "Efetivada":
            continue
        tx = cell(row, "Transação")
        if not tx:
            continue
        # Data BR → ISO
        data_br = cell(row, "Data Realização")
        try:
            dt = datetime.strptime(data_br, "%d/%m/%Y %H:%M:%S")
            data_iso = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        pdv = cell(row, "Nome Ponto")
        cat = cell(row, "Categoria Produto")
        prod = cell(row, "Produto")
        terminal = cell(row, "Terminal")
        try: qtd = float(cell(row, "Quantidade").replace(",", "."))
        except ValueError: qtd = 0
        valor_total = _br_decimal(cell(row, "Valor"))
        unit = round(valor_total / qtd, 4) if qtd not in (0, 0.0) else 0.0
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


def _read_zig_xlsx(xlsx_path: Path):
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
    # Reset contador de overrides pra este evento
    _overrides_aplicados.clear()

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
            zig_rows = read_zig(xlsx)
            print(f"   1 aba Zig ({xlsx.suffix.lstrip('.').upper()}): {len(zig_rows)} linhas válidas (Efetivada)")
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
                cat_origem = (r.get(COL_CATEGORIA) or "").strip()
                produto_bruto = (r.get(COL_PRODUTO) or "").strip()
                # Override por nome do produto (regras agnósticas de sistema).
                # Ex.: SMIRNOFF cadastrado como "Comida" no Zig vira "Bebida".
                cat = corrigir_categoria(cat_origem, produto_bruto)
                produto = normalizar_produto(produto_bruto)
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
    print(f"   Overrides cat aplic: {sum(_overrides_aplicados.values())}  ({len(_overrides_aplicados)} produtos distintos)")
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
    """Injeta `const EVENTOS = {...}`, `const PRODUTOS_ESTOQUE = [...]` e atualiza
    badges de versao (header + footer) a partir do arquivo VERSION."""
    html = HTML_PATH.read_text(encoding="utf-8")

    # Sincroniza versao no header e footer com o arquivo VERSION
    version_file = ROOT / "VERSION"
    if version_file.exists():
        ver = version_file.read_text(encoding="utf-8").strip()
        if ver:
            html = re.sub(r'<span class="doc-version">v[\d.]+</span>',
                          f'<span class="doc-version">v{ver}</span>', html, count=1)
            html = re.sub(r'(HOPS — Head of Operations <span[^>]*>· v)[\d.]+( ·)',
                          rf'\g<1>{ver}\g<2>', html, count=1)

    payload = json.dumps(eventos_out, ensure_ascii=False)
    novo = f"const EVENTOS = {payload};"
    # Substitui o placeholder (qualquer conteúdo entre `const EVENTOS = ` e `;`)
    pattern = r"const EVENTOS = \{.*?\};"
    if re.search(pattern, html, flags=re.DOTALL):
        html = re.sub(pattern, lambda m: novo, html, count=1, flags=re.DOTALL)
    else:
        print("⚠️  placeholder `const EVENTOS = {};` não encontrado — adicione em index.html")
        return

    # Injeta tambem o catalogo de produtos de estoque (pro modal de edicao)
    if PRODUTOS_ESTOQUE_PATH.exists():
        produtos = json.loads(PRODUTOS_ESTOQUE_PATH.read_text(encoding="utf-8"))
        payload_est = json.dumps(produtos, ensure_ascii=False)
        novo_est = f"const PRODUTOS_ESTOQUE = {payload_est};"
        pattern_est = r"const PRODUTOS_ESTOQUE = \[.*?\];"
        if re.search(pattern_est, html, flags=re.DOTALL):
            html = re.sub(pattern_est, lambda m: novo_est, html, count=1, flags=re.DOTALL)
        else:
            print("⚠️  placeholder `const PRODUTOS_ESTOQUE = [];` nao encontrado em index.html")

    HTML_PATH.write_text(html, encoding="utf-8")


# =============================================================================
# Estoque (composições + consumo agregado)
# =============================================================================
def carregar_composicoes():
    """Le data/produtos_estoque.json e data/composicoes.json.

    Retorna (produtos_estoque_index, composicoes_por_evento).
      - produtos_estoque_index: {id: {id, categoria, nome}}
      - composicoes_por_evento: {evt_id: {nome_vendido_upper: regra}}
    """
    if not PRODUTOS_ESTOQUE_PATH.exists() or not COMPOSICOES_PATH.exists():
        return {}, {}
    estoque = json.loads(PRODUTOS_ESTOQUE_PATH.read_text(encoding="utf-8"))
    estoque_index = {p["id"]: p for p in estoque}
    composicoes = json.loads(COMPOSICOES_PATH.read_text(encoding="utf-8"))
    # Normaliza chaves (nome do produto vendido) pra UPPER, garante consistencia
    comps_norm = {}
    for evt_id, sugs in composicoes.items():
        comps_norm[evt_id] = {(k or "").strip().upper(): v for k, v in sugs.items()}
    return estoque_index, comps_norm


def calcular_consumo_estoque(data_list, dpd, composicoes_evento, estoque_index):
    """Soma qtd_vendida x fracao por alvo (produto ou categoria de estoque).

    Retorna dict {key: {tipo, alvo, nome, categoria, total}}, onde key e
    "produto::<id>" ou "categoria::<NOME>". Tambem retorna lista de produtos
    vendidos sem composicao (pra debug/cobertura).
    """
    if not composicoes_evento or not estoque_index:
        return {}, []

    nome_por_id = {str(p["id"]): (p.get("nome") or "").strip().upper() for p in data_list}
    qtd_por_nome = defaultdict(float)
    for _data, by_id in dpd.items():
        for pid, qtd in by_id.items():
            nome = nome_por_id.get(str(pid), "")
            if nome:
                qtd_por_nome[nome] += float(qtd or 0)

    consumo = {}
    sem_composicao = []
    for nome_vendido, qtd_total in qtd_por_nome.items():
        comp = composicoes_evento.get(nome_vendido)
        if not comp:
            sem_composicao.append(nome_vendido)
            continue
        if not comp.get("controla"):
            continue
        for v in comp.get("vinculos", []) or []:
            alvo = v.get("alvo")
            tipo_alvo = v.get("tipo_alvo")
            fracao = float(v.get("fracao", 1) or 0)
            if not alvo or not tipo_alvo:
                continue
            key = f"{tipo_alvo}::{alvo}"
            entry = consumo.setdefault(key, {
                "tipo": tipo_alvo,
                "alvo": alvo,
                "nome": alvo,
                "categoria": alvo if tipo_alvo == "categoria" else "",
                "total": 0.0,
            })
            if tipo_alvo == "produto":
                p = estoque_index.get(alvo)
                if p:
                    entry["nome"] = p["nome"]
                    entry["categoria"] = p["categoria"]
            entry["total"] += qtd_total * fracao

    # Arredonda totais pra 4 casas (evita ruido de float)
    for entry in consumo.values():
        entry["total"] = round(entry["total"], 4)

    return consumo, sorted(sem_composicao)


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
        "composicao": {},
        "consumo_estoque": {},
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
                # Leitor zig aceita xlsx OU csv (Zig exporta nos 2 formatos)
                exts = ("*.xlsx", "*.csv") if sub["leitor"] == "zig" else ("*.xlsx",)
                arquivos = sorted([x for ext in exts for x in p.glob(ext)])
                if arquivos:
                    fontes.append((arquivos, sub["sistema"], sub["leitor"], sub["abas"]))
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

    estoque_index, composicoes_por_evento = carregar_composicoes()
    if estoque_index:
        print(f"📦 Estoque carregado: {len(estoque_index)} produtos no catalogo, "
              f"{sum(len(v) for v in composicoes_por_evento.values())} composicoes")

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
        composicao_evt = composicoes_por_evento.get(evt_id, {})
        consumo, sem_comp = calcular_consumo_estoque(
            data_list, dpd, composicao_evt, estoque_index
        )
        if composicao_evt:
            controlados = sum(1 for c in composicao_evt.values() if c.get("controla"))
            print(f"   Estoque:             {controlados}/{len(composicao_evt)} produtos controlados, "
                  f"{len(consumo)} alvos com consumo, {len(sem_comp)} sem composicao")

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
            "composicao": composicao_evt,
            "consumo_estoque": consumo,
        }

    injetar_no_html(eventos_out)
    ativos = [eid for eid, e in eventos_out.items() if e["data"]]
    vazios = [eid for eid, e in eventos_out.items() if not e["data"]]
    print(f"\n✅ HTML atualizado. Eventos com dados: {ativos or '—'}. Placeholders: {vazios or '—'}")


if __name__ == "__main__":
    main()
