"""Etapa 10: orquestra coleta -> validação -> normalização -> qualidade -> publicação -> match -> alertas."""

import hashlib
import json
import logging
import shutil
import statistics
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays
from psycopg.types.json import Jsonb

from . import alerts, collect, db, history, match, normalize, parse, quality, validate
from .config import TIMEZONE, TIPOS, Config
from .db import q

log = logging.getLogger("cauc")
TZ = ZoneInfo(TIMEZONE)
ESTADO_RUN: dict = {"run_id": None}


# ---------------------------------------------------------------- logs estruturados

class FormatoJson(logging.Formatter):
    CAMPOS = ("etapa", "tipo_ente", "data_referencia", "duracao_s", "linhas")

    def format(self, r: logging.LogRecord) -> str:
        d = {"ts": datetime.now(TZ).isoformat(timespec="seconds"), "nivel": r.levelname, "run_id": ESTADO_RUN["run_id"],
             "msg": r.getMessage()}
        d.update({c: getattr(r, c) for c in self.CAMPOS if hasattr(r, c)})
        if r.exc_info:
            d["erro"] = self.formatException(r.exc_info)
        return json.dumps(d, ensure_ascii=False, default=str)


def configurar_logs(pasta: Path | None = None) -> None:
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    raiz.handlers.clear()
    handlers = [logging.StreamHandler()]
    if pasta:
        pasta.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(pasta / f"cauc_{datetime.now():%Y%m%d}.jsonl", encoding="utf-8"))
    for h in handlers:
        h.setFormatter(FormatoJson())
        raiz.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ---------------------------------------------------------------- calendário

@lru_cache(maxsize=32)
def _feriados(ano: int, facultativos: bool) -> frozenset[date]:
    cats = ("public", "optional") if facultativos else ("public",)
    return frozenset(holidays.country_holidays("BR", years=ano, categories=cats))


def eh_dia_util(d: date, cfg: Config) -> bool:
    return d.weekday() < 5 and d not in _feriados(d.year, cfg.feriados_facultativos) and d not in cfg.feriados_extras


def proximo_dia_util(d: date, cfg: Config) -> date:
    d += timedelta(days=1)
    while not eh_dia_util(d, cfg):
        d += timedelta(days=1)
    return d


def primeiro_dia_util_semana(d: date, cfg: Config) -> date:
    segunda = d - timedelta(days=d.weekday())
    return segunda if eh_dia_util(segunda, cfg) else proximo_dia_util(segunda, cfg)


def janela(agora: datetime, cfg: Config) -> str | None:
    """PRIMEIRO_DIA (disparos 08h, 12h, 18h) ou DIA_SEGUINTE (só o das 08h, último da janela); fora dela, None."""
    hoje = agora.date()
    primeiro = primeiro_dia_util_semana(hoje, cfg)
    if hoje == primeiro:
        return "PRIMEIRO_DIA"
    if hoje == proximo_dia_util(primeiro, cfg) and agora.hour < 12:
        return "DIA_SEGUINTE"
    return None


# ---------------------------------------------------------------- execução

@dataclass
class Execucao:
    conn: object
    cfg: Config
    run_id: uuid.UUID
    alertas: list = field(default_factory=list)
    etapas: dict = field(default_factory=dict)
    vereditos: dict = field(default_factory=dict)
    publicados: dict = field(default_factory=dict)  # tipo -> (data_ref, data_anterior, itens_suprimidos)

    def operacional(self, tipo_alerta: str, severidade: str, data_ref: date | None, sufixo: str | None = None, **payload):
        self.alertas.append(alerts.Alerta(tipo_alerta, severidade, data_ref, payload=payload, sufixo_dedup=sufixo))
        log.warning("alerta %s: %s", tipo_alerta, payload)


@contextmanager
def etapa(ex: Execucao, tipo: str, nome: str):
    inicio = time.perf_counter()
    yield
    dur = round(time.perf_counter() - inicio, 3)
    ex.etapas.setdefault(tipo, {})[nome] = dur
    log.info("etapa %s concluída", nome, extra={"etapa": nome, "tipo_ente": tipo, "duracao_s": dur})


def _execucao_inicio(ex: Execucao, tipo: str) -> None:
    ex.conn.execute(q("""insert into {s}.cauc_execucao (run_id, tipo_ente, inicio, status) values (%s, %s, now(), 'EM_EXECUCAO')
                         on conflict (run_id, tipo_ente) do nothing"""), (ex.run_id, tipo))


def _execucao_fim(ex: Execucao, tipo: str, status: str, data_ref: date | None = None, erro: str | None = None, **extra) -> None:
    ex.conn.execute(q("""update {s}.cauc_execucao set fim = now(), status = %s, data_referencia = %s, etapas = %s, erro = %s
                         where run_id = %s and tipo_ente = %s"""),
                    (status, data_ref, Jsonb({**ex.etapas.get(tipo, {}), **extra}), erro, ex.run_id, tipo))


def _lock(conn, tipo: str) -> bool:
    return conn.execute("select pg_try_advisory_lock(hashtext(%s))", (f"cauc:{tipo}",)).fetchone()[0]


def _unlock(conn, tipo: str) -> None:
    conn.execute("select pg_advisory_unlock(hashtext(%s))", (f"cauc:{tipo}",))


def registrar_carga(ex: Execucao, tipo: str, data_ref: date, sha: str, tamanho: int, caminho: Path, manifesto: dict,
                    status: str = "RECEBIDO", motivo: str | None = None) -> int:
    return ex.conn.execute(q("""
        insert into {s}.cauc_carga_arquivo (tipo_ente, data_referencia, semana_iso, sha256, tamanho_bytes, url_origem,
            caminho_raw, ckan_last_modified, baixado_em, status, motivo_rejeicao, run_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id_carga"""),
        (tipo, data_ref, data_ref.strftime("%G-W%V"), sha, tamanho, manifesto.get("url_origem", ""), str(caminho),
         manifesto.get("ckan_last_modified"), manifesto.get("baixado_em") or datetime.now(timezone.utc), status, motivo,
         ex.run_id)).fetchone()[0]


def _rejeitar(ex: Execucao, id_carga: int, tipo: str, data_ref, motivo: str) -> str:
    ex.conn.execute(q("update {s}.cauc_carga_arquivo set status = 'REJEITADO', motivo_rejeicao = %s where id_carga = %s"),
                    (motivo, id_carga))
    ex.operacional("VERSAO_REJEITADA", "ALTA", data_ref, sufixo=f"{tipo}|carga={id_carga}", tipo_ente=tipo,
                   id_carga=id_carga, motivo=motivo[:2000])
    return "REJEITADO"


def _publicar(ex: Execucao, id_carga: int, tipo: str, data_ref: date, entes: list[dict], fatos: list[normalize.Fato],
              com_ressalva: bool, met: list) -> None:
    """Uma transação: silver + status + SCD2 + eventos + métricas. Falha = versão anterior continua vigente."""
    c = ex.conn
    with c.transaction():
        c.execute(q("delete from {s}.cauc_fato_situacao_item where tipo_ente = %s and data_referencia = %s"), (tipo, data_ref))
        c.execute(q("delete from {s}.cauc_fato_ente where tipo_ente = %s and data_referencia = %s"), (tipo, data_ref))
        with c.cursor().copy(q("""copy {s}.cauc_fato_ente (data_referencia, tipo_ente, codigo_ibge, codigo_siafi, uf, nome,
                                  nome_normalizado, regiao, populacao, fonte_populacao, id_carga) from stdin""")) as cp:
            for e in entes:
                cp.write_row((data_ref, tipo, e["codigo_ibge"], e["codigo_siafi"], e["uf"], e["nome"], e["nome_normalizado"],
                              e["regiao"], e["populacao"], e["fonte_populacao"], id_carga))
        with c.cursor().copy(q("""copy {s}.cauc_fato_situacao_item (data_referencia, tipo_ente, codigo_ibge, codigo_item,
                                  valor_bruto, status, data_validade, dias_para_vencer, id_carga) from stdin""")) as cp:
            for f in fatos:
                cp.write_row((data_ref, tipo, f.codigo_ibge, f.codigo_item, f.valor_bruto, f.status, f.data_validade,
                              f.dias_para_vencer, id_carga))
        c.execute(q("""update {s}.cauc_carga_arquivo set status = 'SUBSTITUIDO'
                       where tipo_ente = %s and data_referencia = %s and status = 'PUBLICADO' and id_carga <> %s"""),
                  (tipo, data_ref, id_carga))
        c.execute(q("""update {s}.cauc_carga_arquivo set status = 'PUBLICADO', com_ressalva = %s, publicado_em = now()
                       where id_carga = %s"""), (com_ressalva, id_carga))
        history.reconstruir_scd2(c, tipo)
        n_ev = history.reconstruir_eventos(c, tipo, data_ref)
        c.execute(q("delete from {s}.cauc_dq_metrica where tipo_ente = %s and data_referencia = %s"), (tipo, data_ref))
        with c.cursor() as cur:
            cur.executemany(q("""insert into {s}.cauc_dq_metrica (data_referencia, tipo_ente, metrica, dimensao, valor, run_id)
                                 values (%s, %s, %s, %s, %s, %s)"""), [(data_ref, tipo, m, d, v, ex.run_id) for m, d, v in met])
    log.info("versão publicada", extra={"tipo_ente": tipo, "data_referencia": data_ref, "linhas": len(fatos)})
    log.info("%s eventos de mudança", n_ev, extra={"tipo_ente": tipo, "data_referencia": data_ref})


def _anterior(conn, tipo: str, data_ant: date | None) -> quality.Anterior | None:
    if data_ant is None:
        return None
    entes = {r[0]: {"codigo_siafi": r[1], "nome": r[2], "uf": r[3]} for r in conn.execute(q(
        "select codigo_ibge, codigo_siafi, nome, uf from {s}.cauc_fato_ente where tipo_ente = %s and data_referencia = %s"),
        (tipo, data_ant))}
    status = {k: v[0] for k, v in history.carregar_snapshot(conn, tipo, data_ant).items()}
    return quality.Anterior(data_ant, entes, status)


def _historico_taxas(conn, tipo: str, data_ref: date) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for item, valor in conn.execute(q("""select dimensao, valor from {s}.cauc_dq_metrica
                                         where tipo_ente = %s and metrica = 'pct_pendente_item' and data_referencia < %s
                                         order by data_referencia desc"""), (tipo, data_ref)):
        out.setdefault(item, []).append(float(valor))
    return out


def processar_arquivo(ex: Execucao, id_carga: int, tipo: str, conteudo: bytes, hoje: date) -> str:
    """Do bruto à publicação. Reexecutável para a mesma carga (reprocess)."""
    c, cfg = ex.conn, ex.cfg
    with etapa(ex, tipo, "validacao"):
        arq = parse.ler(conteudo)
        db.garantir_dim_item(c)
        dim = db.carregar_dim_item(c)
        res = validate.validar_estrutura(arq, tipo, dim)
        db.gravar_dq(c, ex.run_id, id_carga, res)
    data_ref = arq.data_referencia
    layout = [r for r in validate.falhas(res) if r.regra in ("CAB_COLUNAS_FIXAS", "CAB_ITENS_CONHECIDOS",
                                                              "CAB_ITENS_AUSENTES", "META_ENCODING", "META_TIPO_ENTE")]
    if layout:
        ex.operacional("MUDANCA_DE_LAYOUT", "ALTA", data_ref, sufixo=f"{tipo}|carga={id_carga}", tipo_ente=tipo,
                       regras={r.regra: r.mensagem for r in layout})
    bloq = validate.falhas(res, validate.BLOQUEANTE)
    if bloq:
        return _rejeitar(ex, id_carga, tipo, data_ref, " | ".join(f"{r.regra}: {r.mensagem}" for r in bloq))

    with etapa(ex, tipo, "bronze"), c.transaction():
        c.execute(q("delete from {s}.cauc_bronze_linha where id_carga = %s"), (id_carga,))
        with c.cursor().copy(q("copy {s}.cauc_bronze_linha (id_carga, num_linha, dados) from stdin")) as cp:
            for num, cel in arq.linhas:
                cp.write_row((id_carga, num, Jsonb(dict(zip(arq.cabecalho, cel)))))
        c.execute(q("""update {s}.cauc_carga_arquivo set status = 'VALIDADO', qtd_entes = %s, colunas_itens = %s,
                       encoding_detectado = %s, motivo_rejeicao = null where id_carga = %s"""),
                  (len(arq.linhas), validate.colunas_itens(arq.cabecalho), arq.encoding, id_carga))
    mudancas = next(r for r in res if r.regra == "LEG_DESCRICAO_MUDOU").detalhe.get("mudancas", {})
    for codigo, descricao in mudancas.items():
        # ponytail: dim_item guarda só a descrição vigente (o histórico fica no cauc_dq_resultado)
        c.execute(q("update {s}.cauc_dim_item set descricao = %s where codigo_item = %s"), (descricao, codigo))

    with etapa(ex, tipo, "normalizacao"):
        entes, fatos = normalize.normalizar(arq)
    with etapa(ex, tipo, "qualidade"):
        data_ant = db.ultima_publicada(c, tipo, antes_de=data_ref)
        lim = quality.limites(cfg.dq_limites_json)
        qtd_bronze = c.execute(q("select count(*) from {s}.cauc_bronze_linha where id_carga = %s"), (id_carga,)).fetchone()[0]
        qres = (quality.regras_conteudo(tipo, data_ref, entes, fatos, lim)
                + quality.regras_drift(tipo, data_ref, hoje, entes, fatos, _anterior(c, tipo, data_ant),
                                       _historico_taxas(c, tipo, data_ref), lim)
                + quality.reconciliar(arq, qtd_bronze, entes, fatos))
        db.gravar_dq(c, ex.run_id, id_carga, qres)
        decisao = quality.veredito(qres)
        tamanho = len(conteudo)
        met = quality.metricas(entes, fatos, tamanho)
        met_ant = {(m, d): float(v) for m, d, v in c.execute(q(
            "select metrica, dimensao, valor from {s}.cauc_dq_metrica where tipo_ente = %s and data_referencia = %s"),
            (tipo, data_ant))} if data_ant else {}
        cfg.pasta_relatorios.mkdir(parents=True, exist_ok=True)
        (cfg.pasta_relatorios / f"dq_report_{tipo}_{data_ref.isoformat()}.md").write_text(
            quality.relatorio_md(tipo, data_ref, decisao, res + qres, met, met_ant), encoding="utf-8")
    ex.vereditos[tipo] = decisao
    if decisao == "REJEITADO":
        return _rejeitar(ex, id_carga, tipo, data_ref, " | ".join(
            f"{r.regra}: {r.mensagem}" for r in validate.falhas(qres, validate.BLOQUEANTE)))

    with etapa(ex, tipo, "publicacao"):
        _publicar(ex, id_carga, tipo, data_ref, entes, fatos, decisao == "COM_RESSALVA", met)
    if decisao == "COM_RESSALVA":
        ex.operacional("VERSAO_COM_RESSALVA", "MEDIA", data_ref, sufixo=f"{tipo}|carga={id_carga}", tipo_ente=tipo,
                       regras={r.regra: r.mensagem for r in validate.falhas(qres, validate.QUARENTENA)})
    suprimidos = set()
    for r in validate.falhas(qres):
        if r.regra in ("DRIFT_TAXA_PENDENCIA_ITEM", "DRIFT_ITEM_TUDO_IGUAL"):
            suprimidos |= set(r.detalhe.get("itens", []))
    ex.publicados[tipo] = (data_ref, data_ant, suprimidos)
    return decisao


def _ja_existe(conn, tipo: str):
    def consulta(data_ref: date, sha: str) -> bool:
        return conn.execute(q("""select 1 from {s}.cauc_carga_arquivo
                                 where tipo_ente = %s and data_referencia = %s and sha256 = %s"""),
                            (tipo, data_ref, sha)).fetchone() is not None
    return consulta


def processar_tipo(ex: Execucao, tipo: str, cliente, hoje: date) -> str:
    c = ex.conn
    try:
        with etapa(ex, tipo, "coleta"):
            col = collect.coletar(ex.cfg, tipo, cliente, _ja_existe(c, tipo))
    except collect.ErroColeta as erro:
        ex.operacional(erro.tipo_alerta, "ALTA", None, sufixo=f"{tipo}|{hoje.isoformat()}", tipo_ente=tipo, erro=str(erro))
        return "ERRO"
    extra = {"tipo_ente": tipo, "data_referencia": col.data_referencia}
    if not col.nova_versao:
        log.info("sem novidade (versão já registrada)", extra=extra)
        return "SEM_NOVIDADE"
    ultima = db.ultima_publicada(c, tipo)
    if ultima and col.data_referencia < ultima:
        id_carga = registrar_carga(ex, tipo, col.data_referencia, col.sha256, col.tamanho_bytes, col.caminho, col.manifesto)
        return _rejeitar(ex, id_carga, tipo, col.data_referencia,
                         f"Arquivo regrediu: data de referência {col.data_referencia} < última publicada {ultima}")
    if ultima == col.data_referencia:
        ex.operacional("REPUBLICACAO", "INFO", col.data_referencia, sufixo=f"{tipo}|{col.sha256}", tipo_ente=tipo,
                       sha256=col.sha256)
    id_carga = registrar_carga(ex, tipo, col.data_referencia, col.sha256, col.tamanho_bytes, col.caminho, col.manifesto)
    return processar_arquivo(ex, id_carga, tipo, col.caminho.read_bytes(), hoje)


def pos_processamento(ex: Execucao, cliente) -> None:
    """Match (07) e alertas de negócio (09) para os tipos publicados nesta execução; grava a mensagem (dry-run)."""
    c, cfg = ex.conn, ex.cfg
    if ex.publicados:
        with etapa(ex, "GERAL", "match"):
            try:
                n = match.atualizar_ref_cnpj(c, cliente)
                if n is not None:
                    log.info("tabela CNPJ↔IBGE atualizada: %s entes", n)
            except Exception as erro:  # Siconfi fora do ar não impede o match com a tabela existente
                log.warning("não foi possível atualizar a tabela CNPJ↔IBGE: %s", erro)
            res = match.executar_match(c, cfg.pasta_relatorios, ex.run_id)
            db.gravar_dq(c, ex.run_id, None, res)
            data_ref = max(d for d, _, _ in ex.publicados.values())
            for r in validate.falhas(res, validate.QUARENTENA):
                ex.operacional("VERSAO_COM_RESSALVA", "MEDIA", data_ref, sufixo=f"{r.regra}|{data_ref}", regra=r.regra,
                               mensagem=r.mensagem)
    negocio = []
    for tipo, (data_ref, data_ant, suprimidos) in ex.publicados.items():
        ctx = alerts.contexto_negocio(c, tipo, data_ref, data_ant, suprimidos, cfg.janela_vencimento_dias,
                                      cfg.alerta_vencimento_ativo)
        negocio += alerts.gerar_alertas_negocio(ctx)
        if suprimidos:
            ex.operacional("FONTE_ITEM_INSTAVEL", "MEDIA", data_ref, sufixo=f"{tipo}|{data_ref}", tipo_ente=tipo,
                           itens=sorted(suprimidos))
    novos = alerts.registrar(c, ex.run_id, negocio + ex.alertas)
    if novos:
        data_ref = max((d for d, _, _ in ex.publicados.values()), default=None)
        descricoes = {k: v["descricao"] for k, v in db.carregar_dim_item(c).items()}
        caminho = alerts.gravar_mensagem(cfg.pasta_alertas, ex.run_id,
                                         alerts.compor_mensagem(novos, data_ref, descricoes, ex.vereditos))
        log.info("%s alerta(s) novos gravados em %s (dry-run, nada enviado)", len(novos), caminho)


def _semana_publicada(conn, tipo: str, desde: datetime) -> bool:
    return conn.execute(q("""select 1 from {s}.cauc_carga_arquivo where tipo_ente = %s and status = 'PUBLICADO'
                             and publicado_em >= %s"""), (tipo, desde)).fetchone() is not None


def _checagens_operacionais(ex: Execucao, hoje: date, duracao: float) -> None:
    c = ex.conn
    for tipo in TIPOS:
        ultima = db.ultima_publicada(c, tipo)
        if ultima and (hoje - ultima).days > 9:
            ex.operacional("ULTIMA_VERSAO_ANTIGA", "ALTA", ultima, sufixo=f"{tipo}|{hoje.isoformat()}", tipo_ente=tipo,
                           dias=(hoje - ultima).days)
    if ex.publicados:
        anteriores = [float(r[0]) for r in c.execute(q("""
            select extract(epoch from fim - inicio) from {s}.cauc_execucao
            where tipo_ente = 'GERAL' and (etapas->>'publicou')::boolean and run_id <> %s and fim is not null
            order by inicio desc limit 8"""), (ex.run_id,))]
        if len(anteriores) >= 3 and duracao > 3 * statistics.median(anteriores):
            ex.operacional("EXECUCAO_LENTA", "MEDIA", None, sufixo=str(ex.run_id), duracao_s=round(duracao, 1),
                           mediana_s=round(statistics.median(anteriores), 1))


def executar(cfg: Config, agora: datetime | None = None, forcar: bool = False, cliente=None,
             tipos: tuple[str, ...] = TIPOS) -> int:
    """Códigos de saída: 0 ok/sem novidade/fora da janela, 2 já em execução, 3 parcial (rejeitado ou erro)."""
    agora = agora or datetime.now(TZ)
    hoje = agora.date()
    slot = janela(agora, cfg)
    if slot is None and not forcar:
        log.info("fora da janela de execução (primeiro dia útil da semana e o seguinte)")
        return 0
    inicio_janela = datetime.combine(primeiro_dia_util_semana(hoje, cfg), datetime.min.time(), TZ)
    ex = Execucao(db.conectar(cfg), cfg, uuid.uuid4())
    ESTADO_RUN["run_id"] = str(ex.run_id)
    cliente = cliente or collect.novo_cliente()
    t0 = time.perf_counter()
    status: dict[str, str] = {}
    try:
        _execucao_inicio(ex, "GERAL")
        for tipo in tipos:
            if not forcar and _semana_publicada(ex.conn, tipo, inicio_janela):
                status[tipo] = "JA_PUBLICADA"
                continue
            if not _lock(ex.conn, tipo):
                log.warning("outra execução em andamento", extra={"tipo_ente": tipo})
                status[tipo] = "OCUPADO"
                continue
            _execucao_inicio(ex, tipo)
            try:
                status[tipo] = processar_tipo(ex, tipo, cliente, hoje)
                _execucao_fim(ex, tipo, status[tipo], ex.publicados.get(tipo, (None,))[0])
            except Exception as erro:
                log.exception("falha ao processar", extra={"tipo_ente": tipo})
                status[tipo] = "ERRO"
                ex.operacional("FALHA_EXECUCAO", "ALTA", None, sufixo=f"{tipo}|{ex.run_id}", tipo_ente=tipo, erro=str(erro))
                _execucao_fim(ex, tipo, "ERRO", erro=str(erro)[:4000])
            finally:
                _unlock(ex.conn, tipo)
            if slot == "DIA_SEGUINTE" and not forcar and status[tipo] == "SEM_NOVIDADE" \
                    and not _semana_publicada(ex.conn, tipo, inicio_janela):
                ex.operacional("ARQUIVO_NAO_ATUALIZADO", "ALTA", db.ultima_publicada(ex.conn, tipo),
                               sufixo=f"{tipo}|{hoje.strftime('%G-W%V')}", tipo_ente=tipo)
        duracao = time.perf_counter() - t0
        _checagens_operacionais(ex, hoje, duracao)
        pos_processamento(ex, cliente)
        geral = "OCUPADO" if "OCUPADO" in status.values() else \
            "PARCIAL" if {"ERRO", "REJEITADO"} & set(status.values()) else "OK"
        _execucao_fim(ex, "GERAL", geral, publicou=bool(ex.publicados), tipos=status, duracao_s=round(duracao, 1))
        log.info("execução concluída: %s %s", geral, status)
        return {"OK": 0, "OCUPADO": 2, "PARCIAL": 3}[geral]
    finally:
        ex.conn.close()


def reprocessar(cfg: Config, tipo: str, data_ref: date) -> str:
    """A partir do raw, sem baixar de novo (ex.: depois de aprovar-item)."""
    ex = Execucao(db.conectar(cfg), cfg, uuid.uuid4())
    ESTADO_RUN["run_id"] = str(ex.run_id)
    try:
        linha = ex.conn.execute(q("""select id_carga, caminho_raw from {s}.cauc_carga_arquivo
                                     where tipo_ente = %s and data_referencia = %s and status <> 'SUBSTITUIDO'
                                     order by id_carga desc limit 1"""), (tipo, data_ref)).fetchone()
        if not linha:
            raise SystemExit(f"Nenhuma carga de {tipo} em {data_ref}")
        if not _lock(ex.conn, tipo):
            raise SystemExit("Outra execução em andamento")
        try:
            _execucao_inicio(ex, tipo)
            resultado = processar_arquivo(ex, linha[0], tipo, Path(linha[1]).read_bytes(), date.today())
            _execucao_fim(ex, tipo, resultado, data_ref, reprocesso=True)
        finally:
            _unlock(ex.conn, tipo)
        pos_processamento(ex, collect.novo_cliente())
        return resultado
    finally:
        ex.conn.close()


def backfill(cfg: Config, pasta: Path) -> dict[str, str]:
    """Carrega CSVs antigos em ordem crescente de data_referencia; histórico e eventos são reconstruídos."""
    arquivos = []
    for caminho in sorted(pasta.rglob("*.csv")):
        arq = parse.ler(caminho.read_bytes())
        if arq.data_referencia and arq.tipo_ente:
            arquivos.append((arq.data_referencia, arq.tipo_ente, caminho))
        else:
            log.warning("ignorado (sem data/tipo reconhecidos): %s", caminho)
    ex = Execucao(db.conectar(cfg), cfg, uuid.uuid4())
    ESTADO_RUN["run_id"] = str(ex.run_id)
    saida = {}
    try:
        for data_ref, tipo, caminho in sorted(arquivos):
            conteudo = caminho.read_bytes()
            tmp = cfg.pasta_tmp / f"backfill_{time.time_ns()}.parcial"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(caminho, tmp)
            sha = hashlib.sha256(conteudo).hexdigest()
            destino, _ = collect.guardar_raw(cfg, tipo, tmp, sha, len(conteudo),
                                             {"url_origem": caminho.resolve().as_uri(), "descoberta": "backfill"})
            if _ja_existe(ex.conn, tipo)(data_ref, sha):
                saida[str(caminho)] = "JA_CARREGADO"
                continue
            if not _lock(ex.conn, tipo):
                raise SystemExit("Outra execução em andamento")
            try:
                id_carga = registrar_carga(ex, tipo, data_ref, sha, len(conteudo), destino,
                                           {"url_origem": caminho.resolve().as_uri()})
                saida[str(caminho)] = processar_arquivo(ex, id_carga, tipo, conteudo, data_ref)
            finally:
                _unlock(ex.conn, tipo)
        # backfill não dispara alertas de negócio: só registra os operacionais
        alerts.registrar(ex.conn, ex.run_id, ex.alertas)
        return saida
    finally:
        ex.conn.close()
