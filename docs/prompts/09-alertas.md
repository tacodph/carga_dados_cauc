# 09 · Alertas

## Papel
Engenheiro de dados. Contexto: `01-contrato-de-dados.md`, eventos do prompt 06, qualidade do prompt 08, visões do prompt 07, e os parâmetros `canais_alerta`, `responsaveis_alerta`, `itens_criticos_para_negocio`, `janela_alerta_vencimento_dias`.

## Objetivo
Implementar `alerts.py`: transformar eventos e resultados de qualidade em avisos úteis, sem ruído, para dois públicos.

## Alertas de negócio (público: gestores das entidades do sistema)
Só sobre entidades **com match validado** e só a partir da versão **publicada**.

| Tipo | Gatilho | Severidade |
|---|---|---|
| `NOVA_PENDENCIA` | evento do prompt 06 em item crítico | ALTA |
| `PERDEU_APTIDAO` | `apto_transferencia` passou de verdadeiro para falso | CRÍTICA |
| `RECUPEROU_APTIDAO` | passou de falso para verdadeiro | INFO |
| `PENDENCIA_RESOLVIDA` | evento do prompt 06 | INFO |
| `VENCIMENTO_PROXIMO` | item REGULAR com `dias_para_vencer` ≤ janela (padrão 15) **e** a data não é a própria data de referência | MÉDIA |
| `PENDENCIA_PERSISTENTE` | item PENDENTE há ≥ 4 semanas consecutivas (via SCD2) | MÉDIA, repete a cada 4 semanas |

Atenção ao `VENCIMENTO_PROXIMO`: na linha de base, 47.929 células têm validade igual à data da pesquisa e muitas outras vencem em 30 dias (itens que o CAUC reconsulta com frequência). Sem filtro, isso gera dezenas de milhares de alertas por semana. Antes de ativar, **meça a distribuição de `dias_para_vencer` por item** e proponha ao usuário uma lista de itens em que o vencimento é informativo (ex.: certidões como 1.1 e 4.2, que têm validades variadas: 159 e 176 datas distintas). Deixe esse alerta **desligado por padrão**.

## Alertas operacionais (público: time de dados)
| Tipo | Gatilho |
|---|---|
| `ARQUIVO_NAO_ATUALIZADO` | no prazo (prompt 10) a `data_referencia` não avançou |
| `FALHA_DOWNLOAD` | coleta esgotou tentativas |
| `VERSAO_REJEITADA` | regra BLOQUEANTE falhou |
| `VERSAO_COM_RESSALVA` | regra de QUARENTENA falhou |
| `MUDANCA_DE_LAYOUT` | item novo/removido, encoding diferente, coluna fixa alterada |
| `REPUBLICACAO` | mesmo `data_referencia` com outro sha256 |
| `EXECUCAO_LENTA` | duração > 3× a mediana das últimas 8 execuções |

## Regras anti-ruído
- **Deduplicação** por `chave_dedup = tipo|entidade|item|data_referencia`. O mesmo alerta nunca sai duas vezes para a mesma versão, inclusive em reprocessamento.
- **Agregação**: um e-mail/mensagem por destinatário por execução, agrupando por entidade e severidade. Alertas individuais só para CRÍTICA.
- **Primeira carga**: não enviar alertas de evento; enviar um único resumo de linha de base.
- **Supressão**: se uma regra de qualidade `DRIFT_ITEM_TUDO_IGUAL` ou `DRIFT_TAXA_PENDENCIA_ITEM` disparou para um item, suprimir os alertas de negócio daquele item nessa versão e avisar que a fonte do item parece instável.
- **Silenciar** por entidade/item com data de expiração (`base_tesouro_nacional.cauc_alerta_silencio`).

## Conteúdo da mensagem (modelo)
```
[CAUC] Situação em 30/10/2026 (semana 2026-W44) · 3 entidades com mudança

CRÍTICO · Prefeitura de <nome>/<UF> perdeu aptidão
  Nova pendência: 4.2 Regularidade Previdenciária (desde 30/10/2026)
  Pendências atuais: 1.1, 4.2
  Consulta oficial: <link para o extrato do ente no CAUC/Tesouro>

Resumo: 12 novas pendências · 8 resolvidas · 2 entidades recuperaram aptidão
Qualidade da carga: PUBLICADO sem ressalvas
```
Sempre trazer: data de referência (não a data do envio), descrição do item vinda de `dim_item`, e o aviso de que a fonte oficial prevalece.

## Implementação
- Interface `Canal` com implementações `Email` (SMTP), `Webhook` (Teams/Slack) e `Arquivo` (para testes). Credenciais só por variável de ambiente/cofre.
- Registrar tudo em `base_tesouro_nacional.cauc_alerta` antes de enviar; marcar `enviado_em` após confirmação. Reenvio de falhas na próxima execução.
- Modo `--dry-run` que gera as mensagens sem enviar.

## Critérios de aceite
- Com o cenário de 3 semanas do prompt 06, as mensagens geradas batem com os eventos injetados.
- Reprocessar a semana 2 não reenvia nada.
- Nenhum segredo no código ou nos logs.
