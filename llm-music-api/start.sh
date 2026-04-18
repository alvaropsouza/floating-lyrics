#!/bin/bash

# Script para build e execução rápida do container Docker
# Uso: ./start.sh

set -e

echo "🚀 Starting LLM Music API..."
echo ""

# Verificar se .env existe
if [ ! -f .env ]; then
    echo "⚠️  .env not found. Copying from .env.example..."
    cp .env.example .env
    echo "✅ Please edit .env with your configurations and run this script again."
    exit 1
fi

# Verificar se pasta models existe
if [ ! -d "./models" ]; then
    echo "📁 Creating models directory..."
    mkdir -p ./models/mistral-7b-music-lora
    mkdir -p ./models/lora-weights
    echo "⚠️  Please download your model to ./models/ before starting!"
    echo ""
    echo "Example:"
    echo "  huggingface-cli download mistralai/Mistral-7B-Instruct-v0.2 \\"
    echo "    --local-dir ./models/mistral-7b-music-lora \\"
    echo "    --local-dir-use-symlinks False"
    echo ""
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "🔨 Building Docker image..."
docker-compose build

echo ""
echo "🐳 Starting containers..."
docker-compose up -d

echo ""
echo "⏳ Waiting for service to be ready..."
sleep 5

# Verificar saúde
echo "🏥 Checking health..."
for i in {1..10}; do
    if curl -s http://localhost:3000/health > /dev/null 2>&1; then
        echo "✅ Service is healthy!"
        echo ""
        echo "📡 API available at: http://localhost:3000"
        echo ""
        echo "📊 View logs:"
        echo "  docker-compose logs -f"
        echo ""
        echo "🛑 Stop service:"
        echo "  docker-compose down"
        exit 0
    fi
    echo "   Attempt $i/10..."
    sleep 3
done

echo "❌ Service failed to start. Check logs:"
echo "  docker-compose logs"
exit 1
