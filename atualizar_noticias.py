#!/usr/bin/env python3
"""CX Car Notícias - atualiza o noticias.json lendo feeds RSS/Atom de portais.

Uso:  python3 atualizar_noticias.py
Config: fontes.json (lista de portais, limites e opções).
Guarda só: título, resumo curto (do próprio feed), link da matéria original,
fonte e data. Nunca copia a matéria inteira.
"""
import json, re, html, sys, os, hashlib, urllib.request, urllib.parse
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


def buscar_fotos(consulta, chave):
    """Busca fotos de licença livre no Pexels (precisa de chave gratuita)."""
    url = ("https://api.pexels.com/v1/search?query=" + urllib.parse.quote(consulta)
           + "&per_page=30&orientation=landscape")
    req = urllib.request.Request(url, headers={"Authorization": chave, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        dados = json.loads(r.read(3_000_000))
    fotos = []
    for f in dados.get("photos", []):
        img = (f.get("src") or {}).get("landscape", "")
        if img.startswith("https://images.pexels.com/"):
            fotos.append({"imagem": img, "credito": f.get("photographer", "Pexels"),
                          "credito_link": f.get("url", "https://www.pexels.com"),
                          "fonte_foto": "Pexels", "fonte_foto_link": "https://www.pexels.com"})
    return fotos


UTM = "?utm_source=cx_car_noticias&utm_medium=referral"


def buscar_fotos_unsplash(consulta, chave):
    """Busca fotos no Unsplash (chave gratuita). Usa o link original da foto e guarda o crédito."""
    url = ("https://api.unsplash.com/search/photos?query=" + urllib.parse.quote(consulta)
           + "&per_page=30&orientation=landscape")
    req = urllib.request.Request(url, headers={"Authorization": "Client-ID " + chave,
                                               "Accept-Version": "v1", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        dados = json.loads(r.read(3_000_000))
    fotos = []
    for f in dados.get("results", []):
        img = (f.get("urls") or {}).get("regular", "")
        if not img.startswith("https://images.unsplash.com/"):
            continue
        u = f.get("user") or {}
        perfil = (u.get("links") or {}).get("html", "https://unsplash.com")
        dl = (f.get("links") or {}).get("download_location", "")
        fotos.append({"imagem": img, "credito": u.get("name", "Unsplash"),
                      "credito_link": perfil + UTM,
                      "fonte_foto": "Unsplash", "fonte_foto_link": "https://unsplash.com/" + UTM,
                      "dl": dl if dl.startswith("https://api.unsplash.com/") else ""})
    return fotos


def disparar_download(f, chave):
    """O Unsplash pede que se avise quando uma foto é usada (regra da API)."""
    if not f.get("dl"):
        return
    try:
        req = urllib.request.Request(f["dl"], headers={"Authorization": "Client-ID " + chave, "User-Agent": UA})
        urllib.request.urlopen(req, timeout=20).read(100_000)
    except Exception:
        pass


def aplicar_fotos(noticias, conf, antigas):
    # mantém a mesma foto de uma notícia entre uma rodada e outra
    for n in noticias:
        o = antigas.get(n["link"])
        if o and "imagem" not in n:
            n.update({k: o[k] for k in ("imagem", "credito", "credito_link", "fonte_foto", "fonte_foto_link") if k in o})
    prov = (conf or {}).get("provedor", "unsplash")
    chave = os.environ.get("PEXELS_API_KEY" if prov == "pexels" else "UNSPLASH_ACCESS_KEY", "").strip()
    if not chave or not conf:
        return
    busca = buscar_fotos if prov == "pexels" else buscar_fotos_unsplash
    cache, usadas = {}, {n["imagem"] for n in noticias if n.get("imagem")}
    for n in noticias:
        if n.get("imagem"):
            continue
        consulta = conf.get("consultas", {}).get(n["categoria"], conf.get("padrao", "sports car"))
        if consulta not in cache:
            try:
                cache[consulta] = busca(consulta, chave)
                print(f"OK      fotos '{consulta}': {len(cache[consulta])}")
            except Exception as erro:
                print(f"FALHOU  fotos '{consulta}': {erro}")
                cache[consulta] = []
        fotos = cache[consulta]
        if not fotos:
            continue
        i = int(hashlib.md5(n["link"].encode()).hexdigest(), 16) % len(fotos)
        for k in range(len(fotos)):
            f = fotos[(i + k) % len(fotos)]
            if f["imagem"] not in usadas:
                break
        usadas.add(f["imagem"])
        disparar_download(f, chave)
        n.update({k: v for k, v in f.items() if k != "dl"})


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
    antigas = {}
    try:
        antigo = json.loads((BASE / "noticias.json").read_text(encoding="utf-8"))
        antigas = {n["link"]: n for n in antigo.get("noticias", []) if n.get("imagem") and n.get("credito")}
    except Exception:
        pass
    aplicar_fotos(todas, cfg.get("fotos"), antigas)
    (BASE / "noticias.json").write_text(
        json.dumps({"noticias": todas}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"noticias.json atualizado com {len(todas)} notícias.")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
