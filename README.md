# Radar de Notícias

Coletor de RSS que roda sozinho a cada hora no GitHub Actions, filtra o que importa para a carteira (Brasil, EUA/global, cripto) e guarda num histórico leve em JSONL.

## Como funciona
1. `sources.yaml`: lista de feeds (liga/desliga com `ativo`).
2. `rules.yaml`: temas com palavras-chave e pesos, lista de descarte e nota mínima.
3. `collector.py`: baixa os feeds, descarta duplicatas e lixo, pontua e grava.
4. `.github/workflows/coletar.yml`: roda a coleta a cada hora e faz commit do resultado.

## Saídas
| Arquivo | Conteúdo |
|---|---|
| `data/AAAA-MM.jsonl` | Base permanente: uma notícia por linha (id, data, fonte, título, resumo, link, temas, score). |
| `recentes.md` | Últimas 48h, legível; é o que o briefing diário lê. |
| `recentes.json` | Mesmo conteúdo em JSON. |
| `status.json` | Saúde da última coleta por fonte (ok, itens, item mais recente, erro). |

## Ajustes comuns
- Nova fonte: adicione um bloco em `sources.yaml`.
- Muito ruído: suba `pontuacao_minima` ou acrescente termos em `descartar`.
- Faltando notícia: acrescente o termo no tema certo de `rules.yaml`.
- Testar sem gravar: `python collector.py --dry-run`.

## Consultas rápidas
```bash
# tudo sobre eleição em outubro
grep '"eleicao"' data/2026-10.jsonl | jq -r '.data[:16]+"  "+.titulo'
# SQLite temporário, se precisar de SQL
sqlite-utils insert radar.db noticias data/*.jsonl --nl
```
