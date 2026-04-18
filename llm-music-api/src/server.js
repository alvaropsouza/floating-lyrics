/**
 * Servidor Fastify para API de identificação de músicas com LLM
 * 
 * Este servidor recebe consultas sobre músicas e retorna identificação e letras
 * completas utilizando um modelo de linguagem local fine-tuned.
 */

require('dotenv').config();

const fastify = require('fastify')({ 
  logger: true,
  requestTimeout: 120000, // 2 minutos para inferência do modelo
  bodyLimit: 50 * 1024 * 1024, // Aceita payloads de audio_base64 maiores (evita HTTP 413)
});
const cors = require('@fastify/cors');
const https = require('https');
const http = require('http');

// Configurações do ambiente
const PORT = process.env.PORT || 3000;
const HOST = process.env.HOST || '0.0.0.0';
const OLLAMA_HOST = process.env.OLLAMA_HOST || 'http://localhost:11434';
const MODEL_SERVER = process.env.MODEL_SERVER || 'http://localhost:8000';
const MODEL_NAME = process.env.OLLAMA_MODEL || process.env.MODEL_NAME || 'mistral';

// Registrar CORS
fastify.register(cors, {
  origin: '*', // Ajustar em produção
  methods: ['GET', 'POST'],
});

// Cache simples em memória (pode ser substituído por Redis)
const responseCache = new Map();

/**
 * Chama o servidor Python de inferência local (model_server.py)
 * ou Ollama como fallback, dependendo do que estiver configurado.
 */
async function callModelServer(prompt) {
  const body = JSON.stringify({ prompt });
  const url = new URL(`${MODEL_SERVER}/generate`);
  const transport = url.protocol === 'https:' ? https : http;

  return new Promise((resolve, reject) => {
    const req = transport.request({
      hostname: url.hostname,
      port: url.port,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      }
    }, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(new Error(`Failed to parse model server response: ${data}`));
        }
      });
    });
    req.on('error', (err) => {
      reject(new Error(`Cannot connect to model server at ${MODEL_SERVER}. Is model_server.py running? Error: ${err.message}`));
    });
    req.write(body);
    req.end();
  });
}

async function callModelServerTrainIndex(datasetPath) {
  const body = JSON.stringify({ dataset_path: datasetPath });
  const url = new URL(`${MODEL_SERVER}/train-index`);
  const transport = url.protocol === 'https:' ? https : http;

  return new Promise((resolve, reject) => {
    const req = transport.request({
      hostname: url.hostname,
      port: url.port,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      }
    }, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(new Error(`Failed to parse model server train-index response: ${data}`));
        }
      });
    });
    req.on('error', (err) => {
      reject(new Error(`Cannot connect to model server at ${MODEL_SERVER}. Error: ${err.message}`));
    });
    req.write(body);
    req.end();
  });
}

async function callModelServerIdentifyAudio(audioBase64, topK = 3) {
  const body = JSON.stringify({ audio_base64: audioBase64, top_k: topK });
  const url = new URL(`${MODEL_SERVER}/identify-audio`);
  const transport = url.protocol === 'https:' ? https : http;

  return new Promise((resolve, reject) => {
    const req = transport.request({
      hostname: url.hostname,
      port: url.port,
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      }
    }, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(new Error(`Failed to parse model server identify-audio response: ${data}`));
        }
      });
    });
    req.on('error', (err) => {
      reject(new Error(`Cannot connect to model server at ${MODEL_SERVER}. Error: ${err.message}`));
    });
    req.write(body);
    req.end();
  });
}

async function callOllama(prompt) {
  const body = JSON.stringify({
    model: MODEL_NAME,
    prompt,
    stream: false,
    options: {
      temperature: parseFloat(process.env.TEMPERATURE || '0.7'),
      top_p: parseFloat(process.env.TOP_P || '0.95'),
      num_predict: parseInt(process.env.MAX_LENGTH || '512'),
    }
  });

  return new Promise((resolve, reject) => {
    const url = new URL(`${OLLAMA_HOST}/api/generate`);
    const transport = url.protocol === 'https:' ? https : http;

    const req = transport.request({
      hostname: url.hostname,
      port: url.port || (url.protocol === 'https:' ? 443 : 80),
      path: url.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      }
    }, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          if (parsed.error) {
            reject(new Error(`Ollama error: ${parsed.error}`));
          } else {
            const text = parsed.response || '';
            const jsonMatch = text.match(/\{[\s\S]*\}/);
            if (jsonMatch) {
              try { resolve(JSON.parse(jsonMatch[0])); return; } catch (_) {}
            }
            resolve({ song: 'Unknown', artist: 'Unknown', album: '', lyrics: text, confidence: 0.5 });
          }
        } catch (e) {
          reject(new Error(`Failed to parse Ollama response: ${data}`));
        }
      });
    });
    req.on('error', (err) => {
      reject(new Error(`Cannot connect to Ollama at ${OLLAMA_HOST}. Error: ${err.message}`));
    });
    req.write(body);
    req.end();
  });
}

/**
 * Executa inferência usando model_server.py (Python local) com fallback para Ollama
 */
async function runModelInference(prompt) {
  // Tentar model_server.py primeiro (Phi-3 local já baixado)
  try {
    return await callModelServer(prompt);
  } catch (primaryErr) {
    fastify.log.warn(`model_server.py unavailable (${primaryErr.message}), trying Ollama...`);
    return await callOllama(prompt);
  }
}

/**
 * Endpoint de saúde
 */
fastify.get('/health', async (request, reply) => {
  // Verificar se Ollama está acessível
  let ollamaStatus = 'unknown';
  try {
    const url = new URL(`${OLLAMA_HOST}/api/tags`);
    const transport = url.protocol === 'https:' ? https : http;
    await new Promise((resolve, reject) => {
      const req = transport.get(url.toString(), (res) => {
        res.resume();
        res.on('end', resolve);
      });
      req.on('error', reject);
      req.setTimeout(3000, () => { req.destroy(); reject(new Error('timeout')); });
    });
    ollamaStatus = 'connected';
  } catch (_) {
    ollamaStatus = 'unavailable';
  }

  return { 
    status: 'healthy', 
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
    ollama: ollamaStatus,
    model: MODEL_NAME,
  };
});

/**
 * Endpoint principal: identificação de música
 * 
 * Body esperado:
 * {
 *   "query": "música que fala sobre liberdade, tem um solo de guitarra longo",
 *   "context": { // opcional
 *     "lyric_snippet": "I'm as free as a bird now",
 *     "artist_hint": "Lynyrd Skynyrd",
 *     "genre": "rock"
 *   }
 * }
 */
fastify.post('/identify', async (request, reply) => {
  const { query, context = {} } = request.body;

  if (!query || typeof query !== 'string' || query.trim().length === 0) {
    return reply.code(400).send({
      error: 'Bad Request',
      message: 'Query parameter is required and must be a non-empty string'
    });
  }

  try {
    fastify.log.info(`Processing query: ${query.substring(0, 100)}...`);

    // Verificar cache
    const cacheKey = JSON.stringify({ query, context });
    if (responseCache.has(cacheKey)) {
      fastify.log.info('Returning cached response');
      return responseCache.get(cacheKey);
    }

    // Construir prompt estruturado para o modelo
    const fullPrompt = buildPrompt(query, context);

    // Executar inferência
    const startTime = Date.now();
    const result = await runModelInference(fullPrompt);
    const inferenceTime = Date.now() - startTime;

    fastify.log.info(`Inference completed in ${inferenceTime}ms`);

    const response = {
      success: true,
      data: {
        song: result.song || 'Unknown',
        artist: result.artist || 'Unknown',
        album: result.album || '',
        lyrics: result.lyrics || '',
        confidence: result.confidence || 0.0,
      },
      metadata: {
        inference_time_ms: inferenceTime,
        model: MODEL_NAME,
        backend: MODEL_SERVER,
        timestamp: new Date().toISOString()
      }
    };

    // Armazenar em cache (limitar tamanho)
    if (responseCache.size > 100) {
      const firstKey = responseCache.keys().next().value;
      responseCache.delete(firstKey);
    }
    responseCache.set(cacheKey, response);

    return response;

  } catch (error) {
    fastify.log.error(error);
    return reply.code(500).send({
      success: false,
      error: 'Internal Server Error',
      message: error.message,
      timestamp: new Date().toISOString()
    });
  }
});

/**
 * Treina/atualiza o indice local de reconhecimento por audio.
 *
 * Body opcional:
 * {
 *   "dataset_path": "/app/training_audio"
 * }
 */
fastify.post('/recognition/train', async (request, reply) => {
  const { dataset_path } = request.body || {};

  try {
    const result = await callModelServerTrainIndex(dataset_path || process.env.AUDIO_DATASET_PATH || '/app/training_audio');
    if (result.error) {
      throw new Error(result.error);
    }
    return { success: true, data: result };
  } catch (error) {
    fastify.log.error(error);
    const statusCode = /invalido|não existe|nao existe|nenhum audio valido/i.test(error.message) ? 400 : 500;
    return reply.code(statusCode).send({
      success: false,
      error: 'Internal Server Error',
      message: error.message,
    });
  }
});

/**
 * Identifica musica por trecho de audio (base64) usando algoritmo local treinado.
 *
 * Body esperado:
 * {
 *   "audio_base64": "...",
 *   "top_k": 3
 * }
 */
fastify.post('/identify/audio', async (request, reply) => {
  const { audio_base64, top_k = 3 } = request.body || {};

  if (!audio_base64 || typeof audio_base64 !== 'string') {
    return reply.code(400).send({
      error: 'Bad Request',
      message: 'audio_base64 is required and must be a string',
    });
  }

  try {
    const startTime = Date.now();
    const result = await callModelServerIdentifyAudio(audio_base64, top_k);
    if (result.error) {
      throw new Error(result.error);
    }
    const inferenceTime = Date.now() - startTime;

    return {
      success: true,
      data: {
        song: result.song || 'Unknown',
        artist: result.artist || 'Unknown',
        album: result.album || '',
        confidence: result.confidence || 0.0,
        method: result.method || 'audio_similarity',
        top_matches: result.top_matches || [],
      },
      metadata: {
        inference_time_ms: inferenceTime,
        backend: MODEL_SERVER,
        timestamp: new Date().toISOString(),
      }
    };
  } catch (error) {
    fastify.log.error(error);
    const statusCode = /audio_base64 invalido|Index de audio nao treinado|prompt is required/i.test(error.message) ? 400 : 500;
    return reply.code(statusCode).send({
      success: false,
      error: 'Internal Server Error',
      message: error.message,
    });
  }
});

/**
 * Endpoint para consultas batch
 */
fastify.post('/identify/batch', async (request, reply) => {
  const { queries } = request.body;

  if (!Array.isArray(queries) || queries.length === 0) {
    return reply.code(400).send({
      error: 'Bad Request',
      message: 'queries must be a non-empty array'
    });
  }

  if (queries.length > 10) {
    return reply.code(400).send({
      error: 'Bad Request',
      message: 'Maximum 10 queries per batch request'
    });
  }

  try {
    const results = await Promise.all(
      queries.map(async ({ query, context }) => {
        try {
          const fullPrompt = buildPrompt(query, context || {});
          const result = await runModelInference(fullPrompt);
          return { success: true, data: result };
        } catch (error) {
          return { success: false, error: error.message };
        }
      })
    );

    return {
      success: true,
      results,
      timestamp: new Date().toISOString()
    };

  } catch (error) {
    fastify.log.error(error);
    return reply.code(500).send({
      success: false,
      error: 'Internal Server Error',
      message: error.message
    });
  }
});

/**
 * Limpar cache manualmente
 */
fastify.post('/cache/clear', async (request, reply) => {
  const previousSize = responseCache.size;
  responseCache.clear();
  return { 
    success: true, 
    message: `Cache cleared. ${previousSize} entries removed.` 
  };
});

/**
 * Construir prompt estruturado para o modelo
 */
function buildPrompt(query, context) {
  let prompt = `<s>[INST] You are a music identification assistant. Your task is to identify songs based on descriptions, lyrics snippets, or metadata provided by the user. Always respond in JSON format with the following structure:
{
  "song": "Song Title",
  "artist": "Artist Name",
  "album": "Album Name",
  "lyrics": "Complete lyrics of the song",
  "confidence": 0.95
}

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

/**
 * Iniciar servidor
 */
const start = async () => {
  try {
    await fastify.listen({ port: PORT, host: HOST });
    fastify.log.info(`Server listening on ${HOST}:${PORT}`);
    fastify.log.info(`Model: ${MODEL_NAME}`);
    fastify.log.info(`Model server: ${MODEL_SERVER}`);
    fastify.log.info(`Ollama host (fallback): ${OLLAMA_HOST}`);
  } catch (err) {
    fastify.log.error(err);
    process.exit(1);
  }
};

// Graceful shutdown
process.on('SIGTERM', async () => {
  fastify.log.info('SIGTERM received, shutting down gracefully...');
  await fastify.close();
  process.exit(0);
});

process.on('SIGINT', async () => {
  fastify.log.info('SIGINT received, shutting down gracefully...');
  await fastify.close();
  process.exit(0);
});

start();
