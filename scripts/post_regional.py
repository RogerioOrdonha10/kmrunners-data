#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera o post regional do KM Runners — as provas de um conjunto de estados
num período à frente. Irmão do robô do fim de semana.

Escreve em posts/:
    regional_<slug>_<AAAA-MM-DD>.png      arte 1080x1350
    regional_<slug>_<AAAA-MM-DD>.txt      legenda pronta para colar

Variáveis de ambiente:
  AIRTABLE_TOKEN            Personal Access Token (pat...)   [obrigatória]
  AIRTABLE_BASE_ID          ex: appmRv32Vt5S1UfbY            [obrigatória]
  AIRTABLE_TABLE_EVENTOS    padrão: Eventos
  ESTADOS                   UFs separadas por vírgula, ex: AL,BA,CE,PE
  TITULO                    rótulo da região, ex: NORDESTE
  DIAS_A_FRENTE             janela em dias, padrão 60
"""

import os
import re
import sys
import unicodedata
from datetime import date, timedelta
from collections import defaultdict

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ----------------------------------------------------------------------------
# Configuração
# ----------------------------------------------------------------------------
AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE_ID = os.environ["AIRTABLE_BASE_ID"]
AIRTABLE_TABLE = os.environ.get("AIRTABLE_TABLE_EVENTOS", "Eventos")

ESTADOS = [u.strip().upper() for u in
           os.environ.get("ESTADOS", "AL,BA,CE,MA,PB,PE,PI,RN,SE").split(",")
           if u.strip()]
TITULO = os.environ.get("TITULO", "NORDESTE").strip().upper()
DIAS_A_FRENTE = int(os.environ.get("DIAS_A_FRENTE", "60"))

AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE}"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_TOKEN}"}

PASTA_SAIDA = "posts"
FONTE_URL = ("https://raw.githubusercontent.com/google/fonts/main/"
             "ofl/montserrat/Montserrat%5Bwght%5D.ttf")
FONTE_LOCAL = "/tmp/Montserrat.ttf"

W, H = 1080, 1350
AZUL = (30, 58, 95)
BRANCO = (255, 255, 255)
CLARO = (150, 190, 235)
LINHA = (44, 74, 112)
OURO = (255, 199, 88)
CIDADE_COR = (122, 160, 202)

MAX_LINHAS = 14
ANO_MIN, ANO_MAX = 2025, 2030

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
         "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


# ----------------------------------------------------------------------------
# Fonte
# ----------------------------------------------------------------------------
def baixar_fonte():
    if os.path.exists(FONTE_LOCAL) and os.path.getsize(FONTE_LOCAL) > 100_000:
        return
    r = requests.get(FONTE_URL, timeout=60)
    r.raise_for_status()
    with open(FONTE_LOCAL, "wb") as f:
        f.write(r.content)


def fonte(peso: str, tamanho: int):
    f = ImageFont.truetype(FONTE_LOCAL, tamanho)
    try:
        f.set_variation_by_name(peso)
    except Exception:
        pass
    return f


RG = lambda t: fonte("Regular", t)
MD = lambda t: fonte("Medium", t)
SB = lambda t: fonte("SemiBold", t)
XB = lambda t: fonte("ExtraBold", t)
BK = lambda t: fonte("Black", t)


# ----------------------------------------------------------------------------
# Airtable e datas
# ----------------------------------------------------------------------------
def listar_eventos() -> list:
    registros, offset = [], None
    while True:
        params = {"pageSize": 100}
        if offset:
            params["offset"] = offset
        r = requests.get(AIRTABLE_URL, headers=HEADERS, params=params, timeout=40)
        r.raise_for_status()
        dados = r.json()
        registros.extend(dados.get("records", []))
        offset = dados.get("offset")
        if not offset:
            break
    return registros


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


def parse_data(valor) -> date | None:
    """Aceita DD/MM/AAAA e AAAA-MM-DD. Devolve None se não der para ler."""
    if not valor:
        return None
    texto = str(valor).strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", texto)
    if m:
        dia, mes, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if ano < 100:
            ano += 2000
        try:
            return date(ano, mes, dia)
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", texto)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


# ----------------------------------------------------------------------------
# Seleção
# ----------------------------------------------------------------------------
def agrupar(registros: list, inicio: date, fim: date) -> dict:
    """Devolve {UF: [item, ...]}. Mesmo nome na mesma data vira um item só."""
    por_chave = defaultdict(lambda: {"cidades": {}, "kms": set()})
    for reg in registros:
        f = reg.get("fields", {})
        uf = (f.get("estado") or "").strip().upper()
        if uf not in ESTADOS:
            continue
        dt = parse_data(f.get("data"))
        if not dt or not (inicio <= dt <= fim):
            continue
        nome = (f.get("nome") or "").strip()
        if not nome:
            continue
        chave = (uf, dt, _norm(nome))
        item = por_chave[chave]
        item["nome"], item["uf"], item["data"] = nome, uf, dt
        cidade = (f.get("cidade") or "").strip()
        if cidade:
            item["cidades"][cidade] = True
        km = f.get("km")
        if km:
            try:
                item["kms"].add(int(round(float(km))))
            except (TypeError, ValueError):
                pass

    por_uf = defaultdict(list)
    for item in por_chave.values():
        por_uf[item["uf"]].append(item)
    for uf in por_uf:
        por_uf[uf].sort(key=lambda i: i["data"])
    return por_uf


def pontuar(item: dict) -> int:
    kms = item["kms"]
    p = 0
    if 42 in kms:
        p += 1000
    if 21 in kms:
        p += 300
    if any(k >= 15 for k in kms):
        p += 120
    if 10 in kms:
        p += 40
    p += 25 * (len(item["cidades"]) - 1)
    return p + (max(kms) if kms else 0)


def texto_cidades(item: dict) -> str:
    cidades = list(item["cidades"].keys())
    if not cidades:
        return ""
    if len(cidades) == 1:
        return cidades[0]
    if len(cidades) <= 3:
        return ", ".join(cidades[:-1]) + " e " + cidades[-1]
    return ", ".join(cidades[:2]) + f" e mais {len(cidades) - 2} cidades"


def texto_kms(item: dict) -> str:
    kms = sorted(item["kms"], reverse=True)[:4]
    return (" · ".join(str(k) for k in kms) + "K") if kms else ""


def escolher(por_uf: dict) -> list:
    """Lista cronológica única. Garante ao menos uma prova por estado presente,
    depois completa pelas mais fortes, até MAX_LINHAS."""
    ufs = [u for u in ESTADOS if por_uf.get(u)]
    if not ufs:
        return []
    escolhidas, ids = [], set()
    for uf in ufs:                                   # cobertura: 1 por estado
        if len(escolhidas) >= MAX_LINHAS:
            break
        melhor = max(por_uf[uf], key=pontuar)
        escolhidas.append(melhor)
        ids.add(id(melhor))
    sobra = sorted((i for uf in ufs for i in por_uf[uf] if id(i) not in ids),
                   key=pontuar, reverse=True)
    escolhidas += sobra[:max(MAX_LINHAS - len(escolhidas), 0)]
    escolhidas.sort(key=lambda i: (i["data"], i["uf"]))
    return escolhidas


# ----------------------------------------------------------------------------
# Arte
# ----------------------------------------------------------------------------
def centro(d, y, txt, f, cor=BRANCO, esp=0):
    if esp == 0:
        d.text(((W - d.textlength(txt, font=f)) / 2, y), txt, font=f, fill=cor)
        return
    larg = sum(d.textlength(c, font=f) + esp for c in txt) - esp
    x = (W - larg) / 2
    for c in txt:
        d.text((x, y), c, font=f, fill=cor)
        x += d.textlength(c, font=f) + esp


def encurtar(d, txt, fo, larg):
    """Corta o texto com reticências para caber na largura disponível."""
    if d.textlength(txt, font=fo) <= larg:
        return txt
    while txt and d.textlength(txt + "…", font=fo) > larg:
        txt = txt[:-1]
    return txt.rstrip() + "…"


def texto_periodo(inicio: date, fim: date) -> str:
    if inicio.month == fim.month:
        return f"{MESES[inicio.month-1].upper()} DE {inicio.year}"
    return (f"{MESES[inicio.month-1].upper()} A "
            f"{MESES[fim.month-1].upper()} DE {fim.year}")


def montar_arte(itens, inicio, fim, total, n_ufs, caminho):
    base = Image.new("RGB", (W, H), AZUL)
    g = Image.new("RGB", (W, H), AZUL)
    ImageDraw.Draw(g).ellipse([-320, -760, W + 320, 400], fill=(46, 84, 130))
    im = Image.blend(base, g.filter(ImageFilter.GaussianBlur(180)), 0.60)
    d = ImageDraw.Draw(im)

    centro(d, 54, f"CORRIDAS NO {TITULO}", BK(56), BRANCO, esp=2)
    centro(d, 128, texto_periodo(inicio, fim), XB(32), OURO)
    centro(d, 174, f"{total} provas em {n_ufs} estados", MD(25), CLARO)
    d.line([(W / 2 - 170, 216), (W / 2 + 170, 216)], fill=OURO, width=4)

    topo, rodape = 250, H - 176
    altura_util = rodape - 24 - topo
    passo = min(58, altura_util // max(len(itens), 1))

    y = topo
    for item in itens:
        d.text((70, y + 2), f"{item['data']:%d/%m}", font=XB(23), fill=OURO)
        km = texto_kms(item)
        tw = d.textlength(km, font=XB(22)) if km else 0
        livre = (W - 70 - tw - 24) - 176
        d.text((176, y - 2), encurtar(d, item["nome"], SB(26), livre),
               font=SB(26), fill=BRANCO)
        cid = texto_cidades(item)
        uf = item["uf"]
        sub = f"{cid}/{uf}" if cid else uf
        d.text((176, y + 26), encurtar(d, sub, RG(19), livre),
               font=RG(19), fill=CIDADE_COR)
        if km:
            d.text((W - 70 - tw, y + 4), km, font=XB(22), fill=BRANCO)
        y += passo
        if y + passo > rodape - 24:
            break

    ry = rodape
    d.rounded_rectangle([64, ry, W - 64, H - 30], 22, fill=BRANCO)
    rod = f"O CALENDÁRIO COMPLETO ESTÁ NO APP"
    centro(d, ry + 18, rod, BK(30), AZUL)
    centro(d, ry + 62, "filtre por cidade, distância e mês — grátis, sem cadastro",
           MD(21), (86, 110, 140))
    centro(d, ry + 100, "KMRUNNERS.COM.BR", XB(28), AZUL)

    im.save(caminho, quality=95)


def montar_legenda(itens, inicio, fim, total, exibidas, ufs_lista):
    cidades = []
    for item in itens:
        for c in item["cidades"]:
            if c not in cidades:
                cidades.append(c)
    restantes = max(total - exibidas, 0)
    ufs = ", ".join(ufs_lista)

    linhas = [
        f"🏃 As corridas de rua do {TITULO.title()} que estão chegando",
        "",
        f"Provas em {ufs} entre {inicio.day}/{inicio.month:02d} e "
        f"{fim.day}/{fim.month:02d}.",
        "",
    ]
    if cidades:
        linhas += ["Tem prova em " + ", ".join(cidades[:6])
                   + (" e mais." if len(cidades) > 6 else "."), ""]
    linhas += [
        "📅 Salve suas provas favoritas no KM Runners. Elas ficam armazenadas"
        " no app e podem ser adicionadas ao seu calendário, para você não perder"
        " nenhuma data importante nem o prazo de inscrição.",
        "",
        "📲 Manda pro seu grupo de treino.",
        "",
        f"São {total} provas na região neste período"
        + (f" — as outras {restantes} estão no app." if restantes else ".")
        + " Filtra por cidade, distância e mês. Grátis, sem cadastro.",
        "",
        "🔗 Link na bio — ou busque KM Runners na sua loja de apps.",
        "",
        "#corridaderua #maratona #meiamaratona #corrida #running #corredores"
        f" #{TITULO.lower()} #calendariodecorridas #10k #21k #42k",
    ]
    return "\n".join(linhas)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    baixar_fonte()
    os.makedirs(PASTA_SAIDA, exist_ok=True)

    registros = listar_eventos()
    print(f"[airtable] {len(registros)} linhas lidas da tabela {AIRTABLE_TABLE}")

    inicio = date.today()
    fim = inicio + timedelta(days=DIAS_A_FRENTE)
    print(f"[janela] {inicio:%d/%m/%Y} a {fim:%d/%m/%Y} · estados {','.join(ESTADOS)}")

    por_uf = agrupar(registros, inicio, fim)
    total = sum(len(v) for v in por_uf.values())
    print(f"[provas] {total} na região, em {len(por_uf)} estados")

    if total == 0:
        print("[provas] nada no período para esses estados — nada a publicar.")
        return

    itens = escolher(por_uf)
    exibidas = len(itens)
    ufs_lista = [u for u in ESTADOS if por_uf.get(u)]
    print(f"[arte] {exibidas} provas exibidas de {total}")

    slug = re.sub(r"[^a-z0-9]+", "-", _norm(TITULO)).strip("-")
    marca = f"{inicio:%Y-%m-%d}"
    png = os.path.join(PASTA_SAIDA, f"regional_{slug}_{marca}.png")
    txt = os.path.join(PASTA_SAIDA, f"regional_{slug}_{marca}.txt")

    montar_arte(itens, inicio, fim, total, len(ufs_lista), png)
    with open(txt, "w", encoding="utf-8") as f:
        f.write(montar_legenda(itens, inicio, fim, total, exibidas, ufs_lista))
    print(f"[arte] {png}")
    print(f"[legenda] {txt}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERRO] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
