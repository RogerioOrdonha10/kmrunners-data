"""
KM Runners — Robô de manutenção de Eventos
Roda 1x por semana e:
  • Identifica eventos com data anterior a hoje
  • Apaga a data (campo data fica vazio)
  • Garante que o campo 'mes' está preenchido (usa o mês da data antiga)
  • Marca 'revisar' = true para você confirmar/reagendar na próxima edição
  • Gera cidades.json (fonte do dropdown dinâmico de cidades da BuscaPage)
  • Mantém intactos: nome, km, cidade, estado, zona, organizador, link
Variáveis de ambiente (GitHub Secrets):
  AIRTABLE_TOKEN     - Personal Access Token com escopo data.records:read + write
  AIRTABLE_BASE_ID   - ex: appmRv32Vt5S1UfbY
  AIRTABLE_TABLE     - nome da tabela de eventos (ex: Eventos)
"""
import os
import json
import unicodedata
import requests
from datetime import datetime, date

AIRTABLE_TOKEN = os.environ["AIRTABLE_TOKEN"]
AIRTABLE_BASE_ID = os.environ["AIRTABLE_BASE_ID"]
AIRTABLE_TABLE = os.environ.get("AIRTABLE_TABLE", "Eventos")

# Nomes EXATOS das colunas no Airtable (ajuste se forem diferentes)
COL_DATA = "data"
COL_MES = "mes"
COL_REVISAR = "revisar"
COL_CIDADE = "cidade"

# Mapeamento mês → label que vai para a coluna 'mes'
# Mantemos consistência com o dropdown do Flutter (português, capitalizado)
NOMES_MESES = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE}"
HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_TOKEN}",
    "Content-Type": "application/json",
}


def listar_todos_registros() -> list:
    """Lista todos os eventos, paginando se necessário."""
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


def parse_data(valor) -> date | None:
    """Aceita formatos YYYY-MM-DD ou ISO; retorna None se inválido/vazio."""
    if not valor:
        return None
    try:
        # Airtable Date field devolve "YYYY-MM-DD"
        return datetime.strptime(valor[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def atualizar_registros(updates: list):
    """PATCH em lotes de 10 (limite Airtable). 'updates' = [{id, fields}, ...]"""
    for i in range(0, len(updates), 10):
        lote = updates[i : i + 10]
        payload = {"records": lote, "typecast": True}  # typecast cria opção nova no select se faltar
        r = requests.patch(AIRTABLE_URL, headers=HEADERS, json=payload, timeout=30)
        if not r.ok:
            print(f"[ERRO] Lote {i}: {r.status_code} {r.text[:200]}")
        r.raise_for_status()


def _sem_acento(texto: str) -> str:
    """Remove acentos apenas para ordenação (mantém o nome original com acento)."""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def gerar_cidades_json(registros: list):
    """Extrai cidades dos eventos, deduplica, ordena (ignorando acentos) e grava cidades.json."""
    cidades = {
        (reg.get("fields", {}).get(COL_CIDADE) or "").strip()
        for reg in registros
    }
    cidades.discard("")  # remove vazios
    cidades_ordenadas = sorted(cidades, key=_sem_acento)

    with open("cidades.json", "w", encoding="utf-8") as f:
        json.dump(cidades_ordenadas, f, ensure_ascii=False, indent=2)

    print(f"cidades.json gerado: {len(cidades_ordenadas)} cidades.")


def main():
    hoje = date.today()
    todos = listar_todos_registros()
    print(f"Total de eventos na tabela: {len(todos)}")

    # Gera o cidades.json SEMPRE (independe de haver eventos passados)
    gerar_cidades_json(todos)

    updates = []
    for reg in todos:
        fields = reg.get("fields", {})
        data_atual = parse_data(fields.get(COL_DATA))
        if not data_atual:
            continue  # já está sem data → ignora
        if data_atual >= hoje:
            continue  # evento futuro → não mexer

        # Evento passou: limpar data, garantir mês, marcar revisar
        mes_existente = (fields.get(COL_MES) or "").strip()
        mes_da_data = NOMES_MESES[data_atual.month]
        novos_campos = {
            COL_DATA: None,         # apaga a data
            COL_REVISAR: True,      # vai para a view de revisão
        }
        # Só sobrescreve o mes se estiver vazio (preserva edições manuais)
        if not mes_existente:
            novos_campos[COL_MES] = mes_da_data

        updates.append({"id": reg["id"], "fields": novos_campos})

    if not updates:
        print("Nenhum evento passado encontrado. Tabela em dia ✓")
        return

    print(f"Eventos a atualizar: {len(updates)}")
    atualizar_registros(updates)
    print(f"Atualização concluída em {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
