# LLM Music API

API Node.js containerizada para identificação de músicas e retorno de letras utilizando modelos de linguagem locais fine-tuned com LoRA/QLoRA.

## 📋 Requisitos

### Hardware Mínimo
- **CPU:** 4+ cores
- **RAM:** 16GB (8GB mínimo com quantização 8-bit)
- **Disco:** 20GB+ para modelo e dependências
- **GPU:** Opcional (NVIDIA com 8GB+ VRAM acelera inferência)

### Software
- Docker 20.10+
- Docker Compose 2.0+
- (Opcional) NVIDIA Docker para GPU

## 🏗️ Arquitetura

```
┌─────────────────────────────────────────────┐
│           Cliente HTTP/REST API             │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│         Servidor Fastify (Node.js)          │
│  - Endpoints REST                           │
│  - Validação de entrada                     │
│  - Cache em memória                         │
│  - Orquestração de inferência               │
└─────────────────┬───────────────────────────┘
                  │
                  ▼ (spawn subprocess)
┌─────────────────────────────────────────────┐
│      Script Python (model_inference.py)     │
│  - Carregamento do modelo LLM               │
│  - Suporte a LoRA/QLoRA                     │
│  - Quantização (4-bit/8-bit)                │
│  - Geração de texto estruturado             │
└─────────────────────────────────────────────┘
                  │
                  ▼
         ┌────────────────┐
         │  Modelo local  │
         │  (Mistral/Phi) │
         └────────────────┘
```

## 🚀 Quick Start

### Modo dev sem rebuild a cada alteração

```bat
start-dev.bat
```

No `bash`/Git Bash:

```bash
./start-dev.sh
```

Esse fluxo sobe os containers com bind mount do `src/` e roda a API Node com `node --watch`.
Mudanças em `llm-music-api/src/*.js` recarregam automaticamente sem `docker compose build`.

### 1. Clonar e Configurar

```bash
cd llm-music-api

# Copiar arquivo de configuração
cp .env.example .env

# Editar configurações conforme necessário
nano .env
```

### 2. Preparar Modelo

Coloque seu modelo fine-tuned na pasta `models/`:

```bash
mkdir -p models/mistral-7b-music-lora
mkdir -p models/lora-weights

# Copiar modelo base (Mistral, Phi, LLaMA, etc.)
# Exemplo: baixar do HuggingFace ou copiar de treinamento local
# Os arquivos devem incluir:
# - config.json
# - tokenizer.json, tokenizer_config.json
# - pytorch_model.bin ou model.safetensors
# - (Opcional) adapter_config.json e adapter_model.bin para LoRA

# Estrutura esperada:
# models/
# ├── mistral-7b-music-lora/
# │   ├── config.json
# │   ├── tokenizer.json
# │   ├── tokenizer_config.json
# │   └── model.safetensors
# └── lora-weights/
#     ├── adapter_config.json
#     └── adapter_model.bin
```

**Download de modelo de exemplo (Mistral-7B-Instruct):**

```bash
# Instalar huggingface-cli
pip install huggingface-hub

# Baixar modelo
huggingface-cli download mistralai/Mistral-7B-Instruct-v0.2 \
  --local-dir models/mistral-7b-music-lora \
  --local-dir-use-symlinks False
```

### 3. Build e Executar

```bash
# Build da imagem Docker
docker compose build

# Iniciar serviço
docker compose up -d

# Ver logs
docker compose logs -f

# Verificar saúde
curl http://localhost:3000/health
```

### 4. Testar API

```bash
# Exemplo 1: Identificar música por descrição
curl -X POST http://localhost:3000/identify \
  -H "Content-Type: application/json" \
  -d '{
    "query": "música sobre liberdade com um longo solo de guitarra, rock clássico dos anos 70"
  }'

# Exemplo 2: Com contexto adicional
curl -X POST http://localhost:3000/identify \
  -H "Content-Type: application/json" \
  -d '{
    "query": "qual é essa música?",
    "context": {
      "lyric_snippet": "I'\''m as free as a bird now",
      "artist_hint": "Lynyrd Skynyrd",
      "genre": "rock"
    }
  }'

# Exemplo 3: Batch de consultas
curl -X POST http://localhost:3000/identify/batch \
  -H "Content-Type: application/json" \
  -d '{
    "queries": [
      {"query": "música sobre dinheiro crescendo em árvores"},
      {"query": "canção japonesa dos anos 80 sobre ficar comigo"}
    ]
  }'
```

## 📡 Endpoints da API

### `GET /health`
Verifica o status do servidor.

**Resposta:**
```json
{
  "status": "healthy",
  "timestamp": "2026-04-18T12:00:00.000Z",
  "uptime": 123.456
}
```

### `POST /identify`
Identifica uma música e retorna suas informações.

**Request Body:**
```json
{
  "query": "string (obrigatório)",
  "context": {
    "lyric_snippet": "string (opcional)",
    "artist_hint": "string (opcional)",
    "genre": "string (opcional)"
  }
}
```

**Resposta:**
```json
{
  "success": true,
  "data": {
    "song": "Free Bird",
    "artist": "Lynyrd Skynyrd",
    "album": "Pronounced 'Lĕh-'nérd 'Skin-'nérd",
    "lyrics": "If I leave here tomorrow...",
    "confidence": 0.95
  },
  "metadata": {
    "inference_time_ms": 2345,
    "model": "mistralai/Mistral-7B-Instruct-v0.2",
    "timestamp": "2026-04-18T12:00:00.000Z"
  }
}
```

### `POST /identify/batch`
Processa múltiplas consultas em batch (máximo 10).

**Request Body:**
```json
{
  "queries": [
    {"query": "string", "context": {}},
    {"query": "string", "context": {}}
  ]
}
```

### `POST /cache/clear`
Limpa o cache de respostas em memória.

## ⚙️ Configuração

Edite o arquivo `.env`:

```bash
# API
PORT=3000
NODE_ENV=production

# Modelo
MODEL_NAME=mistralai/Mistral-7B-Instruct-v0.2
MODEL_PATH=/app/models/mistral-7b-music-lora
USE_LORA=true
LORA_WEIGHTS_PATH=/app/models/lora-weights

# Geração
MAX_LENGTH=512          # Tokens máximos na resposta
TEMPERATURE=0.7         # 0.0 = determinístico, 1.0 = criativo
TOP_P=0.95              # Nucleus sampling

# Hardware
USE_GPU=false           # true para usar GPU
DEVICE=cpu              # cpu, cuda, mps
LOAD_IN_8BIT=true       # Quantização 8-bit (economiza RAM)
```

### Usando GPU (NVIDIA)

1. Instalar NVIDIA Docker:
```bash
# Ubuntu/Debian
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | \
  sudo tee /etc/apt/sources.list.d/nvidia-docker.list
sudo apt-get update && sudo apt-get install -y nvidia-docker2
sudo systemctl restart docker
```

2. Descomentar seção GPU no `docker-compose.yml`:
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: 1
          capabilities: [gpu]
```

3. Atualizar `.env`:
```bash
USE_GPU=true
DEVICE=cuda
LOAD_IN_8BIT=false  # GPU tem mais VRAM
```

## 🧪 Desenvolvimento Local (sem Docker)

```bash
# Instalar dependências Node.js
npm install

# Instalar dependências Python
pip install -r requirements.txt

# Configurar variáveis de ambiente
cp .env.example .env
nano .env

# Iniciar servidor
npm start

# Ou modo desenvolvimento (com hot-reload)
npm run dev
```

## 🎵 Download de Álbuns para Treino

Use o script de download para salvar áudio no formato recomendado:

`training_audio/{artist}/{album}/{song_file}`

Exemplo básico:

```bash
python download_training_audio.py "URL_DA_PLAYLIST_OU_ALBUM"
```

Com artista/álbum fixos:

```bash
python download_training_audio.py "URL" --artist "Kendrick Lamar" --album "good kid, m.A.A.d city"
```

Somente limpeza/reorganização (sem baixar):

```bash
python download_training_audio.py --clean-only
```

Limpar também arquivos já existentes na pasta:

```bash
python download_training_audio.py --clean-existing --clean-only
```

Principais flags:

- `--clean-only`: pula o download e executa só limpeza/reorganização.
- `--clean-existing`: processa todos os arquivos já presentes no output-root.
- `--no-llm-cleanup`: desativa chamada ao endpoint `/clean-metadata` e usa apenas heurística local/catálogo.
- `--llm-url`: define URL do endpoint de limpeza de metadados.
- `--llm-timeout`: timeout em segundos para limpeza via LLM.

Observação: o script aplica cache em memória durante a execução para evitar chamadas LLM repetidas para os mesmos metadados.

## 🎯 Escolha do Modelo Base

### Recomendações por hardware:

| Modelo | Parâmetros | RAM Mínima | GPU VRAM | Velocidade | Qualidade |
|--------|------------|------------|----------|------------|-----------|
| **Phi-3-mini** | 3.8B | 8GB | 4GB | ⚡⚡⚡ | ⭐⭐⭐ |
| **Mistral-7B** | 7B | 16GB | 8GB | ⚡⚡ | ⭐⭐⭐⭐ |
| **LLaMA-2-7B** | 7B | 16GB | 8GB | ⚡⚡ | ⭐⭐⭐⭐ |
| **Mixtral-8x7B** | 47B | 32GB+ | 24GB | ⚡ | ⭐⭐⭐⭐⭐ |

### Configuração para hardware limitado (8-16GB RAM):

```bash
MODEL_NAME=microsoft/Phi-3-mini-4k-instruct
LOAD_IN_8BIT=true
DEVICE=cpu
MAX_LENGTH=256  # Reduzir comprimento máximo
```

## 🔧 Fine-tuning do Modelo

Para treinar seu próprio modelo de identificação de músicas:

### 1. Preparar Dataset

Formato JSON:
```json
[
  {
    "instruction": "Identifique a música baseado na descrição.",
    "input": "música sobre liberdade com solo de guitarra longo",
    "output": "{\"song\": \"Free Bird\", \"artist\": \"Lynyrd Skynyrd\", \"album\": \"...\", \"lyrics\": \"...\", \"confidence\": 0.95}"
  }
]
```

### 2. Script de Treinamento (exemplo com LoRA)

```python
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer

# Configuração LoRA
lora_config = LoraConfig(
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "v_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM"
)

# Carregar modelo base
model = AutoModelForCausalLM.from_pretrained(
    "mistralai/Mistral-7B-Instruct-v0.2",
    load_in_8bit=True,
    device_map="auto"
)
model = prepare_model_for_kbit_training(model)
model = get_peft_model(model, lora_config)

# Treinar
trainer = SFTTrainer(
    model=model,
    train_dataset=dataset,
    max_seq_length=2048,
    # ... outras configurações
)
trainer.train()

# Salvar adaptadores LoRA
model.save_pretrained("./lora-weights")
```

## 📊 Performance e Otimização

### Benchmarks (Mistral-7B, CPU 16GB):
- **Tempo de carregamento:** ~30s
- **Primeira inferência:** ~15s
- **Inferências subsequentes:** ~5-8s
- **Memória:** ~12GB RAM

### Dicas de otimização:

1. **Quantização:** Use 8-bit ou 4-bit para reduzir uso de RAM
2. **Cache:** Respostas são cacheadas automaticamente
3. **Batch processing:** Use `/identify/batch` para múltiplas consultas
4. **GPU:** Acelera inferência em ~5-10x
5. **Compilação:** (Opcional) Use `torch.compile()` no Python 3.11+

## 🐛 Troubleshooting

### Erro: "Out of memory"
- Reduza `MAX_LENGTH`
- Habilite `LOAD_IN_8BIT=true`
- Use modelo menor (Phi-3-mini)
- Aumente RAM do container no `docker-compose.yml`

### Erro: "Model not found"
- Verifique se o modelo está em `./models/`
- Confira `MODEL_PATH` no `.env`
- Execute `ls -la models/` para verificar arquivos

### Inferência muito lenta
- Habilite GPU se disponível
- Use quantização (8-bit ou 4-bit)
- Reduza `MAX_LENGTH`
- Considere modelo menor

### Erro: "Python process exited with code 1"
- Verifique logs: `docker compose logs`
- Teste script Python manualmente: `python3 src/model_inference.py --query "test"`
- Verifique dependências: `pip list`

## 📝 Estrutura do Projeto

```
llm-music-api/
├── Dockerfile                 # Imagem Docker com Node.js + Python
├── docker-compose.yml         # Orquestração de serviços
├── docker-compose.dev.yml     # Override dev com bind mount + watch mode
├── start-dev.bat              # Sobe stack dev sem rebuild do src/
├── package.json               # Dependências Node.js
├── requirements.txt           # Dependências Python
├── .env.example               # Configurações de exemplo
├── .dockerignore              # Arquivos ignorados no build
├── .gitignore                 # Arquivos ignorados no Git
├── README.md                  # Esta documentação
├── src/
│   ├── server.js              # Servidor Fastify
│   └── model_inference.py     # Script de inferência Python
├── models/                    # Modelos LLM (não versionado)
│   ├── mistral-7b-music-lora/
│   └── lora-weights/
└── logs/                      # Logs da aplicação (opcional)
```

## 🤝 Contribuindo

1. Fork o projeto
2. Crie uma branch: `git checkout -b feature/nova-funcionalidade`
3. Commit: `git commit -am 'Adiciona nova funcionalidade'`
4. Push: `git push origin feature/nova-funcionalidade`
5. Abra um Pull Request

## 📄 Licença

MIT License - veja LICENSE para detalhes

## 🔗 Recursos Úteis

- [Hugging Face Transformers](https://huggingface.co/docs/transformers)
- [PEFT (LoRA/QLoRA)](https://huggingface.co/docs/peft)
- [Fastify Documentation](https://www.fastify.io/)
- [Docker Documentation](https://docs.docker.com/)
- [bitsandbytes (Quantização)](https://github.com/TimDettmers/bitsandbytes)

## ⚠️ Notas Importantes

1. **Modelos pré-treinados:** Esta API assume que você já possui um modelo fine-tuned. Para melhores resultados, fine-tune um modelo base com seu próprio dataset de músicas.

2. **Compliance:** Certifique-se de ter os direitos necessários para distribuir letras de músicas. Esta é uma ferramenta de demonstração técnica.

3. **Recursos:** Modelos de linguagem são intensivos em recursos. Monitore uso de CPU/RAM/GPU.

4. **Segurança:** Em produção:
   - Configure CORS adequadamente
   - Adicione autenticação (JWT, API keys)
   - Implemente rate limiting
   - Use HTTPS

---

**Desenvolvido com ❤️ para a comunidade de ML**
