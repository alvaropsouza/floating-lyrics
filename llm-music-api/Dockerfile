# Multi-stage build para otimização de tamanho
FROM node:20-bullseye-slim AS base

# Instalar dependências do sistema necessárias para Python e bibliotecas ML
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    build-essential \
    curl \
    git \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar arquivos de dependências
COPY package*.json ./
COPY requirements.txt ./

# Instalar dependências Node.js
RUN npm install --omit=dev

# Instalar dependências Python
# Para CPU-only, use a versão CPU do PyTorch
RUN pip3 install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip3 install --no-cache-dir -r requirements.txt

# Copiar código fonte
COPY . .

# Criar diretório para modelos (será montado como volume)
RUN mkdir -p /app/models

# Expor porta da API
EXPOSE 3000

# Variáveis de ambiente padrão
ENV NODE_ENV=production \
    PORT=3000 \
    PYTHONUNBUFFERED=1

# Comando de inicialização
CMD ["node", "src/server.js"]
