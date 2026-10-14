"""Radar de Notícias: coleta RSS, filtra por regras e grava JSONL mensal.

Uso:  python collector.py            (coleta todas as fontes ativas)
      python collector.py --dry-run  (mostra o que entraria, sem gravar)

Saídas:
  data/AAAA-MM.jsonl   base permanente, uma notícia por linha, sem duplicatas
  recentes.md          últimas 48h em markdown (lido pelo briefing diário)
  recentes.json        mesmo conteúdo em JSON
  status.json          resultado da última coleta por fonte
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UA = "Mozilla/5.0 (compatible; radar-noticias/1.0; +https://github.com)"
JANELA_RECENTES_H = 48
MAX_IDADE_DIAS = 3          # ignora itens mais velhos que isso (feeds com lixo antigo)
RESUMO_MAX = 300


# ---------- utilitários ----------
def normalizar(txt: str) -> str:
    txt = unicodedata.normalize("NFKD", txt or "")
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", txt.lower()).strip()


def limpar_html(txt: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", txt or "")
    return re.sub(r"\s+", " ", html.unescape(txt)).strip()


def link_canonico(url: str) -> str:
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))


def id_item(url: str) -> str:
    return hashlib.sha1(link_canonico(url).encode()).hexdigest()[:12]


def compilar(termo: str) -> re.Pattern:
    t = normalizar(termo)
    prefixo = t.endswith("*")
    t = re.escape(t).replace(r"\*", r"\w*")   # "*" vira "qualquer continuação da palavra"
    fim = r"" if prefixo else r"(?![\w])"
    return re.compile(r"(?<![\w])" + t + fim)


# ---------- regras ----------
class Regras:
    def __init__(self, cfg: dict):
        self.minimo = cfg.get("pontuacao_minima", 2)
        self.descartar = [compilar(t) for t in cfg.get("descartar", [])]
        self.temas = {
            nome: (t.get("peso", 1), [compilar(x) for x in t.get("termos", [])])
            for nome, t in cfg.get("temas", {}).items()
        }

    def avaliar(self, texto: str, bonus: int = 0) -> tuple[int, list[str]] | None:
        n = normalizar(texto)
        if any(p.search(n) for p in self.descartar):
            return None
        temas = [nome for nome, (_, pats) in self.temas.items() if any(p.search(n) for p in pats)]
        score = sum(self.temas[t][0] for t in temas) + (bonus if temas else 0)
        return (score, temas) if score >= self.minimo else None


# ---------- armazenamento ----------
def arquivo_mes(dt: datetime) -> Path:
    return DATA / f"{dt:%Y-%m}.jsonl"


def carregar(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def ids_existentes(agora: datetime) -> set[str]:
    anterior = (agora.replace(day=1) - timedelta(days=1))
    ids = set()
    for p in {arquivo_mes(agora), arquivo_mes(anterior)}:
        ids.update(i["id"] for i in carregar(p))
    return ids


def data_item(e) -> datetime | None:
    for k in ("published_parsed", "updated_parsed"):
        t = e.get(k)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


# ---------- coleta ----------
def coletar(dry_run: bool = False) -> int:
    fontes = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))["fontes"]
    regras = Regras(yaml.safe_load((ROOT / "rules.yaml").read_text(encoding="utf-8")))
    agora = datetime.now(timezone.utc)
    vistos = ids_existentes(agora)
    novos: list[dict] = []
    status = {"coleta_utc": agora.isoformat(timespec="seconds"), "fontes": {}}

    for f in fontes:
        if not f.get("ativo", True):
            continue
        st = {"ok": False, "itens_feed": 0, "aceitos": 0, "item_mais_recente": None, "erro": None}
        try:
            feed = feedparser.parse(f["url"], agent=UA, request_headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"})
            if feed.bozo and not feed.entries:
                raise RuntimeError(str(feed.get("bozo_exception", "feed inválido"))[:200])
            st["ok"] = True
            st["itens_feed"] = len(feed.entries)
            datas = [d for d in (data_item(e) for e in feed.entries) if d]
            if datas:
                st["item_mais_recente"] = max(datas).isoformat(timespec="seconds")
            for e in feed.entries:
                link = e.get("link")
                if not link:
                    continue
                iid = id_item(link)
                if iid in vistos:
                    continue
                dt = data_item(e) or agora
                if agora - dt > timedelta(days=MAX_IDADE_DIAS):
                    continue
                titulo = limpar_html(e.get("title", ""))
                resumo = limpar_html(e.get("summary", ""))[:RESUMO_MAX]
                r = regras.avaliar(f"{titulo} {resumo}", f.get("bonus", 0))
                if not r:
                    continue
                vistos.add(iid)
                novos.append({
                    "id": iid, "data": dt.isoformat(timespec="seconds"), "fonte": f["id"],
                    "titulo": titulo, "resumo": resumo, "link": link,
                    "temas": r[1], "score": r[0],
                    "coletado": agora.isoformat(timespec="seconds"),
                })
                st["aceitos"] += 1
        except Exception as ex:  # uma fonte com problema não derruba as outras
            st["erro"] = f"{type(ex).__name__}: {ex}"[:300]
        status["fontes"][f["id"]] = st
        print(f"[{f['id']}] ok={st['ok']} feed={st['itens_feed']} aceitos={st['aceitos']} {st['erro'] or ''}")

    if dry_run:
        for n in sorted(novos, key=lambda x: -x["score"]):
            print(f"  {n['score']:>2} {n['fonte']:<12} {n['titulo'][:90]}  {n['temas']}")
        return len(novos)

    DATA.mkdir(exist_ok=True)
    for n in sorted(novos, key=lambda x: x["data"]):
        p = arquivo_mes(datetime.fromisoformat(n["data"]))
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(n, ensure_ascii=False) + "\n")

    gerar_recentes(agora)
    (ROOT / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"novos: {len(novos)}")
    return len(novos)


def gerar_recentes(agora: datetime) -> None:
    corte = agora - timedelta(hours=JANELA_RECENTES_H)
    anterior = agora.replace(day=1) - timedelta(days=1)
    itens = [i for p in {arquivo_mes(agora), arquivo_mes(anterior)} for i in carregar(p)
             if datetime.fromisoformat(i["data"]) >= corte]
    itens.sort(key=lambda i: i["data"], reverse=True)
    (ROOT / "recentes.json").write_text(json.dumps(itens, ensure_ascii=False, indent=1), encoding="utf-8")

    linhas = [f"# Radar de notícias: últimas {JANELA_RECENTES_H}h",
              f"Atualizado em {agora:%Y-%m-%d %H:%M} UTC. {len(itens)} itens. Formato: data UTC | fonte | score | temas.", ""]
    for i in itens:
        d = datetime.fromisoformat(i["data"]).strftime("%m-%d %H:%M")
        linhas.append(f"- {d} | {i['fonte']} | {i['score']} | {', '.join(i['temas'])}  ")
        linhas.append(f"  [{i['titulo']}]({i['link']})")
        if i["resumo"]:
            linhas.append(f"  {i['resumo']}")
    (ROOT / "recentes.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    coletar(a.dry_run)
    sys.exit(0)
