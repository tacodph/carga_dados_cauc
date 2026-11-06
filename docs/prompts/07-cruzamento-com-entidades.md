# 07 · Cruzamento com as entidades do sistema

## Papel
Cientista/engenheiro de dados responsável por *entity resolution*. Contexto: `01-contrato-de-dados.md` (§4, §7) e o parâmetro `tabela_entidades_sistema` / `chave_entidade_sistema`.

## Objetivo
Implementar `match.py`: ligar cada entidade do sistema do usuário ao ente do CAUC, com rastreabilidade e alta precisão, e expor a situação CAUC por entidade do sistema.

## Primeiro, investigue o sistema (não suponha)
1. Leia o schema da tabela de entidades informada. Liste colunas candidatas a chave: CNPJ, código IBGE, código SIAFI, UF, nome, tipo (prefeitura, câmara, autarquia, fundo, consórcio, estado...).
2. Faça um perfil: % nulos, formatos, duplicidades, entidades que não são entes federados.
3. **Pare e pergunte ao usuário** somente se não houver nenhuma coluna de identificação utilizável.

## Ponto crítico: o CAUC é por ente, não por órgão
- O arquivo traz só o **CNPJ principal** do ente (prefeitura / governo do estado), e **não traz o CNPJ**: só IBGE e SIAFI.
- Se o sistema tiver órgãos com CNPJ próprio (câmara, fundo municipal, autarquia, secretaria), eles **herdam a situação do ente** a que pertencem. O match dessas entidades é sempre indireto: entidade → município/UF → ente CAUC. Marque `metodo_match` e deixe claro em telas e relatórios que a situação é a do ente principal.

## Estratégia de match (em cascata, do mais forte ao mais fraco)

| Ordem | Método | Condição | Confiança |
|---|---|---|---|
| 1 | `IBGE` | sistema tem código IBGE (7 dígitos município / 2 UF) | 1.00 |
| 2 | `SIAFI` | sistema tem código SIAFI (texto, com zeros) | 1.00 |
| 3 | `CNPJ_VIA_TABELA` | sistema tem só CNPJ do ente principal → usar tabela de correspondência CNPJ↔IBGE | 0.98 |
| 4 | `NOME_UF` | nome normalizado (sem acento, maiúsculas, sem "MUNICIPIO DE", "PREFEITURA MUNICIPAL DE") + UF, igualdade exata | 0.90, exige validação humana |
| 5 | `NOME_UF_FUZZY` | similaridade ≥ 0.92 (Jaro-Winkler ou token sort), **mesma UF obrigatória**, candidato único | 0.70, só sugestão, nunca automático |

Regras:
- Nunca casar só por nome sem UF: há 5.296 nomes distintos para 5.569 municípios (ex.: vários "Bom Jesus", "Santa Helena").
- Um ente CAUC pode ter várias entidades do sistema; uma entidade do sistema tem no máximo um ente CAUC.

### Tabela de correspondência CNPJ ↔ IBGE (para o método 3)
Construa `base_tesouro_nacional.cauc_ref_cnpj_ente(cnpj, tipo_ente, codigo_ibge, fonte, atualizado_em)` a partir de uma fonte oficial, por exemplo a API de entes do Siconfi (`https://apidatalake.tesouro.gov.br/ords/siconfi/tt/entes`, que traz `cod_ibge` e `cnpj`) ou do cadastro do seu próprio sistema. Atualize mensalmente. Valide dígitos verificadores do CNPJ e guarde só os 14 dígitos.

## Persistência e governança
- Resultado em `base_tesouro_nacional.cauc_map_entidade`. Matches de confiança < 1.00 ficam com `validado_por` nulo até aprovação; só matches validados entram na visão de negócio.
- Gere `relatorio_match.csv` a cada execução: entidades sem match, com match ambíguo, e matches novos ou alterados.
- Reexecutar o match é idempotente e nunca sobrescreve um match validado manualmente.

## Saídas
- `base_tesouro_nacional.cauc_vw_situacao_entidade_sistema`: para cada entidade do sistema, `data_referencia`, `apto_transferencia`, `qtd_pendentes`, `itens_pendentes`, `menor_validade`, `dias_pendente_mais_antigo`, `metodo_match`.
- Se o sistema do usuário aceitar escrita, ofereça (sem implementar sem autorização) uma integração de volta: coluna `situacao_cauc` atualizada pela visão.

## Métricas de qualidade do match (vão para o prompt 08)
- Cobertura: % de entidades do sistema com match validado. Meta ≥ 99% para entidades que são entes federados.
- Precisão por amostragem: revisar 30 matches aleatórios por método na primeira carga.
- Estabilidade: nº de matches que mudaram de ente entre execuções (deve ser 0).

## Critérios de aceite
- Teste com uma tabela sintética de entidades contendo: IBGE válido, SIAFI com zero à esquerda, CNPJ de prefeitura, câmara municipal, nome homônimo em UFs diferentes, nome com grafia diferente ("Sao Joao del Rei" vs "São João del-Rei"), entidade inexistente.
- Nenhum homônimo casado com a UF errada.
