# 08 · Qualidade de dados

## Papel
Cientista de dados responsável por confiabilidade. Contexto: `01-contrato-de-dados.md` (§8) e as regras estruturais já criadas no prompt 04.

## Objetivo
Implementar `quality.py`: regras de qualidade em camadas, com severidade, métricas históricas e detecção de anomalias, que decidem se a versão é **publicada**.

## Três níveis de severidade
- **BLOQUEANTE**: não publica a versão; a anterior continua valendo; alerta operacional.
- **QUARENTENA**: publica, mas marca a versão como `com_ressalva` e alerta o responsável de dados.
- **AVISO**: só registra e entra no relatório semanal.

## Regras

### A. Estruturais (já no prompt 04, reutilizar)
Todas BLOQUEANTE.

### B. Conteúdo, por célula e por linha
| Regra | Descrição | Severidade |
|---|---|---|
| `VAL_DOMINIO_ITEM` | % de células `DESCONHECIDO` | > 0 → QUARENTENA; > 0,5% → BLOQUEANTE |
| `VAL_VALIDADE_PASSADA` | `data_validade < data_referencia` em item REGULAR | > 0 → QUARENTENA |
| `VAL_VALIDADE_DISTANTE` | `data_validade > data_referencia + 400 dias` | AVISO (possível erro de século) |
| `VAL_UF_REGIAO` | região coerente com a UF | QUARENTENA |
| `VAL_IBGE_UF` | prefixo IBGE coerente com a UF | BLOQUEANTE |
| `VAL_POPULACAO` | NULL ou ≤ 0, exceto DF já conhecido | AVISO |
| `VAL_NOME_VAZIO` | nome em branco | BLOQUEANTE |

### C. Comparação com a versão anterior (drift)
| Regra | Descrição | Limite inicial |
|---|---|---|
| `DRIFT_QTD_ENTES` | variação absoluta no nº de entes | > 5 municípios ou ≠ 0 estados → QUARENTENA |
| `DRIFT_ENTES_AUSENTES` | entes presentes antes e ausentes agora | > 0 → QUARENTENA |
| `DRIFT_TAXA_PENDENCIA_ITEM` | variação da % de PENDENTE por item | z-score robusto (mediana/MAD) das últimas 12 semanas > 4 → QUARENTENA; enquanto houver < 8 semanas de histórico, usar limite absoluto de 15 p.p. |
| `DRIFT_ITEM_TUDO_IGUAL` | item que passa a ter 100% de um mesmo valor (ex.: todo `!` ou todo `Desabilitado`) e antes não tinha | QUARENTENA (indica falha na fonte do item, não do ente) |
| `DRIFT_TROCA_SIAFI` | ente com `codigo_siafi` diferente para o mesmo IBGE | QUARENTENA |
| `DRIFT_DATA_REF_ATRASO` | `data_referencia` mais de 10 dias anterior à data de execução | AVISO |

Observação de domínio: na linha de base, o item `1.3` (FGTS) está 100% `Desabilitado`. Isso é o estado conhecido, não anomalia. A regra `DRIFT_ITEM_TUDO_IGUAL` compara com a semana anterior, então só dispara quando um item **passa** a esse estado ou **sai** dele (saída também gera AVISO: o item voltou a ser avaliado e vai mudar a aptidão de muitos entes).

### D. Cruzamento (do prompt 07)
| Regra | Severidade |
|---|---|
| `MATCH_COBERTURA` < 99% das entidades que são entes | AVISO |
| `MATCH_INSTAVEL` entidade mudou de ente | QUARENTENA |
| `MATCH_ENTE_AUSENTE` entidade do sistema ligada a ente que sumiu do CAUC | QUARENTENA |

### E. Reconciliação
- Nº de linhas: arquivo bruto = bronze = (entes no silver); registros silver = entes × itens aplicáveis.
- Contagens por status no silver = contagem recalculada direto do bronze.
- Qualquer diferença → BLOQUEANTE (bug do pipeline, não da fonte).

## Métricas persistidas por execução
`dq_resultado` + uma tabela `dq_metrica(data_referencia, tipo_ente, metrica, dimensao, valor)` com, no mínimo: qtd_entes, % pendente por item, % pendente por UF, % entes com ≥1 pendência, tempo de execução por etapa, bytes do arquivo. É a base para os limites estatísticos e para um painel.

## Relatório de qualidade
Gerar `dq_report_<tipo_ente>_<data_referencia>.md` (ou HTML) com: veredito (PUBLICADO / COM RESSALVA / REJEITADO), regras que falharam com exemplos (até 10 linhas cada), métricas principais vs. semana anterior.

## Implementação
- Regras declarativas (YAML ou classes registradas) com `nome`, `descricao`, `severidade`, `limite`, `consulta`. Limites ajustáveis sem mudar código.
- Pode usar Great Expectations, Soda, Pandera ou dbt tests se já fizerem parte da stack; senão, SQL simples.

## Critérios de aceite
- Arquivos reais de `docs/`: veredito PUBLICADO, zero falhas bloqueantes e de quarentena (exceto as de comparação, que não se aplicam na primeira carga).
- Cada regra tem ao menos um teste que a faz falhar.
- Uma versão BLOQUEADA não altera `vw_situacao_atual`.
