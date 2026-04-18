# Arquitetura Técnica Detalhada

## 🏛️ Visão Geral da Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                     Cliente (HTTP/REST)                      │
│                  (curl, Postman, Frontend)                   │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Docker Container                          │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              Fastify Server (Node.js)                 │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  Express/Fastify App                            │  │  │
│  │  │  - Routing                                       │  │  │
│  │  │  - Validation                                    │  │  │
│  │  │  - Error handling                                │  │  │
│  │  │  - Response caching (in-memory Map)             │  │  │
│  │  └────────────────┬────────────────────────────────┘  │  │
│  │                   │                                    │  │
│  │                   ▼                                    │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  Subprocess Manager (child_process.spawn)       │  │  │
│  │  │  - Process lifecycle                             │  │  │
│  │  │  - Stdin/Stdout communication                    │  │  │
│  │  │  - JSON parsing                                  │  │  │
│  │  └────────────────┬────────────────────────────────┘  │  │
│  └───────────────────┼────────────────────────────────────┘  │
│                      │                                        │
│                      ▼                                        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │         Python Inference Engine                      │    │
│  │  ┌──────────────────────────────────────────────┐   │    │
│  │  │  Model Loader (Transformers + PEFT)          │   │    │
│  │  │  - Load base model                            │   │    │
│  │  │  - Apply LoRA adapters (if enabled)          │   │    │
│  │  │  - Quantization (8-bit/4-bit)                │   │    │
│  │  └──────────────────┬───────────────────────────┘   │    │
│  │                     │                                │    │
│  │                     ▼                                │    │
│  │  ┌──────────────────────────────────────────────┐   │    │
│  │  │  Tokenizer                                    │   │    │
│  │  │  - Text → Token IDs                          │   │    │
│  │  │  - Padding, truncation                       │   │    │
│  │  └──────────────────┬───────────────────────────┘   │    │
│  │                     │                                │    │
│  │                     ▼                                │    │
│  │  ┌──────────────────────────────────────────────┐   │    │
│  │  │  LLM Model (Mistral/Phi/LLaMA)               │   │    │
│  │  │  - Forward pass                               │   │    │
│  │  │  - Sampling (temperature, top-p, top-k)      │   │    │
│  │  │  - Token generation                           │   │    │
│  │  └──────────────────┬───────────────────────────┘   │    │
│  │                     │                                │    │
│  │                     ▼                                │    │
│  │  ┌──────────────────────────────────────────────┐   │    │
│  │  │  Response Formatter                           │   │    │
│  │  │  - JSON extraction                            │   │    │
│  │  │  - Validation                                 │   │    │
│  │  │  - Error handling                             │   │    │
│  │  └──────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Persistent Storage                        │
│                 (Docker Volume - models/)                    │
│  - Base model weights                                        │
│  - LoRA adapter weights                                      │
│  - Tokenizer files                                           │
└─────────────────────────────────────────────────────────────┘
```

## 🔄 Fluxo de Requisição

### 1. Cliente → API (HTTP POST)
```javascript
POST /identify
Headers: { "Content-Type": "application/json" }
Body: {
  "query": "música sobre liberdade",
  "context": { "genre": "rock" }
}
```

### 2. Fastify → Validação
```javascript
// server.js
fastify.post('/identify', async (request, reply) => {
  const { query, context = {} } = request.body;
  
  // Validação
  if (!query || typeof query !== 'string' || query.trim().length === 0) {
    return reply.code(400).send({ error: 'Query required' });
  }
  
  // Verificar cache
  const cacheKey = JSON.stringify({ query, context });
  if (responseCache.has(cacheKey)) {
    return responseCache.get(cacheKey);
  }
  
  // Construir prompt
  const fullPrompt = buildPrompt(query, context);
  
  // Executar inferência
  const result = await runModelInference(fullPrompt);
  
  // Cache e retorno
  responseCache.set(cacheKey, result);
  return result;
});
```

### 3. Construção do Prompt
```javascript
function buildPrompt(query, context) {
  let prompt = `<s>[INST] You are a music identification assistant. 
Your task is to identify songs based on descriptions, lyrics snippets, 
or metadata. Always respond in JSON format.

User query: ${query}`;

  if (context.lyric_snippet) {
    prompt += `\nLyric snippet: "${context.lyric_snippet}"`;
  }
  if (context.artist_hint) {
    prompt += `\nArtist hint: ${context.artist_hint}`;
  }
  if (context.genre) {
    prompt += `\nGenre: ${context.genre}`;
  }

  prompt += ` [/INST]`;
  return prompt;
}
```

### 4. Node.js → Python (Subprocess)
```javascript
async function runModelInference(query) {
  return new Promise((resolve, reject) => {
    const args = [
      'src/model_inference.py',
      '--query', query,
      '--model-name', process.env.MODEL_NAME,
      '--max-length', process.env.MAX_LENGTH,
      '--temperature', process.env.TEMPERATURE,
      '--device', process.env.DEVICE,
    ];
    
    const pythonProcess = spawn('python3', args);
    
    let stdoutData = '';
    
    pythonProcess.stdout.on('data', (data) => {
      stdoutData += data.toString();
    });
    
    pythonProcess.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(`Python exited with code ${code}`));
      }
      const result = JSON.parse(stdoutData);
      resolve(result);
    });
  });
}
```

### 5. Python → Carregamento do Modelo
```python
class MusicIdentificationModel:
    def _load_model(self):
        # Configurar quantização
        bnb_config = BitsAndBytesConfig(
            load_in_8bit=self.load_in_8bit,
            load_in_4bit=self.load_in_4bit,
        )
        
        # Carregar modelo base
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_path,
            quantization_config=bnb_config,
            device_map="auto",
            torch_dtype=torch.float16,
        )
        
        # Carregar LoRA (se habilitado)
        if self.use_lora:
            self.model = PeftModel.from_pretrained(
                self.model, 
                self.lora_path
            )
            self.model = self.model.merge_and_unload()
        
        self.model.eval()
```

### 6. Python → Tokenização
```python
def generate(self, prompt: str, max_length: int, temperature: float):
    # Tokenizar entrada
    inputs = self.tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=2048,
    ).to(self.device)
    
    # Configurar geração
    generation_config = GenerationConfig(
        max_new_tokens=max_length,
        temperature=temperature,
        top_p=0.95,
        top_k=50,
        do_sample=True,
    )
    
    # Gerar tokens
    with torch.no_grad():
        outputs = self.model.generate(**inputs, generation_config=generation_config)
    
    # Decodificar
    generated_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
    
    return generated_text
```

### 7. Python → Extração JSON
```python
def identify_song(self, query: str):
    response_text = self.generate(query)
    
    # Procurar JSON na resposta
    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
    if json_match:
        result = json.loads(json_match.group(0))
        return result
    else:
        # Fallback
        return {
            "song": "Unknown",
            "artist": "Unknown",
            "lyrics": response_text,
            "confidence": 0.5,
        }
```

### 8. Python → Node.js (JSON via stdout)
```python
def main():
    result = model.identify_song(args.query)
    print(json.dumps(result, ensure_ascii=False, indent=2))
```

### 9. Node.js → Cliente (HTTP Response)
```javascript
return {
  success: true,
  data: {
    song: result.song,
    artist: result.artist,
    album: result.album,
    lyrics: result.lyrics,
    confidence: result.confidence,
  },
  metadata: {
    inference_time_ms: 2345,
    model: "mistralai/Mistral-7B-Instruct-v0.2",
    timestamp: "2026-04-18T12:00:00Z"
  }
};
```

## 💾 Estrutura de Dados

### Modelo em Memória
```
GPU/CPU Memory:
├── Base Model Weights (7B × 2 bytes = ~14GB com FP16)
│   ├── Attention layers
│   ├── MLP layers
│   └── Embedding layers
├── LoRA Adapters (se aplicável, ~100MB)
│   ├── Query projection adapters
│   ├── Value projection adapters
│   └── Other target module adapters
└── KV Cache (dinâmico, cresce com comprimento de sequência)
    └── ~100MB-1GB dependendo do batch size
```

### Cache de Respostas (Node.js)
```javascript
// Map em memória (limitado a 100 entradas)
responseCache = Map {
  '{"query":"rock 70s","context":{}}' => {
    success: true,
    data: {...},
    metadata: {...}
  },
  // ... até 100 entradas
}
```

## ⚡ Otimizações de Performance

### 1. Quantização (8-bit/4-bit)
```
Memória original (FP16): ~14GB
Memória quantizada (INT8): ~7GB (50% redução)
Memória quantizada (INT4): ~3.5GB (75% redução)

Trade-off: Pequena perda de qualidade (~1-2%) por grande ganho de memória
```

### 2. LoRA vs Full Fine-tuning
```
Full Fine-tuning:
  ✓ Melhor qualidade
  ✗ Requer treinar todos os 7B parâmetros
  ✗ ~28GB VRAM necessário

LoRA (r=16):
  ✓ Treina apenas ~16M parâmetros (~0.2% do modelo)
  ✓ ~8GB VRAM suficiente
  ✓ Treinamento ~3x mais rápido
  ≈ Qualidade similar (95-99% da performance)
```

### 3. Cache Strategy
```javascript
// LRU-like: Remove mais antiga quando >100 entradas
if (responseCache.size > 100) {
  const firstKey = responseCache.keys().next().value;
  responseCache.delete(firstKey);
}
```

## 🔐 Segurança e Produção

### Melhorias Recomendadas para Produção

1. **Autenticação**
```javascript
fastify.addHook('onRequest', async (request, reply) => {
  const apiKey = request.headers['x-api-key'];
  if (!apiKey || apiKey !== process.env.API_KEY) {
    reply.code(401).send({ error: 'Unauthorized' });
  }
});
```

2. **Rate Limiting**
```javascript
import rateLimit from '@fastify/rate-limit';

fastify.register(rateLimit, {
  max: 100,
  timeWindow: '15 minutes'
});
```

3. **Input Sanitization**
```javascript
import validator from 'validator';

if (!validator.isLength(query, { max: 5000 })) {
  return reply.code(400).send({ error: 'Query too long' });
}
```

4. **Logging Estruturado**
```javascript
import pino from 'pino';

const logger = pino({
  level: 'info',
  transport: {
    target: 'pino-pretty'
  }
});
```

---

**Última atualização:** 2026-04-18
