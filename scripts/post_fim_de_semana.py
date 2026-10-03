#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera automaticamente a arte do post "provas do fim de semana" do KM Runners.

Lê a tabela Eventos do Airtable, acha o próximo sábado/domingo, seleciona as
provas mais relevantes e escreve em posts/:
    fds_<AAAA-MM-DD>.png      arte 1080x1350 pronta para o Instagram
    fds_<AAAA-MM-DD>.txt      legenda pronta para colar
    fds_<AAAA-MM-DD>_ALERTAS.txt   erros de dado detectados (se houver)

Se o fim de semana tiver menos de MIN_PROVAS provas, a janela é ampliada
automaticamente para os fins de semana seguintes e a arte vira "PRÓXIMAS PROVAS".

Variáveis de ambiente:
  AIRTABLE_TOKEN            Personal Access Token (pat...)   [obrigatória]
  AIRTABLE_BASE_ID          ex: appmRv32Vt5S1UfbY            [obrigatória]
  AIRTABLE_TABLE_EVENTOS    padrão: Eventos
  DIAS_A_FRENTE             padrão: 0 (0 = próximo fim de semana)
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
DIAS_A_FRENTE = int(os.environ.get("DIAS_A_FRENTE", "0"))

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
CARD = (22, 44, 74)
LINHA = (44, 74, 112)
OURO = (255, 199, 88)
CIDADE_COR = (122, 160, 202)

MAX_LINHAS = 13          # total de provas exibidas na arte
MIN_PROVAS = 4           # abaixo disso, amplia a janela
MAX_FINS_DE_SEMANA = 3   # teto da ampliação
ANO_MIN, ANO_MAX = 2025, 2030

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
         "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
MESES_ABREV = ["jan", "fev", "mar", "abr", "mai", "jun",
               "jul", "ago", "set", "out", "nov", "dez"]
DIAS_SEMANA = ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"]


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
    """peso: Regular, Medium, SemiBold, Bold, ExtraBold, Black."""
    f = ImageFont.truetype(FONTE_LOCAL, tamanho)
    try:
        f.set_variation_by_name(peso)
    except Exception:
        pass
    return f


RG = lambda t: fonte("Regular", t)
MD = lambda t: fonte("Medium", t)
SB = lambda t: fonte("SemiBold", t)
BD = lambda t: fonte("Bold", t)
XB = lambda t: fonte("ExtraBold", t)
BK = lambda t: fonte("Black", t)


# ----------------------------------------------------------------------------
# Airtable
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


def parse_data_parcial(valor) -> tuple | None:
    """Reconhece data incompleta mas legítima: 'Agosto / 2027', '08/2027', '2027'.

    Devolve (ano, mes_ou_None). Serve só para NÃO tratar esses casos como erro.
    """
    if not valor:
        return None
    texto = _norm(str(valor)).replace(" de ", " ")
    texto = re.sub(r"\s*/\s*", "/", texto).strip()

    m = re.match(r"^([a-z]+)[/\s]+(\d{4})$", texto)
    if m and m.group(1) in [_norm(x) for x in MESES]:
        return int(m.group(2)), [_norm(x) for x in MESES].index(m.group(1)) + 1
    m = re.match(r"^(\d{1,2})/(\d{4})$", texto)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(2)), int(m.group(1))
    m = re.match(r"^(\d{4})$", texto)
    if m and ANO_MIN <= int(m.group(1)) <= ANO_MAX:
        return int(m.group(1)), None
    return None


def proximo_fim_de_semana(hoje: date) -> tuple:
    """Sábado e domingo da semana corrente/próxima."""
    base = hoje + timedelta(days=DIAS_A_FRENTE)
    # weekday(): segunda=0 ... sábado=5, domingo=6
    dias_ate_sabado = (5 - base.weekday()) % 7
    if base.weekday() == 6:          # se rodar num domingo, pega o fds seguinte
        dias_ate_sabado = 6
    sabado = base + timedelta(days=dias_ate_sabado)
    return sabado, sabado + timedelta(days=1)


# ----------------------------------------------------------------------------
# Agrupamento e seleção
# ----------------------------------------------------------------------------
def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip()


def agrupar(registros: list, dia: date) -> list:
    """Agrupa linhas do mesmo evento. Mesmo nome em várias cidades vira um item."""
    por_nome = defaultdict(lambda: {"cidades": {}, "kms": set()})
    for reg in registros:
        f = reg.get("fields", {})
        if parse_data(f.get("data")) != dia:
            continue
        nome = (f.get("nome") or "").strip()
        if not nome:
            continue
        cidade = (f.get("cidade") or "").strip()
        estado = (f.get("estado") or "").strip()
        km = f.get("km")
        chave = _norm(nome)
        item = por_nome[chave]
        item["nome"] = nome
        if cidade:
            item["cidades"][f"{cidade}/{estado}" if estado else cidade] = True
        if km:
            item["kms"].add(int(round(float(km))))
    return list(por_nome.values())


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
    p += 25 * (len(item["cidades"]) - 1)   # circuito em várias cidades pesa mais
    p += max(kms) if kms else 0
    return p


def texto_cidades(item: dict) -> str:
    cidades = list(item["cidades"].keys())
    if len(cidades) == 1:
        return cidades[0]
    curtas = [c.split("/")[0] for c in cidades]
    if len(curtas) <= 3:
        return ", ".join(curtas[:-1]) + " e " + curtas[-1]
    return ", ".join(curtas[:2]) + f" e mais {len(curtas) - 2} cidades"


def texto_kms(item: dict) -> str:
    kms = sorted(item["kms"], reverse=True)[:4]
    return " · ".join(str(k) for k in kms) + "K"


# ----------------------------------------------------------------------------
# Janela: um fim de semana, ou vários se o primeiro estiver vazio
# ----------------------------------------------------------------------------
def montar_janela(registros: list, sabado: date) -> tuple:
    """Devolve (dias, provas_por_dia, ampliada).

    Começa com sábado+domingo. Se o total ficar abaixo de MIN_PROVAS, inclui
    os fins de semana seguintes, até MAX_FINS_DE_SEMANA.
    """
    dias, por_dia, total = [], {}, 0
    for n in range(MAX_FINS_DE_SEMANA):
        sab = sabado + timedelta(days=7 * n)
        for dia in (sab, sab + timedelta(days=1)):
            provas = sorted(agrupar(registros, dia), key=pontuar, reverse=True)
            dias.append(dia)
            por_dia[dia] = provas
            total += sum(len(i["cidades"]) or 1 for i in provas)
        if total >= MIN_PROVAS:
            return dias, por_dia, n > 0
    return dias, por_dia, True


# ----------------------------------------------------------------------------
# Alertas de qualidade de dado
# ----------------------------------------------------------------------------
def detectar_alertas(registros: list) -> list:
    alertas, parciais = [], defaultdict(int)
    vistos = defaultdict(list)
    for reg in registros:
        f = reg.get("fields", {})
        bruto = f.get("data")
        dt = parse_data(bruto)
        nome = (f.get("nome") or "").strip()
        if bruto and dt is None:
            if parse_data_parcial(bruto):
                parciais[str(bruto).strip()] += 1      # legítimo: mês/ano
            else:
                alertas.append(f"DATA ILEGÍVEL  | {nome} | valor: {bruto!r}")
        elif dt and not (ANO_MIN <= dt.year <= ANO_MAX):
            alertas.append(f"ANO SUSPEITO   | {nome} | {bruto}")
        chave = (_norm(nome), _norm(f.get("cidade")), str(f.get("km")), str(bruto))
        vistos[chave].append(f.get("id") or reg.get("id"))
    for chave, ids in vistos.items():
        if len(ids) > 1 and chave[0]:
            alertas.append(f"DUPLICATA      | {chave[0]} | {chave[1]} | "
                           f"{chave[2]}km | {chave[3]} | ids: {ids}")
    if parciais:
        total = sum(parciais.values())
        alertas.append("")
        alertas.append(f"--- DATAS PARCIAIS (não são erro): {total} registros ---")
        for valor, n in sorted(parciais.items(), key=lambda x: -x[1]):
            alertas.append(f"DATA PARCIAL   | {valor!r} | {n} registros")
    return alertas


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


def texto_periodo(dias: list) -> str:
    ini, fim = dias[0], dias[-1]
    if ini.month == fim.month:
        return f"{ini.day} A {fim.day} DE {MESES[ini.month-1].upper()}"
    return (f"{ini.day} DE {MESES[ini.month-1].upper()} A "
            f"{fim.day} DE {MESES[fim.month-1].upper()}")


def montar_arte(dias, ampliada, destaque, blocos, total, caminho):
    base = Image.new("RGB", (W, H), AZUL)
    g = Image.new("RGB", (W, H), AZUL)
    ImageDraw.Draw(g).ellipse([-320, -760, W + 320, 400], fill=(46, 84, 130))
    im = Image.blend(base, g.filter(ImageFilter.GaussianBlur(180)), 0.60)
    d = ImageDraw.Draw(im)

    if ampliada:
        titulo, periodo = "PRÓXIMAS PROVAS", texto_periodo(dias)
    else:
        sab, dom = dias[0], dias[1]
        titulo = "FIM DE SEMANA"
        if sab.month != dom.month:
            periodo = (f"{sab.day} DE {MESES[sab.month-1].upper()} E "
                       f"{dom.day} DE {MESES[dom.month-1].upper()}")
        else:
            periodo = f"{sab.day} E {dom.day} DE {MESES[sab.month-1].upper()}"

    centro(d, 52, titulo, BK(62), BRANCO, esp=2)
    centro(d, 128, periodo, XB(34), OURO)
    centro(d, 176, "as provas pelo Brasil", MD(26), CLARO)
    d.line([(W / 2 - 170, 218), (W / 2 + 170, 218)], fill=OURO, width=4)

    # espaçamento adaptativo: pouca prova => linhas mais altas, sem buraco
    n_itens = sum(len(p) for _, p in blocos) + (1 if destaque else 0)
    n_blocos = len(blocos)
    altura_util = (H - 176 - 30) - 248
    base_itens = n_itens * 50 + n_blocos * 56
    folga = max(0, altura_util - base_itens - (142 if destaque else 0))
    extra = min(34, folga // max(n_itens, 1)) if n_itens <= 7 else 0
    passo = 50 + extra
    gap_bloco = 12 + extra

    y = 248
    if destaque:
        item, rotulo, dia_txt = destaque
        d.rounded_rectangle([56, y, W - 56, y + 116], 22, fill=CARD)
        d.rounded_rectangle([56, y, 66, y + 116], 5, fill=OURO)
        d.text((96, y + 20), rotulo, font=BK(24), fill=OURO)
        d.text((96, y + 56), item["nome"][:34], font=SB(34), fill=BRANCO)
        d.text((96, y + 94), f"{texto_cidades(item)}  ·  {dia_txt}",
               font=RG(19), fill=CLARO)
        km = texto_kms(item)
        tw = d.textlength(km, font=XB(26))
        d.text((W - 96 - tw, y + 62), km, font=XB(26), fill=OURO)
        y += 116 + 26

    for rotulo_bloco, provas in blocos:
        if not provas:
            continue
        d.text((70, y), rotulo_bloco, font=BK(25), fill=CLARO)
        d.line([(70 + d.textlength(rotulo_bloco, font=BK(25)) + 20, y + 15),
                (W - 70, y + 15)], fill=LINHA, width=2)
        y += 44
        for item in provas:
            d.text((70, y), item["nome"][:36], font=SB(26), fill=BRANCO)
            d.text((70, y + 28), texto_cidades(item), font=RG(19), fill=CIDADE_COR)
            km = texto_kms(item)
            tw = d.textlength(km, font=XB(23))
            d.text((W - 70 - tw, y + 6), km, font=XB(23), fill=BRANCO)
            y += passo
        y += gap_bloco

    ry = H - 176
    d.rounded_rectangle([64, ry, W - 64, H - 30], 22, fill=BRANCO)
    if ampliada:
        rodape = f"SÃO {total} PROVAS NAS PRÓXIMAS SEMANAS"
    else:
        rodape = f"SÃO {total} PROVAS NESTE FIM DE SEMANA"
    centro(d, ry + 16, rodape, BK(30 if len(rodape) <= 34 else 26), AZUL)
    centro(d, ry + 60, "veja todas no app — e as dos próximos meses",
           MD(22), (86, 110, 140))
    centro(d, ry + 96, "KMRUNNERS.COM.BR", XB(28), AZUL)

    im.save(caminho, quality=95)


# ----------------------------------------------------------------------------
# Legenda
# ----------------------------------------------------------------------------
def montar_legenda(dias, ampliada, destaque, blocos, total, exibidas):
    itens = [i for _, provas in blocos for i in provas]
    cidades = []
    for item in itens:
        for c in item["cidades"]:
            nome_cidade = c.split("/")[0]
            if nome_cidade not in cidades:
                cidades.append(nome_cidade)
    mostradas = sum(len(i["cidades"]) or 1 for i in itens)
    if destaque:
        mostradas += len(destaque[0]["cidades"]) or 1
    restantes = max(total - mostradas, 0)

    ini, fim = dias[0], dias[-1]
    if ampliada:
        cabecalho = (f"🏃 As próximas provas pelo Brasil — de {ini.day}/{ini.month:02d} "
                     f"a {fim.day}/{fim.month:02d}")
    else:
        cabecalho = (f"🏃 As provas deste fim de semana pelo Brasil — "
                     f"{ini.day} e {fim.day} de {MESES[fim.month-1]}")

    linhas = [cabecalho, ""]
    if destaque:
        item, rotulo, _ = destaque
        linhas.append(f"Destaque para a {item['nome']} — {rotulo.lower()}.")
        linhas.append("")
    if cidades:
        linhas.append("Tem prova em " + ", ".join(cidades[:6]) + ".")
        linhas.append("")
    linhas += [
        "📅 Salve suas provas favoritas no KM Runners. Elas ficam armazenadas"
        " no app e podem ser adicionadas ao seu calendário, para você não perder"
        " nenhuma data importante nem o prazo de inscrição.",
        "",
        "📲 Manda pro seu grupo de treino.",
        "",
        f"São {total} provas no total"
        + (f" — as outras {restantes} estão no app," if restantes else ",")
        + " junto com 1.300+ provas de todo o Brasil."
        " Filtra por cidade, distância e mês. Grátis, sem cadastro.",
        "",
        "🔗 kmrunners.com.br",
        "",
        "#corridaderua #maratona #meiamaratona #corrida #running #corredores"
        " #fimdesemana #calendariodecorridas #10k #21k #42k",
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

    sabado, domingo = proximo_fim_de_semana(date.today())
    print(f"[fds] sábado {sabado:%d/%m/%Y} · domingo {domingo:%d/%m/%Y}")

    dias, por_dia, ampliada = montar_janela(registros, sabado)
    dias = [dia for dia in dias if por_dia[dia]] or dias[:2]
    total = sum(sum(len(i["cidades"]) or 1 for i in por_dia[dia]) for dia in dias)
    print(f"[fds] janela {dias[0]:%d/%m} a {dias[-1]:%d/%m} · "
          f"{total} provas · ampliada={ampliada}")

    if total == 0:
        print("[fds] nenhuma prova na janela — nada a publicar.")
        return

    # destaque: a prova mais forte da janela
    destaque = None
    candidatos = [(i, dia) for dia in dias for i in por_dia[dia]]
    if candidatos:
        melhor, dia_melhor = max(candidatos, key=lambda x: pontuar(x[0]))
        rotulo = ("A MARATONA DA SEMANA" if 42 in melhor["kms"]
                  else "DESTAQUE DO FIM DE SEMANA")
        if ampliada:
            dia_txt = f"{DIAS_SEMANA[dia_melhor.weekday()].lower()} {dia_melhor:%d/%m}"
        else:
            dia_txt = "sábado" if dia_melhor.weekday() == 5 else "domingo"
        destaque = (melhor, rotulo, dia_txt)
        por_dia[dia_melhor] = [i for i in por_dia[dia_melhor] if i is not melhor]

    # distribui as linhas disponíveis entre os dias, começando pelos mais cheios
    restante = MAX_LINHAS
    escolhidas = {}
    for dia in sorted(dias, key=lambda x: -len(por_dia[x])):
        cota = max(1, round(MAX_LINHAS * len(por_dia[dia]) /
                            max(sum(len(por_dia[x]) for x in dias), 1)))
        escolhidas[dia] = por_dia[dia][:min(cota, restante)]
        restante -= len(escolhidas[dia])

    blocos = []
    for dia in dias:
        provas = escolhidas.get(dia) or []
        if not provas:
            continue
        rotulo_bloco = f"{DIAS_SEMANA[dia.weekday()]}  {dia:%d/%m}"
        blocos.append((rotulo_bloco, provas))

    exibidas = sum(len(p) for _, p in blocos) + (1 if destaque else 0)

    marca = f"{dias[0]:%Y-%m-%d}"
    png = os.path.join(PASTA_SAIDA, f"fds_{marca}.png")
    txt = os.path.join(PASTA_SAIDA, f"fds_{marca}.txt")

    montar_arte(dias, ampliada, destaque, blocos, total, png)
    with open(txt, "w", encoding="utf-8") as f:
        f.write(montar_legenda(dias, ampliada, destaque, blocos, total, exibidas))
    print(f"[arte] {png}")
    print(f"[legenda] {txt}")

    alertas = detectar_alertas(registros)
    if alertas:
        cam = os.path.join(PASTA_SAIDA, f"fds_{marca}_ALERTAS.txt")
        with open(cam, "w", encoding="utf-8") as f:
            f.write("\n".join(alertas))
        reais = sum(1 for a in alertas if a.startswith(("DATA ILEGÍVEL",
                                                        "ANO SUSPEITO",
                                                        "DUPLICATA")))
        print(f"[alertas] {reais} problemas reais de dado -> {cam}")
    else:
        print("[alertas] nenhum problema de dado detectado")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERRO] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
