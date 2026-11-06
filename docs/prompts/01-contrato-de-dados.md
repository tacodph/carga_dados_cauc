# 01 · Contrato de dados do CAUC (contexto obrigatório)

> Anexe este arquivo a todos os prompts. Ele descreve o que foi **observado** nos arquivos reais (extração de 30/10/2026) e nos metadados oficiais do Tesouro. Onde algo for inferência, está marcado como **[inferido]**.

## 1. Arquivos de origem

| Recurso | Abrangência | Linhas de dados (30/10/2026) | Colunas |
|---|---|---|---|
| `relatorio-situacao-de-varios-entes---municipios---uf-todas---abrangencia-1.csv` | Municípios, CNPJ principal | 5.569 (26 UFs, sem DF) | 35 |
| `relatorio-situacao-de-varios-entes---estados-e-df---uf-todas---abrangencia-1.csv` | Estados e DF, CNPJ principal | 27 | 37 |

- Portal: CKAN do Tesouro Transparente, dataset `cauc` (id `72b5f371-0c35-4613-8076-c99c821a6410`).
- Resource id municípios: `07af297a-5e59-494a-a88a-55ddfd2f4b01`. Resource id estados/DF: `83d2fbd7-4369-4935-85c2-366925cc5841`.
- Os metadados dizem periodicidade **diária, divulgação em dias úteis**. O negócio considera a carga **semanal**, com o arquivo disponível no **primeiro dia útil da semana**. Observação: a extração analisada tem `Data da Pesquisa: 30/10/2026` (sexta-feira), portanto **a data da pesquisa pode ser anterior à data de publicação**. Nunca use a data do download como data de referência.

## 2. Formato físico

- **Encoding: ISO-8859-1 (latin-1)**. Não há BOM. Ler como UTF-8 corrompe acentos (`Abrangência`, `Acrelândia`).
- **Separador: `;`**. Todos os campos entre aspas duplas `"`, exceto campos vazios (`;;`).
- **Quebra de linha: LF** (nenhum `\r` encontrado). Aceitar CRLF mesmo assim.
- Valor literal `"null"` aparece em `População` (DF). Tratar como nulo.

## 3. Estrutura em blocos (o arquivo NÃO é um CSV retangular)

```
Linha 1  "Data da Pesquisa: 30/10/2026"                       -> metadado: data de referência
Linha 2  "Tipo de Ente: Municípios"  |  "Tipo de Ente: Estados e DF"
Linha 3  "Abrangência: CNPJ principal dos Entes Federados"
Linha 4  cabeçalho: "UF";"Nome do Ente Federado";"Código IBGE";...;"5.7"
Linhas 5..N  dados (uma linha por ente)
Linha N+1 "Código do Itens";"Exigência"                       -> início da legenda (SEM linha em branco antes)
Linhas seguintes: legenda (39 linhas) com código e descrição de grupos (I..V), subgrupos (2.1, 3.1...) e itens
```

- O fim dos dados é detectado pela linha cuja primeira célula é `Código do Itens`. Não confiar em contagem fixa de linhas.
- A legenda é **igual nos dois arquivos** e inclui itens que não existem como coluna em municípios (4.3 e 5.8). Use a legenda como dicionário de itens versionado, não como lista de colunas.

## 4. Colunas

| Col. | Nome no arquivo | Tipo destino | Observado |
|---|---|---|---|
| A | `UF` | char(2) | 27 UFs em estados, 26 em municípios |
| B | `Nome do Ente Federado` | text | Nomes de município **não são únicos** entre UFs (5.296 distintos para 5.569 linhas). Nunca usar nome como chave |
| C | `Código IBGE` | char(7) municípios / char(2) estados | Único. Municípios sempre 7 dígitos. Estados = código da UF (ex.: `12` = AC) |
| D | `Código SIAFI` | varchar(4) | Único. Municípios: 4 dígitos **com zero à esquerda** (998 começam com `0`) → manter como texto. Estados: sigla da UF (`AC`) |
| E | `Região` | enum | `N`, `NE`, `CO`, `SE`, `S` |
| F | `População` | bigint nulo | `"null"` literal no DF |
| G | `Fonte` | text | Sempre vazia na extração analisada (fonte da população) |
| H.. | itens do extrato (`1.1` ... `5.7`/`5.8`) | ver §5 | 28 itens em municípios, 30 em estados |

**Colunas de itens**
- Municípios (28): `1.1 1.2 1.3 1.4 1.5 2.1.1 2.1.2 3.1.1 3.1.2 3.2.1 3.2.2 3.2.3 3.2.4 3.3 3.4.1 3.4.2 3.5 3.6 3.7 4.1 4.2 5.1 5.2 5.3 5.4 5.5 5.6 5.7`
- Estados/DF (30): os mesmos + `4.3` (Concessão de Incentivos Fiscais) e `5.8` (Destinação mínima ao Fundeb).

> **Não há coluna CNPJ.** A abrangência é o "CNPJ principal" do ente, mas o arquivo só traz IBGE e SIAFI. Cruzar por CNPJ exige uma tabela de correspondência (ver prompt 07).

## 5. Semântica das células de item (fonte: metadados oficiais)

| Valor | Significado oficial | Status normalizado |
|---|---|---|
| `dd/mm/aa` (ex.: `30/01/27`) | Item comprovado; a data é a **validade** da comprovação | `REGULAR` (com `data_validade`) |
| `!` | "Não foi possível ao CAUC obter a informação de comprovação de cumprimento do requisito" | `PENDENTE` |
| `Desabilitado` | "A informação do item não está disponível na data da consulta. Válida para todos os entes" | `NAO_AVALIADO` |
| qualquer outro | Não previsto | `DESCONHECIDO` → erro de qualidade |

Observações dos dados reais:
- Datas no formato **ano com 2 dígitos**. Converter com pivô fixo: `aa` → `20aa`. Nenhuma data observada é anterior à data da pesquisa; 47.929 células têm validade **igual** à data da pesquisa.
- **[inferido]** Uma data de validade menor que a data de referência seria inconsistente (item vencido mostrado como regular). Trate como anomalia de qualidade, não corrija.
- `1.3` (FGTS) está `Desabilitado` para 100% dos entes na extração analisada.
- Volume de referência: municípios 139.818 datas, 10.545 `!`, 5.569 `Desabilitado`; 3.331 de 5.569 municípios (59,8%) têm ao menos um `!`. Estados: 24 `!`, 14 de 27 com pendência.

## 6. Hierarquia dos itens (da legenda)

```
I   Obrigações de Adimplência Financeira          -> 1.1 a 1.5
II  Adimplemento na Prestação de Contas de Convênios
    2.1 Prestação de Contas de Recursos Federais  -> 2.1.1 SIAFI/Subsistema Transferências, 2.1.2 Transferegov.br
III Obrigações de Transparência
    3.1 RGF                                       -> 3.1.1 Publicação, 3.1.2 Envio ao Siconfi
    3.2 RREO                                      -> 3.2.1, 3.2.2, 3.2.3 (Anexo 8 Siope), 3.2.4 (Anexo 12 Siops)
    3.3 Contas Anuais; 3.4 MSC (3.4.1 mensal, 3.4.2 encerramento); 3.5 CDP; 3.6 Transparência; 3.7 Siafic
IV  Adimplemento de Obrigações Constitucionais ou Legais -> 4.1, 4.2, 4.3
V   Cumprimento de Limites Constitucionais e Legais     -> 5.1 a 5.8
```

Descrições completas: ler da legenda do próprio arquivo a cada carga (podem mudar).

## 7. Chaves

- Chave natural do ente: **(`tipo_ente`, `codigo_ibge`)**. `codigo_siafi` é chave alternativa.
- Chave natural do fato: **(`data_referencia`, `tipo_ente`, `codigo_ibge`, `codigo_item`)**.
- Chave da versão do arquivo: **(`tipo_ente`, `data_referencia`)** + `sha256` do conteúdo bruto.

## 8. Invariantes (devem virar testes e regras de qualidade)

1. Linha 1 casa `^Data da Pesquisa: \d{2}/\d{2}/\d{4}$`; linha 2 casa `^Tipo de Ente: (Municípios|Estados e DF)$`.
2. Cabeçalho começa com as 7 colunas fixas na ordem A..G.
3. Toda linha de dados tem o mesmo número de colunas do cabeçalho.
4. `codigo_ibge` único, numérico, 7 dígitos (municípios) ou 2 dígitos (estados); `codigo_siafi` único.
5. Todo valor de item ∈ {data válida `dd/mm/aa`, `!`, `Desabilitado`}.
6. Toda coluna de item existe na legenda.
7. Quantidade de entes dentro da faixa esperada: municípios 5.560 a 5.575; estados exatamente 27.
8. Data de referência da carga ≥ data de referência da última carga publicada.
