from flask import Flask, request, jsonify, render_template
import json
import os
import redis
from rq import Queue
from .tasks import process_new_case_job
from pydantic import BaseModel, Field

# Importações do Gemini
from google import genai
from google.genai import types

# --- 1. CONFIGURAÇÃO DA LLM (Pydantic Schema) ---

class TriageOutput(BaseModel):
    """Define a estrutura JSON de saída que a LLM deve seguir."""
    area_problema: str = Field(description="Categoria principal do problema (ex: 'Direito Civil', 'Saúde', 'Trabalhista').")
    fatos_chave: str = Field(description="Resumo conciso de 1-2 frases dos fatos mais relevantes.")
    urgencia: str = Field(description="Classificação da urgência (ex: 'Alta', 'Média', 'Baixa').")


# --- 2. CONFIGURAÇÃO DA FILA (RQ/Redis) ---

# Conexão REAL com o servidor Redis (porta e host padrão)
redis_conn = redis.Redis()
# Cria uma fila chamada 'cases'
q = Queue('cases', connection=redis_conn)

def process_new_case_job(case_data):
    """
    ⚠️ Esta é a função do WORKER. Ela será executada pelo sistema do especialista 
    quando ele puxar a tarefa da fila.
    """
    print("--- INICIANDO PROCESSAMENTO DO CASO PELO WORKER ---")
    print(f"Área Classificada: {case_data.get('area_problema')}")
    print(f"Resumo do Caso: {case_data.get('fatos_chave')}")
    # A lógica REAL de inserção no DB do especialista e envio de notificação entra aqui.
    print("--- CASO PROCESSADO E ENVIADO PARA ATENDIMENTO ---")

def enqueue_case(case_data):
    # ...
    # q.enqueue(process_new_case_job, case_data) # <--- Continua usando a função importada
    # ...


# --- 3. FUNÇÃO DE ORQUESTRAÇÃO DA LLM ---

def call_llm_api(user_message):
    """
    Chama a Gemini API para triagem estruturada usando Pydantic.
    """
    try:
        if not os.getenv("GEMINI_API_KEY"):
            raise ValueError("A variável de ambiente GEMINI_API_KEY não está configurada.")
            
        client = genai.Client()
        
        system_prompt = (
            "Você é um sistema de triagem inteligente. Sua única função é analisar a mensagem do usuário e "
            "extrair as informações solicitadas no formato JSON exato. Não adicione nenhum outro texto."
        )

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=TriageOutput,
            ),
        )

        # Retorna o dicionário Python do JSON da LLM
        return json.loads(response.text)
    
    except Exception as e:
        print(f"❌ ERRO GRAVE ao chamar a LLM: {e}")
        return None

# --- 4. ROTAS DO FLASK ---

app = Flask(__name__)

@app.route("/chat")
def chat_page():
    """ Rota GET: Apenas renderiza a interface do chat (chat.html) """
    return render_template("chat.html")

@app.route('/send_message', methods=['POST'])
def process_chat_message():
    """ Rota POST: Executa o pipeline completo: Receber -> LLM -> Enfileirar -> Responder """
    
    try:
        data = request.get_json()
        user_message = data.get('message')
    except Exception:
        return jsonify({"error": "Formato JSON inválido"}), 400

    if not user_message:
        return jsonify({"error": "O campo 'message' está ausente"}), 400

    print(f"\n[RECEBIDO] Mensagem do usuário: {user_message}")

    # CHAMA A LLM
    processed_data = call_llm_api(user_message)
    
    if processed_data:
        area_buscada = processed_data.get('area_problema', 'Não Classificado')
        
        # ROTEAMENTO: Enfileira o caso
        enqueue_case(processed_data)
        
        # RESPOSTA ao Usuário
        success_message = (
            f"Sua solicitação foi classificada como **{area_buscada}** e enviada a um especialista. "
            "O mesmo entrará em contato em breve!"
        )
        
        return jsonify({
            "status": "success", 
            "response_text": success_message,
            "area_classified": area_buscada
        }), 200
        
    else:
        return jsonify({"error": "Houve uma falha na triagem do sistema. Por favor, tente novamente mais tarde."}), 500


if __name__ == '__main__':
    # Certifique-se de que você tem um arquivo 'chat.html' na pasta 'templates/'
    # E que o Redis Server está rodando!
    print("--- INICIANDO FLASK SERVER ---")
    app.run(debug=True, port=5000)