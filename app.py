from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import SQLAlchemyError
import json
import os
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


# --- 2. CONFIGURAÇÃO DO FLASK E SQLALCHEMY (MySQL) ---

app = Flask(__name__)

# Configuração para MySQL - Use Variáveis de Ambiente ou substitua com seus dados
DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "sua_senha_secreta")
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_NAME = os.getenv("MYSQL_DATABASE", "triagem_db")

# URI de conexão para MySQL: mysql+pymysql://user:password@host/database
app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- 3. MODELO DE DADOS SQL ---

class Caso(db.Model):
    """Define a tabela 'caso' no banco de dados MySQL."""
    id = db.Column(db.Integer, primary_key=True)
    area_problema = db.Column(db.String(100), nullable=False)
    fatos_chave = db.Column(db.Text, nullable=False)
    urgencia = db.Column(db.String(20), nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.now())
    # Status que a tela de fila do especialista irá ler
    status = db.Column(db.String(50), default='PENDENTE_ESPECIALISTA') 

    def __repr__(self):
        return f'<Caso {self.id} - {self.area_problema}>'

# --- 4. FUNÇÃO DE ORQUESTRAÇÃO DA LLM ---

def call_llm_api(user_message):
    """Chama a Gemini API para triagem estruturada."""
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

        return json.loads(response.text)
        
    except Exception as e:
        print(f"❌ ERRO GRAVE ao chamar a LLM: {e}")
        return None

# --- 5. FUNÇÃO DE PERSISTÊNCIA SQL ---

def persist_case_to_sql(case_data):
    """Cria uma nova linha no MySQL, marcando-a como PENDENTE_ESPECIALISTA."""
    try:
        novo_caso = Caso(
            area_problema=case_data.get('area_problema'),
            fatos_chave=case_data.get('fatos_chave'),
            urgencia=case_data.get('urgencia')
        )
        
        db.session.add(novo_caso)
        db.session.commit()
        print(f"[MYSQL] Caso {novo_caso.id} registrado para atendimento de especialista.")
        return True
    
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"❌ ERRO MYSQL ao inserir caso: {e}")
        return False

# --- 6. ROTAS DO FLASK ---

@app.route("/chat")
def chat_page():
    """ Rota GET: Apenas renderiza a interface do chat (chat.html) """
    # Nota: O arquivo 'chat.html' deve estar na pasta 'templates/'
    return render_template("chat.html")

@app.route('/send_message', methods=['POST'])
def process_chat_message():
    """ Rota POST: Executa o pipeline SINCRONO: Receber -> LLM -> SQL -> Responder """
    
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
        
        # PERSISTÊNCIA E DIRECIONAMENTO
        if not persist_case_to_sql(processed_data):
            return jsonify({"error": "Falha ao registrar e direcionar o caso no banco de dados."}), 500
        
        # RESPOSTA ao Usuário, confirmando o encaminhamento para o especialista
        success_message = (
            f"✅ Triagem Concluída! Sua solicitação na área de **{area_buscada}** foi registrada e "
            "**direcionada para a fila de atendimento de um especialista**. "
            "Você será contatado(a) por ele(a) em breve."
        )
        
        return jsonify({
            "status": "success", 
            "response_text": success_message,
            "area_classified": area_buscada,
            "urgency": processed_data.get('urgencia')
        }), 200
        
    else:
        return jsonify({"error": "Houve uma falha na triagem do sistema. Por favor, tente novamente mais tarde."}), 500


if __name__ == '__main__':
    # Cria as tabelas do banco de dados antes de iniciar o servidor
    with app.app_context():
        db.create_all()
    
    print("--- INICIANDO FLASK SERVER (Com MySQL) ---")
    app.run(debug=True, port=5000)