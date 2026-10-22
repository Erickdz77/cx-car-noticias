#!/usr/bin/env python3
"""CX Car Notícias - atualiza o noticias.json lendo feeds RSS/Atom de portais.

Uso:  python3 atualizar_noticias.py
Config: fontes.json (lista de portais, limites e opções).
Guarda só: título, resumo curto (do próprio feed), link da matéria original,
fonte e data. Nunca copia a matéria inteira.
"""
import json, re, html, sys, urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
import xml.etree.ElementTree as ET

BASE = Path(__file__).parent
UA = "CXCarNoticiasBot/1.0 (leitor de feeds RSS publicos)"


def limpar(txt, limite):
    txt = re.sub(r"<[^>]+>", " ", txt or "")
    txt = html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    if len(txt) > limite:
        txt = txt[:limite].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return txt


def baixar(url):
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("endereço precisa começar com http:// ou https://")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(5_000_000)


def nome(e):
    return e.tag.rsplit("}", 1)[-1] if isinstance(e.tag, str) else ""


def achar(e, *nomes):
    for c in e:
        if nome(c) in nomes:
            return c
    return None


def texto(e, *nomes):
    c = achar(e, *nomes)
    return (c.text or "").strip() if c is not None else ""


def data_de(txt):
    if not txt:
        return None
    try:
        d = parsedate_to_datetime(txt)
    except Exception:
        try:
            d = datetime.fromisoformat(txt.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def link_de(e):
    achado = ""
    for c in e:
        if nome(c) == "link":
            if c.get("href"):
                if c.get("rel", "alternate") == "alternate":
                    return c.get("href").strip()
                achado = achado or c.get("href").strip()
            elif c.text and c.text.strip():
                return c.text.strip()
    return achado


def imagem_de(e):
    for c in e.iter():
        n = nome(c)
        u = c.get("url")
        if not u:
            continue
        if n in ("thumbnail",) or (n == "content" and c.get("medium", "image") == "image") \
           or (n == "enclosure" and (c.get("type") or "").startswith("image")):
            if u.startswith("https://"):
                return u
    return ""


def ler_feed(dados, fonte, cfg):
    raiz = ET.fromstring(dados)
    itens = [e for e in raiz.iter() if nome(e) in ("item", "entry")]
    saida = []
    for e in itens:
        titulo = limpar(texto(e, "title"), 160)
        link = link_de(e)
        if not titulo or not link.lower().startswith(("http://", "https://")):
            continue
        d = data_de(texto(e, "pubDate", "published", "updated", "date")) or datetime.now(timezone.utc)
        n = {
            "categoria": fonte.get("categoria", "Notícias"),
            "titulo": titulo,
            "resumo": limpar(texto(e, "description", "summary"), cfg.get("tamanho_resumo", 180)),
            "fonte": fonte["nome"],
            "link": link,
            "data": d.isoformat(),
        }
        if cfg.get("usar_imagens_da_fonte", False):
            img = imagem_de(e)
            if img:
                n["imagem"] = img
        saida.append((d, n))
    saida.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in saida[: cfg.get("max_por_fonte", 6)]]


def principal():
    cfg = json.loads((BASE / "fontes.json").read_text(encoding="utf-8"))
    corte = datetime.now(timezone.utc) - timedelta(days=cfg.get("dias", 10))
    todas, vistos = [], set()
    for f in cfg["fontes"]:
        if not f.get("ativo", True):
            continue
        try:
            novas = ler_feed(baixar(f["url"]), f, cfg)
            print(f"OK      {f['nome']}: {len(novas)} notícias")
            for n in novas:
                if n["link"] not in vistos and datetime.fromisoformat(n["data"]) >= corte:
                    vistos.add(n["link"])
                    todas.append(n)
        except Exception as erro:
            print(f"FALHOU  {f['nome']}: {erro}")
    if not todas:
        print("Nenhuma notícia obtida. O noticias.json atual foi mantido.")
        return 0
    todas.sort(key=lambda n: n["data"], reverse=True)
    todas = todas[: cfg.get("max_total", 30)]
    (BASE / "noticias.json").write_text(
        json.dumps({"noticias": todas}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"noticias.json atualizado com {len(todas)} notícias.")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
