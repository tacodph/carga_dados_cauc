# 11 · Testes e aceite final

## Papel
Engenheiro de qualidade de dados. Contexto: todos os prompts anteriores.

## Objetivo
Garantir, com testes automatizados, que o pipeline faz o que o contrato promete, e entregar um checklist de aceite para o usuário.

## Pirâmide de testes
1. **Unitários**: parser de célula, parser de cabeçalho/blocos, normalização de IBGE/SIAFI, nome normalizado, calendário de dias úteis, chave de deduplicação.
2. **Contrato**: os dois CSVs reais de `docs/` como *golden files*. Qualquer mudança de layout no Tesouro deve quebrar um teste de contrato antes de quebrar produção.
3. **Integração**: banco efêmero (Testcontainers ou Postgres em Docker), pipeline ponta a ponta com servidor HTTP falso.
4. **Cenários temporais**: as 3 semanas sintéticas do prompt 06, republicação, backfill fora de ordem, semana sem arquivo, feriado na segunda.
5. **Propriedades** (Hypothesis ou similar): para qualquer arquivo válido gerado, `entes × itens aplicáveis = registros silver` e reprocessar não muda nada.

## Números de referência (arquivos de 30/10/2026)
| Verificação | Municípios | Estados/DF |
|---|---|---|
| Linhas de dados | 5.569 | 27 |
| Colunas / itens | 35 / 28 | 37 / 30 |
| Registros silver | 155.932 | 810 |
| REGULAR | 139.818 | 759 |
| PENDENTE | 10.545 | 24 |
| NAO_AVALIADO | 5.569 (item 1.3) | 27 (item 1.3) |
| Entes com ≥1 pendência | 3.331 | 14 |
| Item com mais pendências | 3.2.3 (1.740) | 1.5 (9) |
| Legenda | 39 entradas | 39 entradas |
| `data_referencia` | 2026-10-30 | 2026-10-30 |

## Checklist de aceite (entregar preenchido ao usuário)
- [ ] Carga dos dois arquivos de `docs/` bate com a tabela acima.
- [ ] Segunda execução sem arquivo novo não grava nada e não envia alertas.
- [ ] Arquivo com item novo é rejeitado com alerta claro; após aprovar o item, o reprocesso publica.
- [ ] Acentos corretos (Acrelândia, Amapá, São Paulo) e SIAFI com zero à esquerda preservado.
- [ ] DF com população nula sem quebrar.
- [ ] Match: cobertura medida e lista de pendentes de validação gerada.
- [ ] Alertas de negócio só para entidades com match validado; nenhum na primeira carga.
- [ ] `vw_situacao_entidade_sistema` responde "minha entidade está apta?" com data de referência.
- [ ] Histórico responde "como estava na semana X" e "há quanto tempo está pendente".
- [ ] Agendamento respeita feriados e janela de tentativas.
- [ ] Runbook e ADR escritos.
- [ ] Nenhum segredo versionado.

## Entregáveis
- Suíte de testes executável com um comando (`make test` ou `pytest`), rodando em CI.
- Relatório de cobertura (meta ≥ 85% nos módulos `parse`, `normalize`, `history`, `match`).
- Checklist acima preenchido com evidências (saídas dos comandos).
