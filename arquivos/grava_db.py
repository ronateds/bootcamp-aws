import json
import boto3
import os
import logging
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

    if not isinstance(regitro["id"], str):
        return False, f"O campo 'id' deve ser uma string."
    if not isinstance(regitro["cliente"], str):
        return False, f"O campo 'cliente' deve ser uma string."
    if not isinstance(regitro["valor"], (int, float, Decimal)):
        return False, f"O campo 'valor' deve ser numérico."
    if not isinstance(regitro["data_emissao"], str):
        return False, f"O campo 'data_emissao' deve ser uma string no formato de data."

    return True, "REgistro válido"


# Função para mover o arquivo dentro do S3
def mover_arquivo_s3(s3, bucket, key, destino):
    try:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        nome_arquivo = key.split('/')[-1] # Extrai somente o nome do arquivo sem o caminho
        novo_key = f"{destino}/{timestamp}_{nome_arquivo}"
        logger.info(f"Movendo arquivo para: s3://{bucket}/{novo_key}")

        # Copiar e deletar o arquivo original
        s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=novo_key)
    except Exception as e:
        logger.error(f"Erro ao mover o arquivo no S3: {str(e)}")


# Função Principal do Lambda
def lambda_handler(event, context):
    s3 = boto3.client('s3')

    for record in event.get('Records', []):
        s3_bucket = record['s3']['bucket']['name']
        s3_key = record['s3']['object']['key']
        logger.info(f"Processando arquivo: s3://{s3_bucket}/{s3_key}")

    try:
        # Ler o arquivo do S3
        response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
        file_content = response['Body'].read().decode('utf-8')

        # Carregar o conteúdo como JSON
        try:
            registros = json.loads(file_content, parse_float=Decimal)
            logger.info(f"Arquivo JSON carregado com sucesso. Total de registros: {len(registros)}")
        except json.JSONDecodeError as e:
            logger.error(f"Erro ao decodificar o JSON: {str(e)}")
            mover_arquivo_s3(s3, s3_bucket, s3_key, "erro")
            continue

        # Processar cada registro
        for registro in registros:
            valido, mensagem = validar_registro(registro)
            if not valido:
                logger.warning(f"Registro inválido: {mensagem}")
                continue

            try:
                logger.info(f"Inserindo registro no DynamoDB: {registro}")
                table.put_item(Item=registro)
                logger.info("Registro inserido com sucesso!")
            except Exception as e:
                logger.error(f"Erro ao inserir registro no DynamoDB: {str(e)}")
                mover_arquivo_s3(s3, s3_bucket, s3_key, "erro")
                break

        # Mover arquivo para pasta de sucesso após processamento completo
        else:
            mover_arquivo_s3(s3, s3_bucket, s3_key, "sucesso")

    except Exception as e:
        logger.error(f"Erro inesperado ao processar o arquivo: {str(e)}")
        mover_arquivo_s3(s3, s3_bucket, s3_key, "erro")

return {
    'statusCode': 200,
    'body': json.dumps('Processamento concluído com sucesso!')
}