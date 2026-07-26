import json
import boto3
import os
import logging
import urllib.parse
from decimal import Decimal
from datetime import datetime

# Configurar o logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configurar o endpoint dinamicamente
dynamo_endpoint = os.getenv('DYNAMODB_ENDPOINT', None)
logger.info(f"Conectando ao DynamoDB. Endpoint: {dynamo_endpoint or 'usando endpoint padrão da AWS'}")

# Inicializar cliente do DynamoDB
try:
    dynamodb = boto3.resource('dynamodb', endpoint_url=dynamo_endpoint)
    table = dynamodb.Table('NotasFiscais')
except Exception as e:
    logger.error(f"Erro ao conectar ao DynamoDB: {str(e)}")
    raise e

# Função para validar um registro
def validar_registro(registro):
    campos_obrigatorios = {"id", "cliente", "valor", "data_emissao"}

    if not isinstance(registro, dict):
        return False, "Registro não é um objeto JSON válido."

    campos_faltando = campos_obrigatorios - registro.keys()
    if campos_faltando:
        return False, f"Campos obrigatórios faltando: {campos_faltando}"

    if not isinstance(registro["id"], str):
        return False, "O campo 'id' deve ser uma string."
    if not isinstance(registro["cliente"], str):
        return False, "O campo 'cliente' deve ser uma string."
    if not isinstance(registro["valor"], (int, float, Decimal)):
        return False, "O campo 'valor' deve ser numérico."
    if not isinstance(registro["data_emissao"], str):
        return False, "O campo 'data_emissao' deve ser uma string no formato de data."

    try:
        datetime.strptime(registro["data_emissao"], "%Y-%m-%d")
    except ValueError:
        return False, "O campo 'data_emissao' deve estar no formato YYYY-MM-DD."

    return True, "Registro válido"


# Função para mover o arquivo dentro do S3
def mover_arquivo_s3(s3, bucket, key, destino):
    try:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        nome_arquivo = key.split('/')[-1] # Extrai somente o nome do arquivo sem o caminho
        novo_key = f"{destino}/{timestamp}_{nome_arquivo}"
        logger.info(f"Movendo arquivo para: s3://{bucket}/{novo_key}")

        # Copiar e deletar o arquivo original
        s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=novo_key)
        s3.delete_object(Bucket=bucket, Key=key)
        logger.info(f"Arquivo original deletado do S3: s3://{bucket}/{key}")
    except Exception as e:
        logger.error(f"Erro ao mover o arquivo no S3: {str(e)}")


# Helper para serializar Decimal em respostas JSON
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)


# Função Principal do Lambda
def lambda_handler(event, context):
    s3 = boto3.client('s3', endpoint_url=dynamo_endpoint)

    # 1) Processamento de eventos do S3
    if 'Records' in event and len(event['Records']) > 0 and 's3' in event['Records'][0]:
        for record in event.get('Records', []):
            s3_bucket = record['s3']['bucket']['name']
            s3_key = urllib.parse.unquote_plus(record['s3']['object']['key'], encoding='utf-8')
            logger.info(f"Processando arquivo: s3://{s3_bucket}/{s3_key}")

            # Evitar loop de gatilho ao mover arquivos para pastas de "sucesso/" ou "erro/"
            if s3_key.startswith("sucesso/") or s3_key.startswith("erro/"):
                logger.info(f"Arquivo '{s3_key}' já processado na pasta de destino, ignorando.")
                continue

            try:
                # Ler o arquivo do S3
                response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
                file_content = response['Body'].read().decode('utf-8')

                # Carregar o conteúdo como JSON
                try:
                    registros = json.loads(file_content, parse_float=Decimal)
                    if isinstance(registros, dict):
                        registros = [registros]
                    logger.info(f"Arquivo JSON carregado com sucesso. Total de registros: {len(registros)}")
                except json.JSONDecodeError as e:
                    logger.error(f"Erro ao decodificar o JSON: {str(e)}")
                    mover_arquivo_s3(s3, s3_bucket, s3_key, "erro")
                    continue

                # Processar cada registro
                erro_no_processamento = False
                for registro in registros:
                    valido, mensagem = validar_registro(registro)
                    if not valido:
                        logger.warning(f"Registro inválido: {mensagem}")
                        erro_no_processamento = True
                        break

                    try:
                        logger.info(f"Inserindo registro no DynamoDB: {registro}")
                        table.put_item(Item=registro)
                        logger.info("Registro inserido com sucesso!")
                    except Exception as e:
                        logger.error(f"Erro ao inserir registro no DynamoDB: {str(e)}")
                        erro_no_processamento = True
                        break

                # Mover arquivo para pasta apropriada no S3
                if erro_no_processamento:
                    mover_arquivo_s3(s3, s3_bucket, s3_key, "erro")
                else:
                    mover_arquivo_s3(s3, s3_bucket, s3_key, "sucesso")

            except Exception as e:
                logger.error(f"Erro inesperado ao processar o arquivo: {str(e)}")
                mover_arquivo_s3(s3, s3_bucket, s3_key, "erro")

        return {
            'statusCode': 200,
            'body': json.dumps('Processamento S3 concluído com sucesso!')
        }

    # 2) Processamento via API Gateway (HTTP POST / GET)
    elif 'httpMethod' in event:
        http_method = event.get('httpMethod')
        logger.info(f"Requisição recebida via API Gateway: {http_method}")

        if http_method == 'POST':
            try:
                body = event.get('body')
                if isinstance(body, str):
                    dados = json.loads(body, parse_float=Decimal)
                else:
                    dados = body or {}

                if isinstance(dados, dict):
                    dados = [dados]

                resultados = []
                for registro in dados:
                    valido, mensagem = validar_registro(registro)
                    if not valido:
                        return {
                            'statusCode': 400,
                            'headers': {'Content-Type': 'application/json'},
                            'body': json.dumps({'erro': f"Registro inválido: {mensagem}"})
                        }
                    table.put_item(Item=registro)
                    resultados.append(registro)

                return {
                    'statusCode': 201,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'mensagem': 'Nota(s) fiscal(is) cadastrada(s) com sucesso!',
                        'registros': resultados
                    }, cls=DecimalEncoder)
                }
            except Exception as e:
                logger.error(f"Erro no processamento POST API Gateway: {str(e)}")
                return {
                    'statusCode': 500,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'erro': str(e)})
                }

        elif http_method == 'GET':
            try:
                query_params = event.get('queryStringParameters') or {}
                if 'id' in query_params:
                    response = table.get_item(Key={'id': query_params['id']})
                    item = response.get('Item')
                    if not item:
                        return {
                            'statusCode': 404,
                            'headers': {'Content-Type': 'application/json'},
                            'body': json.dumps({'erro': 'Nota fiscal não encontrada'})
                        }
                    return {
                        'statusCode': 200,
                        'headers': {'Content-Type': 'application/json'},
                        'body': json.dumps(item, cls=DecimalEncoder)
                    }
                else:
                    response = table.scan()
                    items = response.get('Items', [])
                    return {
                        'statusCode': 200,
                        'headers': {'Content-Type': 'application/json'},
                        'body': json.dumps(items, cls=DecimalEncoder)
                    }
            except Exception as e:
                logger.error(f"Erro no processamento GET API Gateway: {str(e)}")
                return {
                    'statusCode': 500,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'erro': str(e)})
                }

    # 3) Fallback para chamadas diretas sem evento formatado
    return {
        'statusCode': 200,
        'body': json.dumps('Processamento concluído com sucesso!')
    }