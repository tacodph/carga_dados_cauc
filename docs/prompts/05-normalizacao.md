# 05 · Normalização (bronze → silver)

## Papel
Engenheiro de dados com olhar de cientista de dados. Contexto: `01-contrato-de-dados.md` (§4, §5, §7).

## Objetivo
Implementar `normalize.py`: transformar cada linha do bronze em registros tipados no modelo longo `fato_situacao_item` e atualizar `dim_ente`.

## Regras de transformação

### Atributos do ente
| Origem | Destino | Regra |
|---|---|---|
| `UF` | `uf` | `upper(strip)`; validar contra lista das 27 UFs |
| `Nome do Ente Federado` | `nome` | `strip`, normalizar espaços múltiplos; **manter acentos e caixa originais**. Criar também `nome_normalizado` (sem acento, maiúsculas) só para busca |
| `Código IBGE` | `codigo_ibge` | texto; municípios `zfill(7)`, estados `zfill(2)`; validar que os 2 primeiros dígitos do município = código IBGE da UF |
| `Código SIAFI` | `codigo_siafi` | texto, `strip`, **sem** conversão numérica |
| `Região` | `regiao` | domínio `N, NE, CO, SE, S`; validar coerência com a UF |
| `População` | `populacao` | `""` ou `"null"` → NULL; senão inteiro > 0 |
| `Fonte` | `fonte_populacao` | vazio → NULL |

### Itens (unpivot)
Para cada coluna de item presente no cabeçalho, gerar um registro:

```
valor_bruto = strip(célula)
se valor_bruto casa ^\d{2}/\d{2}/\d{2}$:
    data_validade = parse dd/mm/aa com século 20 (aa → 20aa)   # não usar o pivô padrão da biblioteca
    status = 'REGULAR'
    dias_para_vencer = data_validade - data_referencia
senão se valor_bruto == '!':            status = 'PENDENTE'
senão se lower(valor_bruto) == 'desabilitado': status = 'NAO_AVALIADO'
senão:                                   status = 'DESCONHECIDO'   # nunca descartar; vai para qualidade
```

Itens que existem em `dim_item` mas não se aplicam ao tipo de ente (4.3 e 5.8 para municípios) **não** geram registro. Ausência ≠ pendência.

### Indicadores derivados por ente (visão, não tabela)
- `qtd_pendentes`, `qtd_regulares`, `qtd_nao_avaliados`, `itens_pendentes` (lista ordenada).
- `apto_transferencia` **[inferido, validar com o negócio]**: verdadeiro se nenhum item aplicável estiver `PENDENTE`. `NAO_AVALIADO` não reprova. Documente essa regra como configurável, pois a regra legal de exigibilidade de cada item depende do tipo de transferência.
- `menor_validade`: menor `data_validade` entre os itens regulares (próximo vencimento).
- Pendências por grupo (I a V), úteis para priorizar ação.

### dim_ente (SCD2)
- Comparar os atributos cadastrais (`nome`, `codigo_siafi`, `uf`, `regiao`, `populacao`) com a versão atual.
- Mudou → fecha a atual (`valido_ate = data_referencia - 1`, `atual = false`) e abre nova. População muda todo ano: isso é esperado.
- Ente que some do arquivo **não** é fechado aqui; é tratado como evento `ENTE_AUSENTE` no prompt 06.

## Carga
- Upsert idempotente por `(data_referencia, tipo_ente, codigo_ibge, codigo_item)`. Reprocessar a mesma versão substitui os registros dessa versão em uma transação (delete + insert por `id_carga`, ou `MERGE`).
- Use carga em lote (`COPY`/bulk insert). Volume esperado por semana: ~156 mil linhas (5.569 × 28) + 810 (27 × 30).

## Entregáveis
- `normalize.py`, testes unitários do parser de célula (datas limite: `31/12/99`, `29/02/28`, `00/01/27` inválida, `!`, `Desabilitado`, `desabilitado `, `X`).
- Visão `vw_resumo_ente` com os indicadores derivados.

## Critérios de aceite (com os arquivos de `docs/`)
- Municípios: 155.932 registros (5.569 × 28); 139.818 `REGULAR`, 10.545 `PENDENTE`, 5.569 `NAO_AVALIADO`, 0 `DESCONHECIDO`.
- Estados: 810 registros; 759 `REGULAR`, 24 `PENDENTE`, 27 `NAO_AVALIADO`.
- 3.331 municípios e 14 estados com ao menos uma pendência.
- DF com `populacao` NULL; SIAFI `0643` (Acrelândia) preservado com o zero.
