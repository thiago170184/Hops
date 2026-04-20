# CHANGELOG — IDH HOPS

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento [SemVer](https://semver.org/lang/pt-BR/): `MAJOR.MINOR.PATCH`.

Regra: **versão só é incrementada após o `git push`** (validação local primeiro).

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
