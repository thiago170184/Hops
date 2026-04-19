# IDH — Inteligência de Dados HOPS

Relatórios operacionais da HOPS — Operações e Serviços para Eventos.

## Sobre

Plataforma de inteligência de dados para análise de vendas por operação, agrupamento por categorias, visão macro (Bebidas / Alimentação / Outros) e drill-down até o produto.

- **Evento piloto:** Rodeio de Caçapava 2026
- **Origem dos dados:** Meep
- **Consultoria:** Raphaela Chamon

## Abas do relatório

1. **Dashboard** — KPIs macro, gráficos de barras por operação (Bebidas/Alimentação), top 10 produtos
2. **Vendas** — agrupamento por grupo → operação → categoria → produtos
3. **Produtos** — tabela flat ordenável com busca e filtros
4. **Operações** — visão por setor/PDV com drill-down dos produtos vendidos, subtotais por busca

## Stack

HTML standalone — sem dependências externas obrigatórias. Logo embutida em base64, dados do cardápio e vendas embutidos em `<meta>` e `<script>` inline.

Abertura: duplo-clique no `index.html` — abre em qualquer navegador moderno.
