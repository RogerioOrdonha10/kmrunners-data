"""
KM Runners — Atualizador automático de notícias
GNews API -> Airtable (tabela Noticias)

Variáveis de ambiente necessárias (GitHub Secrets):
  GNEWS_API_KEY      - chave da GNews (gnews.io)
  AIRTABLE_TOKEN     - Personal Access Token do Airtable (pat...)
  AIRTABLE_BASE_ID   - ex: appmRv32Vt5S1UfbY
  AIRTABLE_TABLE     - ex: Noticias
"""

import os
import time

import requests
from datetime import datetime, timedelta

GNEWS_API_KEY = os.environ["GNEWS_API_KEY"]
AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE_ID = os.environ["AIRTABLE_BASE_ID"]
AIRTABLE_TABLE = os.environ.get("AIRTABLE_TABLE", "Noticias")

# Temas buscados — configuráveis pelo workflow via env TEMAS (separados por ";")
TEMAS_PADRAO = [
    '"corrida de rua"',
    "maratona OR meia-maratona",
    '"bem-estar" corrida OR treino',
    '"running" OR "pace" treino',
    '"corredor" Brasil OR "São Paulo"',
    '"prova de corrida" OR "circuito de corrida"',
]
TEMAS = [t.strip() for t in os.environ.get("TEMAS", "").split(";") if t.strip()] or TEMAS_PADRAO

MAX_POR_TEMA = 5            # notícias por tema a cada execução
MAX_REGISTROS_TABELA = 30  # teto: mantém a tabela enxuta
DIAS_VALIDADE = 7          # notícia some depois de 7 dias
PISO_MINIMO = 10           # nunca deixa a tabela com menos que isso (evita app vazio)

# Imagem fallback caso a notícia venha sem foto
IMAGEM_PADRAO = "https://images.unsplash.com/photo-1552674605-db6ffd4facb5?w=640"

AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE}"
HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_TOKEN}",
    "Content-Type": "application/json",
}


def buscar_gnews(query: str) -> list:
    """Busca notícias em português/Brasil no GNews."""
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
    print(f"Registros atuais na tabela: {len(existentes)}")

    # --- Busca e insere notícias novas ---
    novos = []
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
            links_existentes.add(link)
            novos.append(
                {
                    "titulo": (a.get("title") or "")[:200],
                    "image": a.get("image") or IMAGEM_PADRAO,
                    "data": (a.get("publishedAt") or "")[:10],
                    "link": link,
                    "fonte": (a.get("source") or {}).get("name", ""),
                }
            )

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
