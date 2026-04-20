# HOPS — Conceitos, Regras de Negócio e Memórias de Cálculo

> **Objetivo**: este documento é o briefing completo do HOPS v1.x (2026-04) para o futuro **v2.0** (Supabase + API). Qualquer reescrita deve preservar as decisões abaixo — todas foram derivadas de conversas com o cliente ou descobertas em dados reais.
>
> Referência de código: `index.html` + `scripts/build-data.py`.
> Referência do produto: <https://idh-hops.vercel.app/>.

---

## 1. O que é HOPS

**HOPS — Head of Operations** é um relatório operacional de vendas de **bebidas** em eventos grandes (rodeios, festivais), consumindo as transações do sistema **Meep** (PDV + cashless do evento).

Clientes atuais (abril 2026):
- **Rodeio de Caçapava 2026** (17/04 e 18/04)
- **Rodeio de Branca Paulista 2026** (a acontecer — próximo fim de semana, a planilha chega depois)

Público-alvo do relatório: operadores do evento + financeira. Usado para dimensionar equipe, repor estoque, entender ritmo de venda, identificar top terminais ambulantes.

---

## 2. Conceito de SESSÃO (crítico)

Eventos não operam por dia civil. Cada **sessão** começa às **17h** e termina às **08h** do dia seguinte.

- **Chave da sessão** = data de **início** (formato `YYYY-MM-DD`).
- **Janela cinza** (08h–17h) = sem vendas — transações nesse range são descartadas.
- Mapeamento (função `sessao_de` em `build-data.py`):

| Hora da transação | Sessão |
|---|---|
| `hh >= 17` | mesmo dia |
| `hh < 8` | dia anterior |
| `08 ≤ hh < 17` | **descartar** |

**Nunca agregar por data civil** — sempre por sessão. Todos KPIs, gráficos e filtros respeitam `diasAtivos()`.

`SESSOES_VALIDAS` é set de strings por evento, definido em `EVENTOS_CONFIG[evtId]["sessoes"]`. Exemplo Caçapava: `{"2026-04-17", "2026-04-18"}`.

---

## 3. Deduplicação

**Chave de dedup**: `PedidoDetalheId` (cada linha do xlsx da Meep é um item de pedido único).

Planilhas são **cumulativas** — quem baixa a exportação no dia 2 pode receber as linhas do dia 1 novamente. O script ignora IDs já vistos.

---

## 4. Normalização de Produtos

Tabela de aliases `NORMALIZACOES` em `build-data.py`:

```python
NORMALIZACOES = {
    "AMSTEL": "CERVEJA AMSTEL",
    "HEINEKEN": "CERVEJA HEINEKEN",
}
```

- Aplicar ao **ler** qualquer nome de produto (case-insensitive, strip).
- Ao encontrar NOVOS pares potenciais (X vs CERVEJA X), **perguntar ao usuário** antes de consolidar automaticamente.
- Quando consolidar dois produtos com nome canônico igual: agregar `qtd` e `valor` no DATA e OPS_POR_DATA; remapear IDs em DADOS_POR_DATA.

---

## 5. Operações e PDVs

### PDV APELIDO → Operação

O cliente chama os pontos físicos de **PDV APELIDO** (string na planilha da Meep). Agrupamos PDVs relacionados em **operações** lógicas via `MAPA_PDV_OPERACAO`:

| PDV APELIDO | Operação lógica |
|---|---|
| `A1. ATENDENTE.CORP`, `BAR CORPORATIVO`, `C1.CAIXA.CORP` | CAMAROTE CORP |
| `A1. ATENDENTENTE INTENSE`, `BAR INTENSE`, `C2.2.CAIXA.INTENSE` | CAMAROTE INTENSE |
| `CERVEJARIA PRAÇA PITBULL 1/2`, `BEBIDA CAMARÃO` | CERVEJARIA |
| `B1.BAR.FRONT`, `B2.BAR.FRONT`, `C1.1.CAIXA.FRONT`, `C2.2.CAIXA.FRONT`, `C2.3.CAIXA.FRONT` | OPERAÇÃO BAR FRONT |
| `B3.BAR.PISTA`, `CAIXA PISTA` | OPERAÇÃO BAR PISTA |
| `GARÇOM FRONT` | GARÇOM FRONT |
| `WHISKERIA`, `WHISKERIA 1/2`, `CAIXA MÓVEL WHISKERIA` | WHISKERIA |
| `ESPETO SECRETARIO CAIXA`, `ESPETO SECRETARIO GARCOM` | ESPETO SECRETARIO |

**Fallback**: se PDV não estiver no mapa, a operação = o próprio PDV (tal qual).

### Tipos de operação

- **Bares fixos**: CAMAROTE CORP, CAMAROTE INTENSE, CERVEJARIA, GARÇOM FRONT, OPERAÇÃO BAR FRONT, OPERAÇÃO BAR PISTA, WHISKERIA.
- **Ambulantes**: todas as transações da aba AMBULANTE viram a operação única `AMBULANTES`. O detalhamento por terminal é uma dimensão separada (ver §9).
- **Alimentação**: pontos primariamente de comida. Ver §6.

### Operações de Alimentação

Set fixo em `OPERACOES_ALIMENTACAO` (build-data.py):

- Com bebidas: COMIDA TROPEIRA, NOVA ERA
- Só comida (sem bebidas): DOCE MACIEL, ESPETINHO JALES, ESPETO SECRETARIO, HOT DOG JUCA, KREP SUIÇO, PASTEL FERNANDO, PIZZA CONE RAUL

**Regra**: essas operações **são excluídas** do relatório principal de bebidas (`ops_por_data`, `data`, `vendas_*`). As bebidas vendidas nelas vão para um bucket **isolado** `alimentacao_por_data` — **zero impacto no faturamento principal**.

Pontos só-comida aparecem na aba Alimentação como "sem bebidas vendidas" (placeholder esmaecido, pra manter visibilidade operacional).

---

## 6. Categorias de Produto

### Categorias consideradas BEBIDA (`CATEGORIAS_BEBIDAS`)

```
CERVEJAS, CERVEJARIA PRAÇA,
DRINK, SOFT, GARRAFAS,
WHISKERIA - DOSES, WHISKERIA - DRINKS PRONTOS,
WHISKERIA - BATIDAS E CAIPIRINHAS, WHISKERIA - DRINKS COPAO,
WHISKERIA - BEBIDAS LATA,
COMIDA TROPEIRA - BEBIDAS
```

Transações em categorias **não-bebida** são **descartadas** do relatório de bebidas (exceto quando a categoria é `COMIDA TROPEIRA - BEBIDAS`, que é exceção de nomenclatura).

### BUFFET PRIME (pegadinha)

Algumas transações em CAMAROTE CORP/INTENSE têm categoria `BUFFET PRIME` (refeição do camarote corporativo). É **comida vendida em bar**, não bebida. **Descartar** (não entra nem no relatório principal nem na aba Alimentação).

---

## 7. Grupos

Dois grupos para visualização:

| Grupo | Cor CSS | Quando |
|---|---|---|
| `BEBIDAS` | `--bebidas` (azul navy) | Qualquer op exceto AMBULANTES |
| `BEBIDAS AMBULANTES` | `--ambulantes` (vermelho) | Op = AMBULANTES |

O grupo é derivado da operação, não da categoria.

---

## 8. Cálculo do valor da transação

```python
valor_linha = qtd × ValorProduto
```

- **Não usar `ValorPedido`** — vem `#VALUE!` na aba AMBULANTE do xlsx.
- `ValorProduto` = preço unitário do cardápio (ex: R$ 12,00 AMSTEL).
- `qtd` = campo `Quantidade` da planilha.

---

## 9. Terminais Ambulantes

Cada transação AMBULANTE tem um `Equipamento` (= terminal físico PAG*****). Registramos:

- **Faturamento por terminal** por sessão (em `amb_por_data`).
- **Top produtos vendidos por terminal**.
- **Terminais únicos ativos por minuto** (em `terminais_por_min`, enumerados por índice pra reduzir payload).

Usado em: aba Ambulantes (Pódio top-3, Pareto 80/20), aba Ritmo (contagem de terminais únicos por fase).

---

## 10. Estruturas de Dados (no HTML)

Após build, o `index.html` tem um único `const EVENTOS = {...}`. Cada evento:

| Chave | Formato | Uso |
|---|---|---|
| `nome` | string | Display no `<select>` do cabeçalho |
| `sessoes` | `[YYYY-MM-DD, ...]` | Inicializa filtros de data |
| `data` | lista de produtos `{id, nome, categoria, operacao, grupo, preco}` | Catálogo (aba Vendas, Produtos). **Limitação**: 1 linha por (produto, grupo) — a `operacao` é a primeira vista (fonte de vários bugs resolvidos; usar com cuidado). |
| `ops` | `{sessao: {operacao: [{produto, categoria, qtd, valor, preco}]}}` | Aba Operações, Ritmo, Cardápio, totais reais |
| `amb` | `{sessao: [{terminal, qtd, valor, produtos: [...]}]}` | Aba Ambulantes |
| `dpd` | `{sessao: {produto_id: qtd}}` | Meta de cardápio × sessão (legado) |
| `pedidos`, `pedidos_bar`, `pedidos_amb` | `{sessao: int}` | KPIs do Dashboard |
| `vendas_hora` | `{sessao: {hh: {bar, amb, bar_qtd, amb_qtd}}}` | Timeline horária (gráfico de barras) |
| `vendas_min` | `{sessao: {min_abs: valor}}` | Timeline por minuto; detecção de pico |
| `vendas_min_op_prod` | `{sessao: {op: {produto: {min_abs: qtd}}}}` | Aba Ritmo (agregação por fase) |
| `terminais_min` | `{sessao: {min_abs: [idx_terminal]}}` | Aba Ritmo (terminais únicos por fase) |
| `alimentacao` | `{sessao: {op_alim: [{produto, categoria, qtd, valor}]}}` | Aba Alimentação (bucket isolado) |

`min_abs` = minuto absoluto desde 17h00 da sessão. `hh >= 17`: `(hh-17)*60 + mm`. `hh < 8`: `(hh+7)*60 + mm`. Range válido: 0..899 (900 min = 15h).

---

## 11. Janela de Pico

Usada em: Dashboard (timeline) e aba Ritmo de Vendas.

**Algoritmo** (função `calcularJanelaPico`):

1. Agrega `vendas_min[sess][min]` através das sessões ativas (filtro do usuário) → array `vendasMin[0..899]`.
2. Calcula média móvel de 15 minutos (`smooth[i]` = soma dos últimos 15 minutos / janela).
3. `peakMax = max(smooth)`; `argmax` = minuto do pico.
4. `threshold = peakMax × 0,6`.
5. **Expande** a partir do argmax: enquanto `smooth[i-1] ≥ threshold`, decrementa `startMin`. Enquanto `smooth[i+1] ≥ threshold`, incrementa `endMin`.
6. Retorna `{startMin, endMin, duracaoMin = endMin - startMin}` ou `null` se sem dados.

**Parâmetros fixos**: `WIN = 15 min`, `threshold = 60%`. Trocar afetaria comparações históricas.

---

## 12. Fases (Ritmo de Vendas)

Dadas `startMin` e `endMin` da janela de pico:

| Fase | Intervalo | Semântica |
|---|---|---|
| **Antes** | `[primeiro_min_com_venda, startMin-1]` | Subida — operação esquentando |
| **Durante** | `[startMin, endMin]` (inclusivo) | Pico — demanda máxima |
| **Pós** | `[endMin+1, último_min_com_venda]` | Descida — desmontagem |

Se um produto não teve venda em uma fase, `qtd = 0` e duração da fase (pra esse produto) é 0.

---

## 13. Taxa a cada 3 minutos

Fórmula (função `taxa` na renderRitmoVendas):

```
taxa_3min = qtd_na_fase × 3 / duracao_da_fase_em_minutos
```

Exemplo: 450 unidades vendidas em 208 minutos de pico → `450 × 3 / 208 ≈ 6,5 un/3min`.

**Para "Média evento"**: usa toda a janela do produto (primMin → ultMin), não só pico.

---

## 14. Contagem de Terminais Únicos por Fase

Para uma fase `[ini, fim]` em sessões ativas:

```js
const termSet = new Set();
for (sess of sessoes_ativas) {
    for (m = ini; m <= fim; m++) {
        for (idx of TERMINAIS_MIN[sess][m] || [])
            termSet.add(sess + ':' + idx);
    }
}
return termSet.size;
```

A chave `sess:idx` qualifica o terminal pela sessão — evita colisões cross-sessão (mesmo idx pode representar terminais diferentes em sessões diferentes).

---

## 15. Pareto 80/20 (Ambulantes)

1. Ordena ambulantes por faturamento **decrescente**.
2. Acumula. O **subset que acumula ≥ 80% da receita** = "Vital Few".
3. Calcula % do total de ambulantes que são Vital Few.

Exemplo real Caçapava: 25 ambulantes (55,6% do total) geraram 80,8% da receita. Equivale a "56% dos ambulantes = 80% das vendas".

---

## 16. Filtros

| Filtro | Escopo | Comportamento |
|---|---|---|
| **Data/Sessão** (multi-select) | Todas as abas | Vazio = todas ativas. Re-seleção restaura o default. |
| **Busca produto** | Produtos, Operações, Ritmo, Alimentação | Substring match (lowercase) em nome + categoria + operação + grupo. |
| **Operação** (dropdown) | Vendas | Populado dinamicamente a partir de `OPS_POR_DATA`. Exclui automaticamente ops de alimentação. |
| **Evento** (select no header) | Global | Reatribui todos os estados via `hidratarEvento(id)`. |

---

## 17. Exportação

- Aba **Operações** → botão azul **Exportar** (SheetJS 0.18.5 via CDN jsdelivr).
- Respeita filtros ativos (sessão + busca).
- Colunas: **Operação · Produto · Categoria · Qtde · Valor Unt · Valor**.
- `Valor = Qtde × Valor Unt`.
- Formato numérico BR (`z: '0.00'` renderiza como `9999,99` em Excel pt-BR).
- Nome do arquivo: `operacoes_<sessoes>_<termo_busca>.xlsx`.

---

## 18. Pipeline de Build

```
xlsx na pasta do evento  →  scripts/build-data.py  →  const EVENTOS = {...} injetado no index.html  →  git push  →  Vercel auto-deploy (~15s)
```

Pastas:
```
/Users/thiagomonteiro/Downloads/hops-planilhas/
    cacapava-2026/
        GERAL_CACAPAVA.xlsx
    branca-paulista-2026/
        <xlsx futuro>
```

Script processa **um evento por vez** em loop, agregando em `eventos_out`, e injeta **um único** `const EVENTOS = {...}`.

---

## 19. Multi-evento

- `EVENTOS_CONFIG` em `build-data.py`: id, nome, sessões, pasta.
- Frontend: `const EVENTOS = {}` + `let`s top-level + função `hidratarEvento(id)` que reatribui todos os estados globais.
- Troca de evento: `<select id="evento-select">` no header → dispara `hidratarEvento` + `reinitEvento` (re-render + reset de dropdowns dependentes).
- Evento **sem planilha**: placeholder com estruturas vazias — aparece no select mas as abas mostram "sem dados".

Adicionar um **novo evento**:
1. Entrada em `EVENTOS_CONFIG`.
2. Subpasta em `hops-planilhas/`.
3. xlsx na subpasta.
4. Rodar `build-data.py`.

Zero código frontend.

---

## 20. Versionamento

- SemVer (MAJOR.MINOR.PATCH) em:
  - `VERSION` (texto puro)
  - `CHANGELOG.md` (Keep a Changelog)
  - Badges no HTML: canto superior direito do header + rodapé
- Regra: **versão incrementa após push validado**, nunca antes.

---

## 21. Convenções de UI

- **Gradient navy** no cabeçalho (`#060B39 → #0b1455 → #101e6b`).
- **Hero por aba**: Ambulantes vermelho, Alimentação âmbar→laranja, Ritmo com tabela de fases no topo.
- **Números**: tabular-nums; valores R$ em pt-BR (`R$ 9.999,99`); taxas em 1 casa decimal com vírgula.
- **Mobile** (breakpoint 640px): tabs distribuídas por `flex: 1 1 0`; labels abreviados em tabelas densas (Ritmo: Antes/Pico/Pós/Média em vez de "Antes do pico" etc).

---

## 22. Limitações conhecidas (v1)

1. **Dados embutidos no HTML** → bundle cresce linear com eventos (~500 KB hoje com 1 evento).
2. **Atualização manual**: baixar xlsx do Drive → rodar script → push. Não há webhook Meep.
3. **Sem auth**: relatório é público na URL.
4. **Sem histórico/audit**: só uma versão por vez do HTML. Não dá pra comparar "antes × depois" de uma correção.
5. **Sem edição inline**: corrigir nome de produto exige mexer em `NORMALIZACOES` e rebuildar.
6. **Parâmetros fixos** do Pareto (80%) e da janela de pico (60% threshold, smooth 15min).
7. **DATA atual** é catálogo achatado — não usar como fonte de drill-down por operação (já houve bug); sempre usar `opsAtivas()`.
8. **Dedup por `PedidoDetalheId`** assume que a Meep nunca reaproveita IDs. Até agora não houve colisão.
9. **Gray window (08h-17h)** — transações aí são descartadas. Se o evento tiver venda diurna (ex: matinê), regra precisa mudar.

---

## 23. Premissas para o v2.0

O que o cliente pediu (ou ficou explícito nas conversas):

- **Stack alvo**: Supabase (Postgres + RLS + Edge Functions) + Vite/React/shadcn + Vercel.
- **Captura de dados**: via API (Meep se houver, ou sistema intermediário Zig/HubSpot/ClickUp) em vez de xlsx manual.
- **Multi-evento** é requisito permanente — o modelo de dados deve suportar N eventos simultâneos.
- **Gestão de eventos** no próprio app (criar/editar/arquivar sem mexer em código).
- **Histórico**: comparar evento atual × anteriores (Pareto ano × ano, ticket médio por senioridade de vendedor etc).
- **Multi-usuário com permissões**: cliente final vê só o evento dele; operador Zig vê vários.
- **Preservar todos os conceitos acima** (sessão, pico, fases, ritmo, alimentação, Pareto etc.).

Não decidido ainda (ficou pro pós-Branca Paulista):
- Webhook Meep vs pull periódico.
- Tempo real (streaming) vs lote (refresh manual ou cron hourly).
- Onboarding do cliente: eles mesmos upam xlsx até ter API? Ou só via Zig?

---

## 24. Glossário rápido

| Termo | Definição |
|---|---|
| **Sessão** | Janela 17h → 08h do dia seguinte. Chave = data de início. |
| **Gray window** | 08h–17h. Transações aí são descartadas. |
| **PDV APELIDO** | Nome textual do PDV na Meep. Ex: `B1.BAR.FRONT`. |
| **Operação** | Agrupamento lógico de PDVs. Ex: OPERAÇÃO BAR FRONT. |
| **PedidoDetalheId** | Chave de dedup das linhas de transação. |
| **Vital Few** | Subset de ambulantes (ordenados por faturamento) que acumula 80% da receita. |
| **Ritmo** | Unidades vendidas por bloco de 3 minutos (média da fase). |
| **Fase** | Antes / Durante / Pós (pico). Juntas cobrem todo o evento. |
| **mm_abs** | Minuto absoluto desde 17h00 da sessão (0..899). |
| **Alimentação** | Bucket isolado de bebidas vendidas em operações primariamente de comida. Zero impacto no faturamento principal. |
| **BUFFET PRIME** | Categoria de comida vendida em bar de camarote. Descartada (não é bebida nem op de alimentação). |
