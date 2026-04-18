/**
 * Servidor Fastify para API de identificação de músicas com LLM
 * 
 * Este servidor recebe consultas sobre músicas e retorna identificação e letras
 * completas utilizando um modelo de linguagem local fine-tuned.
 */

const fastify = require('fastify')({ 
  logger: true,
  requestTimeout: 120000 // 2 minutos para inferência do modelo
});
const cors = require('@fastify/cors');
const { spawn } = require('child_process');
const path = require('path');

// Configurações do ambiente
const PORT = process.env.PORT || 3000;
const HOST = process.env.HOST || '0.0.0.0';

// Registrar CORS
fastify.register(cors, {
  origin: '*', // Ajustar em produção
  methods: ['GET', 'POST'],
});

// Cache simples em memória (pode ser substituído por Redis)
const responseCache = new Map();

/**
 * Executa inferência do modelo Python via subprocess
 * @param {string} query - Consulta do usuário
 * @returns {Promise<Object>} - Resposta do modelo
 */
async function runModelInference(query) {
  return new Promise((resolve, reject) => {
    const scriptPath = path.join(__dirname, 'model_inference.py');
    
    // Passar configurações via argumentos
    const args = [
      scriptPath,
      '--query', query,
      '--model-path', process.env.MODEL_PATH || '',
      '--model-name', process.env.MODEL_NAME || 'mistralai/Mistral-7B-Instruct-v0.2',
      '--max-length', process.env.MAX_LENGTH || '512',
      '--temperature', process.env.TEMPERATURE || '0.7',
      '--top-p', process.env.TOP_P || '0.95',
      '--device', process.env.DEVICE || 'cpu',
    ];

    if (process.env.USE_LORA === 'true') {
      args.push('--use-lora');
      args.push('--lora-path', process.env.LORA_WEIGHTS_PATH || '');
    }

    if (process.env.LOAD_IN_8BIT === 'true') {
      args.push('--load-in-8bit');
    }

    const pythonProcess = spawn('python3', args);
    
    let stdoutData = '';
    let stderrData = '';

    pythonProcess.stdout.on('data', (data) => {
      stdoutData += data.toString();
    });

    pythonProcess.stderr.on('data', (data) => {
      stderrData += data.toString();
      fastify.log.warn(`Python stderr: ${data}`);
    });

    pythonProcess.on('close', (code) => {
      if (code !== 0) {
        reject(new Error(`Python process exited with code ${code}: ${stderrData}`));
        return;
      }

      try {
        const result = JSON.parse(stdoutData);
        resolve(result);
      } catch (error) {
        reject(new Error(`Failed to parse Python output: ${error.message}\nOutput: ${stdoutData}`));
      }
    });

    pythonProcess.on('error', (error) => {
      reject(new Error(`Failed to start Python process: ${error.message}`));
    });
  });
}

/**
 * Endpoint de saúde
 */
fastify.get('/health', async (request, reply) => {
  return { 
    status: 'healthy', 
    timestamp: new Date().toISOString(),
    uptime: process.uptime()
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
        model: process.env.MODEL_NAME,
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
    fastify.log.info(`Model: ${process.env.MODEL_NAME || 'Not configured'}`);
    fastify.log.info(`Device: ${process.env.DEVICE || 'cpu'}`);
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
