# Prompts de ingestão semanal do CAUC

Conjunto de prompts para um agente de IA (Claude Code, Cursor, Copilot Agent etc.) implementar, passo a passo, a carga semanal do **CAUC** (Serviço Auxiliar de Informações para Transferências Voluntárias, Tesouro Nacional) e o cruzamento com as entidades do seu sistema.

Destino dos dados: banco **`transferepro`**, schema **`base_tesouro_nacional`**, objetos com prefixo `cauc_` (detalhes no prompt 02).

Os prompts foram escritos a partir da análise real dos arquivos em `docs/` (extração de 30/10/2026). Os fatos sobre o layout estão no prompt 01, que todos os outros referenciam.

## Como usar

1. Rode os prompts **em ordem**, um por sessão (ou um por PR). Cada um tem objetivo, entradas, entregáveis e critérios de aceite.
2. Sempre anexe ao agente o **`01-contrato-de-dados.md`** junto do prompt da etapa. Ele é a fonte da verdade do layout.
3. Antes de começar, preencha a seção **"Parâmetros do seu ambiente"** abaixo e cole-a no início de cada prompt.
4. Deixe os dois CSVs de `docs/` disponíveis no repositório como *fixtures* de teste (`tests/fixtures/`).

| # | Arquivo | O que o agente entrega |
|---|---------|------------------------|
| 00 | `00-README.md` | Este guia |
| 01 | `01-contrato-de-dados.md` | Contrato de dados (layout, semântica, regras). Contexto obrigatório |
| 02 | `02-arquitetura-e-modelo.md` | Arquitetura, estrutura do projeto e DDL das tabelas |
| 03 | `03-download-e-deteccao-de-versao.md` | Coletor: descoberta via API CKAN, download, hash, landing zone |
| 04 | `04-parsing-e-validacao.md` | Parser robusto do CSV + validação estrutural (schema drift) |
| 05 | `05-normalizacao.md` | Transformação para modelo longo (ente × item) e status derivado |
| 06 | `06-historico-e-versionamento.md` | Snapshots semanais, SCD2 e cálculo de mudanças entre semanas |
| 07 | `07-cruzamento-com-entidades.md` | Ligação CAUC ↔ entidades do sistema (IBGE, SIAFI, CNPJ) |
| 08 | `08-qualidade-de-dados.md` | Regras de qualidade, métricas, detecção de anomalias |
| 09 | `09-alertas.md` | Alertas de negócio e operacionais, canais, deduplicação |
| 10 | `10-orquestracao-e-operacao.md` | Agendamento semanal, retries, idempotência, backfill, runbook |
| 11 | `11-testes-e-aceite.md` | Suíte de testes e checklist final de aceite |

## Parâmetros do seu ambiente (preencha antes)

```yaml
stack_linguagem: Python 3.12          # ou: n8n, Node/TypeScript, .NET...
banco_destino: transferepro            # confirmar o SGBD (PostgreSQL assumido)
schema_destino: base_tesouro_nacional   # tabelas com prefixo cauc_
armazenamento_bruto: s3://bucket/cauc/raw   # ou pasta local/rede
orquestrador: cron | Airflow | Prefect | n8n | GitHub Actions
timezone: America/Sao_Paulo
tabela_entidades_sistema: <schema.tabela das suas entidades>
chave_entidade_sistema: <coluna: cnpj | codigo_ibge | codigo_siafi | id interno>
canais_alerta: [email, teams|slack webhook]
responsaveis_alerta: <e-mails / grupos>
itens_criticos_para_negocio: [todos]  # ou lista de itens, ex.: ["1.1","1.5","4.2"]
janela_alerta_vencimento_dias: 15
```

Se você não souber algum valor, deixe como está: o agente deve parar e perguntar apenas o que for bloqueante, e assumir o padrão para o resto.

## Fontes

- Municípios (CSV): `https://www.tesourotransparente.gov.br/ckan/dataset/72b5f371-0c35-4613-8076-c99c821a6410/resource/07af297a-5e59-494a-a88a-55ddfd2f4b01/download/relatorio-situacao-de-varios-entes---municipios---uf-todas---abrangencia-1.csv`
- Estados e DF (página do recurso): `https://www.tesourotransparente.gov.br/ckan/dataset/cauc/resource/83d2fbd7-4369-4935-85c2-366925cc5841`
- Metadados oficiais: `docs/*metadadoscaucestados.pdf` e `docs/*metadadoscaucmunicipios2025.pdf`

## Premissas de ciência de dados que guiam todos os prompts

- **Imutabilidade do bruto**: o arquivo baixado é guardado byte a byte, com hash. Tudo é reprocessável a partir dele.
- **Data de referência vem do arquivo**, não do relógio: a linha `Data da Pesquisa` define a versão.
- **Idempotência**: rodar duas vezes a mesma versão não duplica nada.
- **Falhar alto em mudança de layout**: o Tesouro já incluiu itens novos (ex.: 4.3, 5.8). Coluna nova ou ausente bloqueia a publicação e gera alerta, nunca é ignorada em silêncio.
- **Três estados, não dois**: "regular", "pendente (`!`)" e "não avaliado (`Desabilitado`)". Tratar `Desabilitado` como irregular é erro.
