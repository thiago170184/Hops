# CHANGELOG — IDH HOPS

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Versionamento [SemVer](https://semver.org/lang/pt-BR/): `MAJOR.MINOR.PATCH`.

Regra: **versão só é incrementada após o `git push`** (validação local primeiro).

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
