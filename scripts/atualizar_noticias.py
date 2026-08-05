"""
KM Runners — Atualizador automático de notícias
GNews API -> Airtable (tabela Noticias / Tenis)

Variáveis de ambiente necessárias (GitHub Secrets):
  GNEWS_API_KEY      - chave da GNews (gnews.io)
  AIRTABLE_TOKEN     - Personal Access Token do Airtable (pat...)
  AIRTABLE_BASE_ID   - ex: appmRv32Vt5S1UfbY
  AIRTABLE_TABLE     - ex: Noticias

Variáveis opcionais (definidas por workflow; usam padrão se ausentes):
  TEMAS                 - temas separados por ";"
  MAX_POR_TEMA          - notícias por tema por execução (padrão 10)
  MAX_REGISTROS_TABELA  - teto de registros na tabela (padrão 30)
  DIAS_VALIDADE         - dias até a notícia expirar (padrão 7)
  PISO_MINIMO           - mínimo sempre mantido na tabela (padrão 10)
  SIMILARIDADE_DEDUP    - fração de palavras iguais p/ considerar repetida (padrão 0.50)
"""

import os
import re
import time
import unicodedata

import requests
from datetime import datetime, timedelta

GNEWS_API_KEY = os.environ["GNEWS_API_KEY"]
AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE_ID = os.environ["AIRTABLE_BASE_ID"]
AIRTABLE_TABLE = os.environ.get("AIRTABLE_TABLE", "Noticias")

# Temas buscados — configuráveis pelo workflow via env TEMAS (separados por ";")
# IMPORTANTE: no plano free do GNews, use parênteses ao redor de grupos com OR:
#   BOM:  (maratona OR "meia-maratona")
#   RUIM: maratona OR meia-maratona   <- pode dar 400 Bad Request
TEMAS_PADRAO = [
    '"corrida de rua"',
    '(maratona OR "meia-maratona")',
    '"corrida" (treino OR "bem-estar" OR preparo)',
    '("running" OR "pace") treino',
    '"corredor" (corrida OR maratona OR atletismo OR prova)',
    '("prova de corrida" OR "circuito de corrida")',
]
TEMAS = [t.strip() for t in os.environ.get("TEMAS", "").split(";") if t.strip()] or TEMAS_PADRAO

# Parâmetros configuráveis por env (com padrões seguros)
MAX_POR_TEMA = int(os.environ.get("MAX_POR_TEMA", "10"))
MAX_REGISTROS_TABELA = int(os.environ.get("MAX_REGISTROS_TABELA", "30"))
DIAS_VALIDADE = int(os.environ.get("DIAS_VALIDADE", "7"))
PISO_MINIMO = int(os.environ.get("PISO_MINIMO", "10"))
SIMILARIDADE_DEDUP = float(os.environ.get("SIMILARIDADE_DEDUP", "0.50"))

# Imagem fallback caso a notícia venha sem foto
IMAGEM_PADRAO = "https://images.unsplash.com/photo-1552674605-db6ffd4facb5?w=640"

# --- Blacklist de termos trágicos/policiais (descarta a notícia) ---
# Dividida em duas: termos SEMPRE bloqueados, e AMBÍGUOS que só bloqueiam
# quando NÃO há contexto de tênis/promoção (ex.: "preço caiu 40%" é oferta, não tragédia).
BLACKLIST_FORTE = {
    "morre", "morreu", "morte", "morto", "morta", "mortos", "mortas",
    "faleceu", "obito", "cadaver", "suicidio", "suicida", "se lancou",
    "se jogou", "assassinato", "assassinado", "assassinada", "homicidio",
    "esfaqueado", "esfaqueada", "baleado", "baleada", "tiro", "tiros",
    "tiroteio", "crime", "criminoso", "preso", "presa", "prisao", "estupro",
    "abuso", "sequestro", "afogamento", "afogou", "violencia", "agressao",
    "espancado", "espancada", "feminicidio", "delegacia", "atropelado",
    "atropelada", "atropelamento",
}
# Ambíguos: aparecem tanto em tragédia ("atleta caiu") quanto em promoção
# ("preço caiu"). Só bloqueiam se NÃO houver palavra de contexto seguro.
BLACKLIST_AMBIGUA = {
    "caiu", "cair", "queda", "despencou", "acidente", "incendio",
}
# Se o título tiver alguma destas, os termos AMBÍGUOS são liberados.
CONTEXTO_SEGURO = {
    "tenis", "tenis", "desconto", "off", "oferta", "ofertas", "promocao",
    "promocoes", "review", "lancamento", "cupom", "preco", "precos",
    "black", "friday", "comprar", "loja", "modelo", "modelos",
}

# Palavras vazias (stopwords) ignoradas na comparação de similaridade de títulos
STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "do", "da", "dos",
    "das", "e", "ou", "no", "na", "nos", "nas", "em", "para", "por", "com",
    "sem", "que", "se", "ao", "aos", "as", "à", "às", "the", "of", "in", "on",
    "apos", "sobre", "entre", "ate", "pela", "pelo", "pelas", "pelos", "seu",
    "sua", "seus", "suas", "este", "esta", "esse", "essa", "isso", "mais",
    "mil", "reune", "tem", "neste", "nesta", "domingo", "sabado", "hoje",
    "ano", "anos", "2025", "2026",
}


def _normalizar(texto: str) -> str:
    """Minúsculo e sem acento."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.lower()


def contem_termo_bloqueado(*campos) -> bool:
    """True se algum campo tiver termo da blacklist.
    Termos FORTES sempre bloqueiam. Termos AMBÍGUOS (caiu/queda/...) são
    liberados quando o texto tem contexto de tênis/promoção."""
    palavras = set(_normalizar(" ".join(c for c in campos if c)).split())
    if palavras & BLACKLIST_FORTE:
        return True
    ambiguos = palavras & BLACKLIST_AMBIGUA
    if ambiguos and not (palavras & CONTEXTO_SEGURO):
        return True
    return False


def _palavras_chave(titulo: str) -> set:
    """Conjunto de palavras significativas do título (sem acento, sem stopword,
    só termos com 3+ letras)."""
    texto = _normalizar(titulo)
    texto = re.sub(r"[^a-z0-9\s]", " ", texto)  # remove pontuação
    return {p for p in texto.split() if len(p) >= 3 and p not in STOPWORDS}


def eh_repetida(titulo: str, titulos_aceitos: list) -> bool:
    """True se o título for muito parecido com algum já aceito.
    Usa índice de Jaccard: |interseção| / |união| das palavras-chave."""
    novo = _palavras_chave(titulo)
    if not novo:
        return False
    for existente in titulos_aceitos:
        if not existente:
            continue
        inter = novo & existente
        uniao = novo | existente
        if uniao and len(inter) / len(uniao) >= SIMILARIDADE_DEDUP:
            return True
    return False


AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE}"
HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_TOKEN}",
    "Content-Type": "application/json",
}


def buscar_gnews(query: str) -> list:
    """Busca notícias em português/Brasil no GNews.
    Loga corpo do erro quando o GNews devolve 4xx (ajuda a achar query inválida)."""
    url = "https://gnews.io/api/v4/search"
    params = {
        "q": query,
        "lang": "pt",
        "country": "br",
        "max": MAX_POR_TEMA,
        "sortby": "publishedAt",
        "apikey": GNEWS_API_KEY,
    }
    r = requests.get(url, params=params, timeout=30)
    if r.status_code != 200:
        # Mostra o motivo do GNews (ex.: query malformada) em vez de erro genérico
        print(f"[GNEWS {r.status_code}] tema={query!r} -> {r.text[:200]}")
        r.raise_for_status()
    return r.json().get("articles", [])


def listar_registros_airtable() -> list:
    """Lista todos os registros atuais (id, link, data) para deduplicar/limpar."""
    registros = []
    params = {"pageSize": 100}
    while True:
        r = requests.get(AIRTABLE_URL, headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()
        dados = r.json()
        registros.extend(dados.get("records", []))
        offset = dados.get("offset")
        if not offset:
            break
        params["offset"] = offset
    return registros


def criar_registros(novos: list):
    """Cria registros em lotes de 10 (limite do Airtable)."""
    for i in range(0, len(novos), 10):
        lote = novos[i : i + 10]
        payload = {"records": [{"fields": f} for f in lote]}
        r = requests.post(AIRTABLE_URL, headers=HEADERS, json=payload, timeout=30)
        r.raise_for_status()


def apagar_registros(ids: list):
    """Apaga registros em lotes de 10."""
    for i in range(0, len(ids), 10):
        lote = ids[i : i + 10]
        params = [("records[]", rid) for rid in lote]
        r = requests.delete(AIRTABLE_URL, headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()


def _data_registro(reg: dict):
    """Retorna a data (date) do registro, ou None se inválida/vazia."""
    data_str = (reg.get("fields", {}).get("data", "") or "")[:10]
    try:
        return datetime.strptime(data_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def main():
    existentes = listar_registros_airtable()
    links_existentes = {
        reg.get("fields", {}).get("link", "") for reg in existentes
    }
    # Títulos já na tabela viram conjuntos de palavras-chave p/ dedup por similaridade
    titulos_aceitos = [
        _palavras_chave(reg.get("fields", {}).get("titulo", "")) for reg in existentes
    ]
    print(f"Registros atuais na tabela: {len(existentes)}")

    # --- Busca e insere notícias novas ---
    novos = []
    bloqueadas = 0
    repetidas = 0
    for tema in TEMAS:
        time.sleep(2)  # respeita o limite de 1 req/s do GNews free
        try:
            artigos = buscar_gnews(tema)
        except Exception as e:
            print(f"[AVISO] Falha ao buscar tema {tema}: {e}")
            continue
        for a in artigos:
            link = a.get("url", "")
            if not link or link in links_existentes:
                continue
            titulo = a.get("title") or ""
            descricao = a.get("description") or ""
            # 1) descarta notícia trágica/policial
            if contem_termo_bloqueado(titulo, descricao):
                bloqueadas += 1
                print(f"[BLOQUEADA] {titulo[:70]}")
                continue
            # 2) descarta se for a mesma notícia de outra fonte (título parecido)
            if eh_repetida(titulo, titulos_aceitos):
                repetidas += 1
                print(f"[REPETIDA]  {titulo[:70]}")
                continue
            links_existentes.add(link)
            titulos_aceitos.append(_palavras_chave(titulo))
            novos.append(
                {
                    "titulo": titulo[:200],
                    "image": a.get("image") or IMAGEM_PADRAO,
                    "data": (a.get("publishedAt") or "")[:10],
                    "link": link,
                    "fonte": (a.get("source") or {}).get("name", ""),
                }
            )

    if bloqueadas:
        print(f"Total bloqueadas pela blacklist: {bloqueadas}")
    if repetidas:
        print(f"Total ignoradas por serem repetidas: {repetidas}")

    if novos:
        criar_registros(novos)
        print(f"Inseridas {len(novos)} notícias novas.")
    else:
        print("Nenhuma notícia nova.")

    # --- Limpeza: por IDADE (com piso mínimo) + teto de quantidade ---
    todos = listar_registros_airtable()
    hoje = datetime.now().date()
    limite = hoje - timedelta(days=DIAS_VALIDADE)

    # Ordena do mais novo para o mais antigo (sem data vai pro fim)
    todos_ord = sorted(
        todos,
        key=lambda r: _data_registro(r) or datetime.min.date(),
        reverse=True,
    )

    ids_apagar = set()

    # 1) Marca por idade — mas protege o piso mínimo (as N mais novas nunca saem)
    protegidos = {r["id"] for r in todos_ord[:PISO_MINIMO]}
    for r in todos_ord:
        if r["id"] in protegidos:
            continue
        data_pub = _data_registro(r)
        if data_pub is None or data_pub < limite:
            ids_apagar.add(r["id"])

    # 2) Teto de quantidade — entre os que sobraram, mantém só os MAX mais recentes
    restantes = [r for r in todos_ord if r["id"] not in ids_apagar]
    if len(restantes) > MAX_REGISTROS_TABELA:
        ids_apagar.update(r["id"] for r in restantes[MAX_REGISTROS_TABELA:])

    if ids_apagar:
        apagar_registros(list(ids_apagar))
        print(f"Removidos {len(ids_apagar)} registros (idade + teto).")
    else:
        print("Nada a remover.")


if __name__ == "__main__":
    main()
