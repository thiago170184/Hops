# CHANGELOG — IDH HOPS

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento [SemVer](https://semver.org/lang/pt-BR/): `MAJOR.MINOR.PATCH`.

Regra: **versão só é incrementada após o `git push`** (validação local primeiro).

---

## [1.7.0] — 2026-04-26

### Added
- **Suporte multi-sistema (MEEP + ZIG)**: IDH agora ingere planilhas dos dois sistemas no mesmo evento. Bragança Paulista trocou de Meep para Zig em 26/04 (problemas operacionais Meep ontem). Estrutura: `hops-planilhas/braganca-paulista-2026/{meep,zig}/*.xlsx` — detecção do sistema pela subpasta.
- **Leitor Zig (`read_xlsx_zig`)**: parser do XLSX "Lista de Transações" da Zig. Converte serial Excel → ISO datetime, calcula unitário a partir do total da linha, gera ID composto (`Tx+Produto+Qtd+Valor+Terminal`) já que a Zig não tem `PedidoDetalheId`. Devolve dicts no formato Meep — pipeline de agregação não muda.
- **Bucket SERVIÇOS** (paralelo a Alimentação): 3 grupos — `BILHETERIA` (ingressos/cortesias/promocional), `ESTACIONAMENTO`, `PARQUES` (parque de diversão). Roteamento ANTES do filtro de bebida, captura por PDV (`ESTACIONAMENTO`, `PARQUE DIVERSAO`) e categoria (`Outros`).
- **Aba Serviços** no menu (entre Alimentação e Cardápio): cards por grupo com produto + PDV + qtde + valor.
- **KPI Serviços** no Dashboard: faturamento + nº itens (não compõe o total de bebidas).
- **Badge "Origem dos Dados"** no header agora é dinâmico — mostra sistema(s) das sessões selecionadas (`MEEP`, `ZIG` ou `MEEP + ZIG`).
- **Carimbo de sistema por sessão** no JSON do evento (`sistemas`): `{data_iso: ["MEEP","ZIG"]}`. Permite UI saber qual fonte gerou cada sessão.

### Changed
- **`build-data.py`** refatorado: cada evento pode ter `subpastas` com leitores distintos. Caçapava continua igual (compat). Bragança ganhou 2 fontes (`meep/` + `zig/`).
- **Mapa PDV→Operação Bragança** ampliado: aceita prefixos Meep (`FRONT.`, `INTENSE.`, `CORPORATIVO.`, `CAIXA.AMB`) e Zig (`AMBULANTES.`, `ALIMENTACAO.`).
- **`CATEGORIAS_BEBIDAS`** ganhou `BEBIDA` (singular) — Zig usa singular onde Meep usa plural.
- **`NORMALIZACOES`** expandido pra cobrir variações Zig: `AMESTEL`, `CERV AMSTEL`, `CERVEJA AMS 350` → `CERVEJA AMSTEL`. `CERVEJA HEI 350` → `CERVEJA HEINEKEN`.

### Validated
- Bragança 25/04 sessão Zig (R$ 391.746 esperado vs R$ 392.857 calculado, diff < 0,3% por arredondamento de unitário).
- Comida Zig descartada (R$ 215k) — fora de escopo do relatório de bebidas.
- Diferença vs Resumo Financeiro Zig (R$ 615k vs R$ 697k) explicada pelos 20min faltantes na extração + ritmo de pico (~R$ 4-5k/min).

---

## [1.6.0] — 2026-04-25

### Added
- **Alimentação entra no Faturamento Total**: Dashboard ganhou 3 KPIs novos (`Alimentação · Faturamento`, `· Itens`, `· Ticket Médio`) ao lado de BAR e AMBULANTES. As bebidas vendidas em PDV de alimentação (categoria `eh_alimentacao` por evento) passam a somar no card destaque "Faturamento Total", junto com BAR + AMBULANTES. Antes ficavam isoladas na aba Alimentação como "só consulta".
- **`pedidos_alim` no JSON dos eventos**: build-data.py agora coleta também `pedidos_alim_por_data` (set de PedidoIds únicos de bebidas em PDV de alimentação) e injeta em cada evento. Frontend usa para calcular ticket médio da nova linha de KPIs.

### Changed
- **Pcts dos KPIs recalculados sobre o novo total** (BAR + AMB + ALIM). `kpi-itens` e `kpi-total` passam a refletir os 3 grupos.
- **Aba Alimentação**: aviso amarelo no topo deixou de dizer "esses valores NÃO entram no faturamento" — agora descreve a tela como detalhamento da linha de alimentação do Dashboard.

---

## [1.5.3] — 2026-04-25

### Changed
- **Data refresh — manhã de 25/04 (Caçapava + Bragança)**: nova planilha `transacional.xlsx` ingerida (mesmo arquivo unificado em ambas as pastas). Captura transações entre ~04:24 e ~05:56 que faltavam no export anterior.
  - **Caçapava** sessão 24/04: 10.924 → **11.036** pedidos (+112)  ·  total: 28.096 → **28.208**
  - **Bragança** sessão 24/04: 9.518 → **9.659** pedidos (+141)
  - Última transação: **25/04 05:56:55** (Caçapava) · **25/04 05:40:15** (Bragança)

---

## [1.5.2] — 2026-04-25

### Changed
- **Data refresh — madrugada de 25/04 (Caçapava + Bragança)**: nova planilha `transacional.xlsx` ingerida em ambas as pastas (`cacapava-2026/` e `braganca-paulista-2026/`). Dedup global por `PedidoDetalheId` cortou 63.834 linhas em Caçapava e 14.597 em Bragança (sobreposição entre exports anterior e novo).
  - **Caçapava** sessão 24/04: 7.237 → **10.924** pedidos (+3.687)  ·  total: 24.409 → **28.096**
  - **Bragança** sessão 24/04: 5.433 → **9.518** pedidos (+4.085)
  - Última transação: **25/04 04:24**

---

## [1.5.1] — 2026-04-25

### Fixed
- **Frontend não listava sessões dos eventos**: ao mover `EVENTOS_CONFIG[evento].sessoes` para `set()` vazio (auto-descoberta), o JSON injetado em `index.html` saía com `sessoes: []` e o seletor de dia/sessão no frontend ficava vazio. Agora o build injeta a união entre `cfg["sessoes"]` (filtro opcional) e as sessões realmente presentes nos dados (`pedidos_out.keys()`) — sessões aparecem automaticamente conforme os dados forem chegando.

---

## [1.5.0] — 2026-04-25

### Added
- **Rodeio de Bragança Paulista 2026** ativo (sessão 24/04 16h → 25/04 10h). Dados parciais carregados a partir do export Meep `Lista_transacao_Braganca_PARCIAL.xlsx`. 5433 pedidos / 36 produtos / 4 operações (FRONT, INTENSE, CORPORATIVO, AMBULANTES) processados nesta primeira leva.
- **Caçapava 2º fim de semana**: a planilha do Bragança traz também os dados de Caçapava (abas `CAÇAPAVA BAR` e `caçapava ambulante`) — adicionada à pasta `cacapava-2026/` ao lado do `GERAL_CACAPAVA.xlsx` original. Dedup global por `PedidoDetalheId` cortou as 25.477 linhas duplicadas. **+7.237 pedidos novos** na sessão 24/04 (5.013 BAR + 2.224 AMB). Sessão 25/04 noite e 26/04 entram automaticamente quando chegar export completo (parcial vai até 25/04 ~01h, que pelo cálculo de sessão ainda conta como noite de 24/04).
- **Ingestão incremental por padrão (todos os eventos)**: `EVENTOS_CONFIG[evento].sessoes` agora é opcional/vazio por padrão — `sessao_de()` aceita qualquer data presente nos dados quando o set está vazio. Política do sistema: cada xlsx novo é incremental, dedup global por `PedidoDetalheId` descarta duplicatas, datas novas entram sem editar config. Aplicável a Caçapava, Bragança, e qualquer evento futuro.
- **Sessão sem janela cinza (todos os eventos)**: `sessao_de` agora cobre **24h** (17h até 16h59 do dia seguinte) em vez de descartar transações em 08h-16h59. Política: **nunca perder dados**. Mapeamento simplificado: `hh ≥ 17` → sessão = dia atual; `hh < 17` → sessão = dia anterior. Backend gera `mm_abs` em range 0..1439. Janela de pico (frontend) continua filtrada em 0..899 (17h-7h59) — pico é conceito da operação noturna; eventuais vendas diurnas entram em totais mas não na detecção de pico.
- **Aliases de aba por evento**: `abas` agora aceita lista de nomes alternativos por aba (ex: `["BAR", "CAÇAPAVA BAR"]`) — `processar()` tenta cada um até achar a aba existente no xlsx. Permite consumir a planilha do Bragança (que usa nomes prefixados) junto da planilha histórica de Caçapava (nomes simples) sem renomear abas.
- **Suporte a planilha de aba única**: `EVENTOS_CONFIG` agora aceita `aba_tipo="auto"` que classifica BAR vs AMB pelo prefixo do PDV (`CAIXA.AMB.*` → ambulante).
- **Mapa de PDV por evento**: `EVENTOS_CONFIG[evento].mapa_pdv` (callable). Bragança usa derivação por prefixo (`FRONT.*`, `INTENSE.*`, `CORPORATIVO.*`); Caçapava continua com dict explícito (`MAPA_PDV_OPERACAO_CACAPAVA`).
- **Detecção de alimentação por evento**: `eh_alimentacao_op(op, pdv)`. Bragança classifica todo PDV `P.A.*` / `A.C *` / `A.F *` como alimentação (cada PDV vira sua própria operação isolada na aba Alimentação). Caçapava mantém set explícito (`OPERACOES_ALIMENTACAO_CACAPAVA`).
- **Categorias de bebida** ampliadas com variantes de Bragança: `DRINKS`, `SOFTS`, `BEBIDAS`, `MOCHILEIRO`, `BEBIDAS PIT BUL`, `NOVA ERA BEBIDAS`, `BEBIDAS DEZINHO`, `BEBIDAS CAFETERIA`.

### Fixed
- **Typo "Branca Paulista" → "Bragança Paulista"** em `EVENTOS_CONFIG`, ID do evento (`branca-paulista-2026` → `braganca-paulista-2026`), pasta de planilhas e `CONCEITOS.md`.

---

## [1.4.0] — 2026-04-22

### Added
- **Botão "Exportar" na aba Produtos**: gera XLSX com 4 colunas (Produto, Qtde, Valor Unit, Valor Total). Agrega por nome do produto somando qtd e total entre operações. Respeita o filtro de busca e o filtro de data ativo. Nome do arquivo `produtos_<sessoes>[_<busca>].xlsx`.

---

## [1.3.0] — 2026-04-20

### Added
- **Multi-evento**: suporte a múltiplos eventos no mesmo deploy. O cabeçalho agora tem um `<select>` que troca entre Rodeio de Caçapava 2026 e Rodeio de Branca Paulista 2026. Trocar evento re-renderiza todas as abas automaticamente.
- **Estrutura `EVENTOS`** única no HTML contendo `{nome, sessoes, data, ops, amb, pedidos*, vendas*, alimentacao}` por evento. Substitui os ~14 consts top-level anteriores por `let`s reatribuídos via `hidratarEvento()`.
- **`EVENTOS_CONFIG`** no `build-data.py` — adicionar evento novo = adicionar entrada + subpasta em `hops-planilhas/<evento-id>/`.

### Changed
- **Aba Alimentação**: volta a mostrar **apenas bebidas** vendidas em pontos de alimentação (comida foi removida do escopo). Pontos só-comida permanecem listados com "sem bebidas vendidas".
- **Build-data.py** roda o pipeline uma vez por evento; evento sem planilha fica como placeholder vazio.

### Fixed
- **Pareto 80/20 na aba Ambulantes**: limpa corretamente o chart ao trocar pra evento sem dados (antes ficava stale).

---

## [1.2.0] — 2026-04-20

### Added
- **Nova aba "Ritmo de Vendas"**: análise de velocidade de venda por (operação × produto) nas 3 fases do evento — Antes do pico · Durante o pico · Pós pico — mais a média geral. Valores em unidades por 3 min pra dimensionamento de equipe e estoque. Pico detectado dinamicamente (reusa a janela do Timeline).
- **Tabela de fases no topo** do Ritmo: Início/Fim · Duração · Unidades · % das vendas · Faturamento · Terminais únicos.
- **Cabeçalho do card por operação** mostra totais das 3 fases (Antes · Pico · Pós) + Unidades total.
- **Coluna Qtd** (unidades totais) na tabela do Ritmo — contextualiza valores baixos tipo `0,X`.
- **Nova aba "Alimentação"**: vendas (comida + bebida) dos pontos de alimentação, em bucket 100% isolado. **Não impacta faturamento das outras abas**. Inclui BUFFET PRIME vendido através dos camarotes + 9 PDVs de comida (Comida Tropeira, Nova Era, Doce Maciel, Espetinho Jales, Espeto Secretario, Hot Dog Juca, Krep Suíço, Pastel Fernando, Pizza Cone Raul).
- **Hero consolidado** na aba Alimentação (Faturamento · Operações · Unidades), estilo gradient âmbar.
- **Headers responsivos** da tabela Ritmo em mobile: nomes abreviados (Antes · Pico · Pós · Média) pra não sobrepor.

### Changed
- **Dropdown "Operação" da aba Vendas** agora é populado dinamicamente a partir dos dados reais (antes tinha lista hardcoded com operações fantasma). Exclui automaticamente as operações de alimentação.
- **Aba de tabs**: distribuída igualmente na largura do cabeçalho (`flex: 1 1 0` em cada tab) — ficou harmônico com 8 abas.
- **Rebrand no rodapé**: "HOPS — Head of Operations" (antes "Operações e Serviços para Eventos").
- **Pipeline `build-data.py`**: captura isolada em `ALIMENTACAO_POR_DATA` (sem impacto em DATA/OPS_POR_DATA); consolidação de PDVs ESPETO SECRETARIO CAIXA/GARCOM numa única operação; filtro NULL pra evitar bucket-lixo.

### Data
- `VENDAS_MIN_OP_PROD_POR_SESSAO` — qtd por (sessão × op × produto × minuto) pra cálculo dinâmico do Ritmo.
- `TERMINAIS_MIN_POR_SESSAO` — terminais enumerados por minuto (pra contagem de únicos por fase).
- `ALIMENTACAO_POR_DATA` — bucket isolado (R$ 162.198 · 7.228 un nas duas sessões de Caçapava 2026).

---

## [1.1.1] — 2026-04-20

### Added
- **Badge de versão no canto superior direito do cabeçalho** — discreto, branco com 50% de opacidade.

---

## [1.1.0] — 2026-04-20

### Fixed
- **Aba Produtos colapsava operações**: AGUA 510 ML aparecia como vendida só em GARÇOM FRONT (2.962 un) quando na verdade a quantidade era a soma de 6 operações de bar. `renderProdutos()` agora lê de `opsAtivas()` (mesma fonte do Cardápio), mostrando 1 linha por (operação × produto). Totais inalterados.

### Added
- **Exportar Excel na aba Operações**: botão azul ao lado de Recolher. Exporta XLSX respeitando filtro de sessão e busca ativa. Colunas: Operação · Produto · Categoria · Qtde · Valor Unt · Valor (Qtde × Valor Unt). Nome do arquivo inclui sessões ativas e termo de busca.
- **SheetJS** (xlsx 0.18.5) carregado via CDN jsdelivr com `defer`.
- **Versionamento**: arquivo `VERSION`, `CHANGELOG.md` e badge de versão no rodapé.

---

## [1.0.0] — 2026-04-18 (baseline)

Estado inicial do projeto antes do versionamento formal. Histórico de features a partir do `git log`:

- Dashboard com KPIs, timeline por hora, top produtos, top operações
- Aba Vendas
- Aba Produtos (catálogo flat — bug corrigido em v1.1.0)
- Aba Operações com cards expansíveis, busca, sort por coluna
- Aba Ambulantes com ranking por terminal
- Aba Cardápio com breakdown produto × operação
- Pipeline `scripts/build-data.py` — xlsx do Drive → dedup por `PedidoDetalheId` → injeta em `index.html`
- Conceito de sessão (17h → 08h do dia seguinte) substituindo data civil
- Normalização de produtos (AMSTEL → CERVEJA AMSTEL, etc.)
- Filtro de bebidas (descarta alimentação)
- Timeline com marca de pico e modal explicativo
- Deploy automático via Vercel (idh-hops.vercel.app)
