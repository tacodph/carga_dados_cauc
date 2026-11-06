# 04 · Parsing e validação estrutural

## Papel
Engenheiro de dados. Contexto: `01-contrato-de-dados.md` (§2, §3, §4, §8).

## Objetivo
Implementar `parse.py` e a parte estrutural de `validate.py`: transformar o arquivo bruto em (metadados, cabeçalho, linhas, legenda) e decidir se ele pode seguir. Gravar a camada bronze.

## Parser
1. Abrir em **binário**, decodificar `latin-1`. Se a decodificação latin-1 produzir os padrões típicos de UTF-8 mal lido (`Ã§`, `Ã£`, `Ã©`), tentar `utf-8-sig` e registrar `encoding_detectado` no manifesto: o Tesouro pode mudar o encoding e o pipeline deve perceber, não quebrar acentos em silêncio.
2. Usar um leitor CSV real (`csv` do Python, `delimiter=';'`, `quotechar='"'`). Não fazer `split(';')`: nomes podem conter `;` no futuro.
3. Blocos:
   - linhas 1 a 3: metadados. Extrair `data_referencia` (date), `tipo_ente` (`Municípios`→`MUNICIPIO`, `Estados e DF`→`ESTADO`), `abrangencia` (texto).
   - linha 4: cabeçalho. Normalizar com `strip()` apenas; manter os nomes originais no bronze.
   - dados: da linha 5 até a primeira linha cuja célula 0 seja `Código do Itens` (comparar sem acento e sem caixa, para tolerar `Codigo do Itens`). Ignorar linhas totalmente vazias.
   - legenda: linhas após o marcador, pares (`codigo`, `descricao`).
4. Se o marcador da legenda não existir, a última linha de dados deve ainda ter o número certo de colunas; registrar aviso `LEGENDA_AUSENTE`.
5. Não converter tipos aqui. Bronze é texto.

## Validação estrutural (bloqueante)
Implemente cada invariante do contrato (§8) como regra nomeada. Resultado vai para `base_tesouro_nacional.cauc_dq_resultado`.

| Regra | Severidade | Se falhar |
|---|---|---|
| `META_DATA_PESQUISA` formato e data válida | BLOQUEANTE | rejeitar arquivo |
| `META_TIPO_ENTE` reconhecido e coerente com o recurso baixado | BLOQUEANTE | rejeitar |
| `CAB_COLUNAS_FIXAS` A..G presentes na ordem | BLOQUEANTE | rejeitar |
| `CAB_ITENS_CONHECIDOS` toda coluna de item existe em `dim_item` | BLOQUEANTE | rejeitar e alertar "item novo no CAUC" com o código e a descrição da legenda |
| `CAB_ITENS_AUSENTES` todo item esperado para o tipo de ente está presente | BLOQUEANTE | rejeitar e alertar "item removido" |
| `LIN_LARGURA` todas as linhas com nº de colunas do cabeçalho | BLOQUEANTE | rejeitar, listar linhas |
| `LIN_IBGE_UNICO` / `LIN_SIAFI_UNICO` | BLOQUEANTE | rejeitar |
| `LIN_IBGE_FORMATO` 7 dígitos (mun.) / 2 dígitos (UF) | BLOQUEANTE | rejeitar |
| `VOL_QTD_ENTES` dentro da faixa | BLOQUEANTE | rejeitar |
| `LEG_DESCRICAO_MUDOU` descrição de item diferente da `dim_item` | AVISO | alertar, atualizar dimensão com vigência |

Item novo ou removido **não** é corrigido automaticamente: um humano aprova a inclusão em `dim_item` (com `aplica_municipio/aplica_estado`) e reprocessa a versão. Forneça o comando `cli.py aprovar-item <codigo>`.

## Bronze
- Se a validação passar, inserir `bronze_linha` (uma por linha de dados, `dados` como JSON com os nomes originais do cabeçalho) e marcar `carga_arquivo.status = 'VALIDADO'`, tudo em uma transação.
- Se falhar, `status = 'REJEITADO'`, `motivo_rejeicao` preenchido, nada no bronze, e o pipeline para essa fonte.

## Carga inicial de `dim_item`
Popule a partir da legenda dos arquivos em `docs/`: grupos I..V, subgrupos, itens. `aplica_municipio` verdadeiro para os 28 itens da coluna de municípios; `aplica_estado` para os 30 de estados. `1.3` existe mas está `Desabilitado` para todos (isso não é motivo para excluí-lo).

## Entregáveis
- `parse.py`, regras em `validate.py`, seed de `dim_item`.
- Fixtures quebradas em `tests/fixtures/`: arquivo em UTF-8, coluna extra `5.9`, coluna `3.7` removida, linha com coluna a menos, IBGE duplicado, sem legenda, só cabeçalho.

## Critérios de aceite
- Os dois arquivos reais passam: 5.569 linhas (municípios) e 27 (estados), legenda com 39 entradas.
- Cada fixture quebrada é rejeitada pela regra certa, com mensagem que um analista entende.
- `Acrelândia`, `Brasiléia` e `Amapá` aparecem corretamente acentuados no bronze.
