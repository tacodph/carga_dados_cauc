# 03 · Download e detecção de nova versão

## Papel
Engenheiro de dados responsável pela coleta. Contexto: `01-contrato-de-dados.md`, modelo do prompt 02.

## Objetivo
Implementar `collect.py`: descobrir a URL atual de cada recurso, baixar com segurança, guardar o bruto imutável e decidir se é uma versão nova.

## Requisitos

### Descoberta (não fixar URL de download)
1. Consultar a API CKAN: `GET https://www.tesourotransparente.gov.br/ckan/api/3/action/resource_show?id=<resource_id>` para cada recurso:
   - municípios `07af297a-5e59-494a-a88a-55ddfd2f4b01`
   - estados/DF `83d2fbd7-4369-4935-85c2-366925cc5841`
2. Usar `result.url` como URL de download e guardar `last_modified`, `size` e `format` no manifesto.
3. Fallback: se a API falhar, usar `package_show?id=cauc` e localizar o recurso pelo nome (`municipios` / `estados-e-df`). Último fallback: URL fixa do município informada no README.
4. Se o resource id deixar de existir ou o recurso mudar de formato (≠ CSV), **falhar com alerta operacional**. Não tentar adivinhar.

### Download
- HTTP com timeout (conexão 30 s, leitura 300 s), `User-Agent` identificando o sistema (genérico, sem e-mail pessoal), até 5 tentativas com backoff exponencial e jitter em erros 5xx/429/timeouts.
- Download em streaming para arquivo temporário; só mover para o destino final após concluir e calcular `sha256`.
- Rejeitar arquivo vazio, menor que 5 KB (estados) ou 1 MB (municípios), ou com `Content-Type` HTML (página de erro do portal).
- Suportar proxy corporativo via variáveis de ambiente.

### Landing zone (raw)
- Caminho: `raw/tipo_ente=<MUNICIPIO|ESTADO>/data_referencia=<AAAA-MM-DD>/<sha256>.csv` mais `manifest.json` ao lado.
- Para obter `data_referencia` antes de parsear tudo, leia apenas a **primeira linha** decodificando em latin-1 (`Data da Pesquisa: dd/mm/aaaa`).
- Raw é **write-once**: nunca sobrescrever. Política de retenção mínima: 5 anos (configurável).

### Decisão de versão
| Situação | Ação |
|---|---|
| `(tipo_ente, data_referencia, sha256)` já existe | Registrar "sem novidade" no log e encerrar com sucesso (idempotência) |
| `data_referencia` nova | Seguir pipeline |
| Mesma `data_referencia`, **sha256 diferente** | Republicação do Tesouro: processar como nova versão; a anterior vira `SUBSTITUIDO` após publicar a nova; gerar alerta informativo |
| `data_referencia` **menor** que a última publicada | Rejeitar e alertar (arquivo regrediu) |
| `data_referencia` igual à da semana anterior no dia esperado | Arquivo ainda não atualizado: aplicar política de nova tentativa (prompt 10) |

### Os dois recursos
- Baixe municípios e estados de forma independente. A falha de um não impede o outro, mas o status da execução fica `PARCIAL` e gera alerta.

## Entregáveis
- `collect.py` com função `coletar(tipo_ente) -> ResultadoColeta(caminho, sha256, data_referencia, nova_versao: bool, manifesto)`.
- Testes com servidor HTTP falso: sucesso, 503 seguido de sucesso, HTML no lugar de CSV, arquivo truncado, mesmo hash, hash diferente com mesma data.

## Critérios de aceite
- Executar duas vezes seguidas não cria segunda versão nem segunda linha em `carga_arquivo`.
- Nenhuma escrita no raw quando o download falha no meio.
- Os CSVs de `docs/` colocados como resposta do servidor falso resultam em `data_referencia = 2026-10-30`.
