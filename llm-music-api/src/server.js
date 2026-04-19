/**
 * Servidor Fastify para API de identificação de músicas com LLM
 * 
 * Este servidor recebe consultas sobre músicas e retorna identificação e letras
 * completas utilizando um modelo de linguagem local fine-tuned.
 */

require('dotenv').config();

const fastify = require('fastify')({ 
  logger: true,
  disableRequestLogging: true,
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
const OLLAMA_KEEP_ALIVE = process.env.OLLAMA_KEEP_ALIVE_REQUEST || process.env.OLLAMA_KEEP_ALIVE || '24h';
const CATALOG_MIN_SCORE = Number.parseFloat(process.env.CATALOG_MIN_SCORE || '0.58');
const CATALOG_STRONG_SCORE = Number.parseFloat(process.env.CATALOG_STRONG_SCORE || '0.72');
const LLM_PREFER_CONFIDENCE = Number.parseFloat(process.env.LLM_PREFER_CONFIDENCE || '0.85');
const LLM_CATALOG_TITLE_AGREEMENT_MIN = Number.parseFloat(process.env.LLM_CATALOG_TITLE_AGREEMENT_MIN || '0.45');
const PREFER_LLM_ON_CATALOG_CONFLICT = String(process.env.PREFER_LLM_ON_CATALOG_CONFLICT || 'false').toLowerCase() === 'true';
const TITLE_WEB_EVIDENCE_TERMS_LIMIT = Number.parseInt(process.env.TITLE_WEB_EVIDENCE_TERMS_LIMIT || '4', 10);
const TITLE_WEB_EVIDENCE_RESULTS_LIMIT = Number.parseInt(process.env.TITLE_WEB_EVIDENCE_RESULTS_LIMIT || '5', 10);

// Registrar CORS
fastify.register(cors, {
  origin: '*', // Ajustar em produção
  methods: ['GET', 'POST'],
});

// Cache simples em memória (pode ser substituído por Redis)
const responseCache = new Map();
const itunesSearchCache = new Map();

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
    keep_alive: OLLAMA_KEEP_ALIVE,
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
 * Variante rápida do callOllama para /search: temperatura 0, menos tokens.
 * Resposta esperada é um JSON curto (~100 tokens).
 */
async function callOllamaFast(prompt) {
  const body = JSON.stringify({
    model: MODEL_NAME,
    prompt,
    stream: false,
    keep_alive: OLLAMA_KEEP_ALIVE,
    options: {
      temperature: 0,
      top_p: 0.9,
      num_predict: 128,
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
            resolve({ song: 'Unknown', artist: 'Unknown', album: '', confidence: 0.5 });
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

function slug(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s-_]/g, ' ')
    .replace(/[\s_]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
    || 'unknown';
}

function basicCleanTitle(rawTitle) {
  let t = String(rawTitle || '');
  t = t.replace(/^\s*\d{1,2}\s*[-._)]\s*/, '');
  t = t.replace(/(?:__|_)\d{8}_[a-zA-Z0-9_-]{6,}$/ig, '');
  t = t.replace(/[_-][A-Za-z0-9_-]{11}$/ig, '');
  // Remove sufixos artificiais de colisao (_2, -3...), preservando "pt 1".
  t = t.replace(/(?:[-_ ](?:[2-9]|1\d)){1,3}$/ig, '');
  t = t.replace(/\[(official|lyrics?|audio|video|hd|4k)[^\]]*\]/ig, '');
  t = t.replace(/\((official|lyrics?|audio|video|hd|4k)[^\)]*\)/ig, '');
  t = t.replace(/\b(official\s+video|official\s+audio|lyrics?\s+video|audio\s+only)\b/ig, '');
  // Ruido de qualidade / remaster / formato
  t = t.replace(/\b(remasterizado|remastered|remaster|remixed|deluxe|edition|bonus\s+track)\b/ig, '');
  t = t.replace(/\b(full\s+album|complete|completo)\b/ig, '');
  t = t.replace(/\b(hq|hd|4k|1080p|720p|320\s*kbps|flac)\b/ig, '');
  t = t.replace(/[_-]+/g, ' ');
  t = t.replace(/\s{2,}/g, ' ').trim();
  return t || 'unknown-title';
}

function metadataLogPrefix(trace = null) {
  if (!trace || typeof trace !== 'object') {
    return '[clean-metadata]';
  }
  const batchId = trace.batchId || 'single';
  const itemIndex = Number.isInteger(trace.itemIndex) ? trace.itemIndex : '-';
  return `[clean-metadata][batch:${batchId}][item:${itemIndex}]`;
}

function buildCleanupPrompt(combinedInput, webEvidence = []) {
  const hasWebEvidence = Array.isArray(webEvidence) && webEvidence.length > 0;
  const evidenceBlock = hasWebEvidence
    ? `\nWeb search evidence (queried using only the title):\n${webEvidence
        .map((item, idx) => `${idx + 1}. ${item}`)
        .join('\n')}\n`
    : '';

  const evidenceRules = hasWebEvidence
    ? `
- Prioritize web evidence above noisy title text when fields conflict.
- Use title text only to break ties when evidence is ambiguous.`
    : `
- Use only the input string below as evidence. Do not assume extra facts.`;

  return `<s>[INST] You clean noisy music metadata and output strict JSON only.

Return format:
{
  "title": "clean song title",
  "artist": "clean artist name",
  "album": "clean album name",
  "confidence": 0.0
}

Rules:
- Remove noise like "official video", "lyrics", "HD", track numbers and uploader junk.
- Keep only core title/artist/album metadata.
- If album is unknown, return empty string for album.
- If uncertain, keep best guess but lower confidence.
- Never invent artist/album names that are not supported by evidence.
- If evidence is insufficient, keep best guess with lower confidence.${evidenceRules}
- Return JSON only.

Input string:
${combinedInput}${evidenceBlock}
[/INST]`;
}

function buildCleanupInputString(rawTitle) {
  return String(rawTitle || '').trim();
}

function normalizeText(text) {
  return String(text || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function tokenSet(text) {
  const n = normalizeText(text);
  if (!n) return new Set();
  return new Set(n.split(' ').filter(Boolean));
}

function jaccardScore(a, b) {
  const sa = tokenSet(a);
  const sb = tokenSet(b);
  if (sa.size === 0 || sb.size === 0) return 0;
  let inter = 0;
  for (const t of sa) {
    if (sb.has(t)) inter += 1;
  }
  const union = sa.size + sb.size - inter;
  return union > 0 ? inter / union : 0;
}

async function searchItunesSongs(term, limit = 20) {
  const q = encodeURIComponent(String(term || '').trim());
  if (!q) return [];

  const url = new URL(`https://itunes.apple.com/search?term=${q}&entity=song&limit=${Math.max(1, Math.min(50, limit))}`);
  const transport = url.protocol === 'https:' ? https : http;

  return new Promise((resolve, reject) => {
    const req = transport.get(url.toString(), (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          resolve(Array.isArray(parsed.results) ? parsed.results : []);
        } catch (e) {
          reject(new Error(`Failed to parse iTunes response: ${data.slice(0, 300)}`));
        }
      });
    });
    req.on('error', reject);
    req.setTimeout(6000, () => {
      req.destroy(new Error('timeout'));
    });
  });
}

async function searchItunesSongsCached(term, limit = 20) {
  const normalizedTerm = String(term || '').trim().toLowerCase();
  const safeLimit = Math.max(1, Math.min(50, limit));
  const cacheKey = `${normalizedTerm}::${safeLimit}`;
  if (!normalizedTerm) return [];

  if (itunesSearchCache.has(cacheKey)) {
    return itunesSearchCache.get(cacheKey);
  }

  const results = await searchItunesSongs(term, safeLimit);

  if (itunesSearchCache.size > 500) {
    const oldestKey = itunesSearchCache.keys().next().value;
    itunesSearchCache.delete(oldestKey);
  }
  itunesSearchCache.set(cacheKey, results);

  return results;
}

function buildTitleOnlySearchTerms(rawTitle) {
  const cleaned = basicCleanTitle(rawTitle);
  const terms = [cleaned];
  const splitPatterns = [' - ', ' – ', ' — ', ' | ', ': '];

  for (const sep of splitPatterns) {
    if (cleaned.includes(sep)) {
      const parts = cleaned.split(sep).map((part) => part.trim()).filter(Boolean);
      if (parts.length > 1) {
        terms.push(parts[parts.length - 1]);
        terms.push(parts[0]);
      }
    }
  }

  const noFeat = cleaned.replace(/\b(feat\.?|ft\.?)\b.*$/i, '').trim();
  if (noFeat) {
    terms.push(noFeat);
  }

  return [...new Set(terms.filter(Boolean))];
}

async function buildWebEvidenceFromTitle(rawTitle) {
  const titleOnlyTerms = buildTitleOnlySearchTerms(rawTitle).slice(0, Math.max(1, TITLE_WEB_EVIDENCE_TERMS_LIMIT));
  const cleanedTitle = basicCleanTitle(rawTitle);
  const seenTriples = new Set();
  const candidates = [];

  // Padrões que indicam cover/karaoke/remix — penalizados no score
  const coverPatterns = /\b(karaoke|tribute|originally performed|cover|instrumental|backing track|midi|8[- ]?bit|lullaby|music box)\b/i;

  // Buscar todos os termos em paralelo
  const allResults = await Promise.allSettled(
    titleOnlyTerms.map(term => searchItunesSongsCached(term, 12).then(r => ({ term, results: r })))
  );

  for (const outcome of allResults) {
    if (outcome.status !== 'fulfilled') continue;
    for (const c of outcome.value.results) {
      const title = String(c.trackName || '').trim();
      const artist = String(c.artistName || '').trim();
      const album = String(c.collectionName || '').trim();
      if (!title) continue;

      const dedupKey = `${normalizeText(title)}::${normalizeText(artist)}::${normalizeText(album)}`;
      if (seenTriples.has(dedupKey)) continue;
      seenTriples.add(dedupKey);

      const titleScore = jaccardScore(cleanedTitle, title);
      const queryScore = jaccardScore(outcome.value.term, title);
      // Bonus: se o artista aparece no título bruto, boost significativo
      const artistInQuery = artist ? jaccardScore(cleanedTitle, artist) : 0;
      let weightedScore = (titleScore * 0.55) + (queryScore * 0.20) + (artistInQuery * 0.25);

      // Penalizar covers/karaoke (rebaixar muito no ranking)
      if (coverPatterns.test(title) || coverPatterns.test(artist) || coverPatterns.test(album)) {
        weightedScore *= 0.3;
      }

      candidates.push({ score: weightedScore, title, artist, album });
    }
  }

  candidates.sort((a, b) => b.score - a.score);
  return candidates.slice(0, Math.max(1, TITLE_WEB_EVIDENCE_RESULTS_LIMIT));
}

function scoreItunesCandidate(candidate, ctx) {
  const titleScore = jaccardScore(ctx.cleanedTitle, candidate.trackName || '');
  const artistScore = jaccardScore(ctx.uploader || ctx.llmArtist || '', candidate.artistName || '');
  const albumScore = jaccardScore(ctx.albumHint || ctx.llmAlbum || '', candidate.collectionName || '');
  const llmTitleScore = jaccardScore(ctx.llmTitle || '', candidate.trackName || '');

  const weighted =
    (titleScore * 0.55) +
    (artistScore * 0.20) +
    (albumScore * 0.15) +
    (llmTitleScore * 0.10);

  return Math.max(0, Math.min(1, weighted));
}

async function resolveMetadataWithCatalog({ rawTitle, uploader = '', albumHint = '', llmGuess = null }) {
  const cleanedTitle = basicCleanTitle(rawTitle);
  const llmTitle = llmGuess ? String(llmGuess.title || llmGuess.song || '').trim() : '';
  const llmArtist = llmGuess ? String(llmGuess.artist || '').trim() : '';
  const llmAlbum = llmGuess ? String(llmGuess.album || '').trim() : '';
  const titleOnlyTerms = buildTitleOnlySearchTerms(rawTitle);

  const terms = [
    ...titleOnlyTerms,
    `${uploader} ${albumHint} ${cleanedTitle}`,
    `${uploader} ${cleanedTitle}`,
    `${cleanedTitle} ${albumHint}`,
    `${llmArtist} ${llmTitle} ${llmAlbum}`,
  ].map(t => t.trim()).filter(Boolean);

  const uniqueTerms = [...new Set(terms)];
  let best = null;
  let bestScore = 0;

  for (const term of uniqueTerms.slice(0, 5)) {
    let results = [];
    try {
      results = await searchItunesSongsCached(term, 25);
    } catch (error) {
      fastify.log.warn(`iTunes lookup failed for term="${term}": ${error.message}`);
      continue;
    }

    for (const c of results) {
      const score = scoreItunesCandidate(c, {
        cleanedTitle,
        uploader,
        albumHint,
        llmTitle,
        llmArtist,
        llmAlbum,
      });
      if (score > bestScore) {
        bestScore = score;
        best = c;
      }
    }

    if (bestScore >= CATALOG_STRONG_SCORE) {
      break;
    }
  }

  if ((!best || bestScore < CATALOG_MIN_SCORE) && uploader && albumHint) {
    // Fallback: pesquisa focada no album e tenta casar por prefixo/contencao do titulo limpo.
    try {
      const albumResults = await searchItunesSongsCached(`${uploader} ${albumHint}`, 50);
      const albumNorm = normalizeText(albumHint);
      const titleNorm = normalizeText(cleanedTitle);

      const narrowed = albumResults.filter((c) => {
        const candAlbum = normalizeText(c.collectionName || '');
        const candArtist = normalizeText(c.artistName || '');
        return candAlbum.includes(albumNorm) && candArtist.includes(normalizeText(uploader));
      });

      let fallbackBest = null;
      let fallbackScore = 0;
      for (const c of narrowed) {
        const candTitle = normalizeText(c.trackName || '');
        let s = 0;
        if (candTitle.startsWith(titleNorm) || titleNorm.startsWith(candTitle)) {
          s += 0.65;
        }
        if (candTitle.includes(titleNorm) || titleNorm.includes(candTitle)) {
          s += 0.25;
        }
        s += jaccardScore(cleanedTitle, c.trackName || '') * 0.20;
        if (s > fallbackScore) {
          fallbackScore = s;
          fallbackBest = c;
        }
      }

      if (fallbackBest && fallbackScore >= Math.max(CATALOG_MIN_SCORE, 0.6)) {
        best = fallbackBest;
        bestScore = Math.max(bestScore, Math.min(0.9, fallbackScore));
      }
    } catch (error) {
      fastify.log.warn(`Album fallback lookup failed: ${error.message}`);
    }
  }

  if (!best || bestScore < CATALOG_MIN_SCORE) {
    return null;
  }

  return {
    title: String(best.trackName || cleanedTitle).trim(),
    artist: String(best.artistName || uploader || llmArtist).trim(),
    album: String(best.collectionName || albumHint || llmAlbum).trim(),
    confidence: Number(bestScore.toFixed(3)),
    source: 'catalog',
  };
}

async function cleanMetadataRecord({ rawTitle, uploader = '', albumHint = '', trace = null }) {
  const prefix = metadataLogPrefix(trace);
  const startedAt = Date.now();
  fastify.log.warn({
    raw_title_preview: String(rawTitle || '').slice(0, 120),
    uploader_preview: String(uploader || '').slice(0, 80),
    album_hint_preview: String(albumHint || '').slice(0, 80),
  }, `${prefix} inicio`);

  const combinedInput = buildCleanupInputString(rawTitle);
  const webEvidence = await buildWebEvidenceFromTitle(rawTitle);
  const prompt = buildCleanupPrompt(
    combinedInput,
    webEvidence.map((item) => `${item.title} | ${item.artist || 'Unknown artist'} | ${item.album || 'Unknown album'} | score=${item.score.toFixed(3)}`)
  );
  let llm = null;

  try {
    // Preferir Ollama aqui para nao herdar validacoes de "song match" do model_server.
    llm = await callOllama(prompt);
    fastify.log.warn({
      llm_input_preview: combinedInput.slice(0, 180),
      llm_title_preview: String(llm?.title || llm?.song || '').slice(0, 100),
      llm_artist_preview: String(llm?.artist || '').slice(0, 80),
      llm_album_preview: String(llm?.album || '').slice(0, 80),
      llm_confidence: llm?.confidence,
      web_evidence_count: webEvidence.length,
    }, `${prefix} resposta LLM recebida`);
  } catch (error) {
    fastify.log.warn(`${prefix} LLM indisponivel, seguindo com catalog/fallback: ${error.message}`);
  }

  const llmResponse = llm
    ? {
        title: String(llm.title || llm.song || basicCleanTitle(rawTitle)).trim(),
        artist: String(llm.artist || uploader || '').trim(),
        album: String(llm.album || albumHint || '').trim(),
        confidence: Number.isFinite(Number(llm.confidence || 0.6))
          ? Math.max(0, Math.min(1, Number(llm.confidence || 0.6)))
          : 0.6,
        source: 'llm',
      }
    : null;

  const catalog = await resolveMetadataWithCatalog({
    rawTitle,
    uploader,
    albumHint,
    llmGuess: llm,
  });

  if (catalog && llmResponse && PREFER_LLM_ON_CATALOG_CONFLICT) {
    const titleAgreement = jaccardScore(llmResponse.title, catalog.title);
    const artistAgreement = jaccardScore(llmResponse.artist, catalog.artist);
    const preferLlm =
      llmResponse.confidence >= LLM_PREFER_CONFIDENCE
      && titleAgreement < LLM_CATALOG_TITLE_AGREEMENT_MIN
      && artistAgreement < 0.5;

    if (preferLlm) {
      fastify.log.warn({
        llm_confidence: llmResponse.confidence,
        title_agreement: Number(titleAgreement.toFixed(3)),
        artist_agreement: Number(artistAgreement.toFixed(3)),
        llm_title_preview: String(llmResponse.title || '').slice(0, 100),
        catalog_title_preview: String(catalog.title || '').slice(0, 100),
      }, `${prefix} conflito catalogo vs llm, priorizando llm confiante`);

      fastify.log.warn({
        source: llmResponse.source,
        confidence: llmResponse.confidence,
        elapsed_ms: Date.now() - startedAt,
        title_preview: String(llmResponse.title || '').slice(0, 100),
        artist_preview: String(llmResponse.artist || '').slice(0, 80),
        album_preview: String(llmResponse.album || '').slice(0, 80),
      }, `${prefix} resolvido via llm (prioridade por conflito)`);
      return llmResponse;
    }
  }

  if (catalog) {
    fastify.log.warn({
      source: catalog.source,
      confidence: catalog.confidence,
      elapsed_ms: Date.now() - startedAt,
      title_preview: String(catalog.title || '').slice(0, 100),
      artist_preview: String(catalog.artist || '').slice(0, 80),
      album_preview: String(catalog.album || '').slice(0, 80),
    }, `${prefix} resolvido via catalogo`);
    return catalog;
  }

  if (llmResponse) {
    fastify.log.warn({
      source: llmResponse.source,
      confidence: llmResponse.confidence,
      elapsed_ms: Date.now() - startedAt,
      title_preview: String(llmResponse.title || '').slice(0, 100),
      artist_preview: String(llmResponse.artist || '').slice(0, 80),
      album_preview: String(llmResponse.album || '').slice(0, 80),
    }, `${prefix} resolvido via llm`);
    return llmResponse;
  }

  const fallbackResponse = {
    title: basicCleanTitle(rawTitle),
    artist: String(uploader || '').trim(),
    album: String(albumHint || '').trim(),
    confidence: 0.35,
    source: 'fallback',
  };
  fastify.log.warn({
    source: fallbackResponse.source,
    confidence: fallbackResponse.confidence,
    elapsed_ms: Date.now() - startedAt,
    title_preview: String(fallbackResponse.title || '').slice(0, 100),
    artist_preview: String(fallbackResponse.artist || '').slice(0, 80),
    album_preview: String(fallbackResponse.album || '').slice(0, 80),
  }, `${prefix} resolvido via fallback heuristico`);
  return fallbackResponse;
}

/**
 * Endpoint de saúde
 */
fastify.get('/health', { logLevel: 'warn' }, async (request, reply) => {
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

    // Verificar confiança mínima (padrão 0.7 = 70%)
    const minConfidence = parseFloat(process.env.MIN_CONFIDENCE || '0.7');
    const confidence = result.confidence || 0.0;
    
    // Se confiança está abaixo do threshold ou model retornou 'Unknown', não confirmar a resposta
    if ((result.song || '').toLowerCase() === 'unknown' || (result.artist || '').toLowerCase() === 'unknown' || confidence < minConfidence) {
      fastify.log.info(`Low confidence or unknown song (${confidence}). Not confirming match.`);
      return reply.code(404).send({
        success: false,
        message: 'Música não reconhecida no banco de dados do modelo',
        metadata: {
          confidence,
          inference_time_ms: inferenceTime,
          model: MODEL_NAME,
          timestamp: new Date().toISOString()
        }
      });
    }

    const response = {
      success: true,
      data: {
        song: result.song || 'Unknown',
        artist: result.artist || 'Unknown',
        album: result.album || '',
        lyrics: result.lyrics || '',
        confidence: confidence,
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
 * Limpa metadados de titulo de musica usando LLM (com fallback heuristico).
 *
 * Body esperado:
 * {
 *   "raw_title": "01 - Artist - Song (Official Video)",
 *   "uploader": "Channel Name",
 *   "album_hint": "Album Name"
 * }
 */
fastify.post('/clean-metadata', async (request, reply) => {
  const { raw_title, uploader = '', album_hint = '' } = request.body || {};
  const prefix = `[clean-metadata][single][req:${request.id}]`;

  fastify.log.warn({
    raw_title_preview: String(raw_title || '').slice(0, 120),
    uploader_preview: String(uploader || '').slice(0, 80),
    album_hint_preview: String(album_hint || '').slice(0, 80),
  }, `${prefix} requisicao recebida`);

  if (!raw_title || typeof raw_title !== 'string') {
    fastify.log.warn(`${prefix} payload invalido: raw_title ausente/invalido`);
    return reply.code(400).send({
      success: false,
      error: 'Bad Request',
      message: 'raw_title is required and must be a string',
    });
  }

  const data = await cleanMetadataRecord({
    rawTitle: raw_title,
    uploader,
    albumHint: album_hint,
    trace: { batchId: 'single', itemIndex: 0 },
  });

  fastify.log.warn({
    source: data.source,
    confidence: data.confidence,
  }, `${prefix} concluido`);

  return { success: true, data };
});

/**
 * Limpa metadados em lote para reduzir overhead de HTTP entre downloader e API.
 *
 * Body esperado:
 * {
 *   "items": [
 *     { "raw_title": "...", "uploader": "...", "album_hint": "..." }
 *   ]
 * }
 */
fastify.post('/clean-metadata/batch', async (request, reply) => {
  const { items } = request.body || {};
  const batchId = request.id;
  const batchStart = Date.now();
  const prefix = `[clean-metadata][batch:${batchId}]`;

  fastify.log.warn({
    items_count: Array.isArray(items) ? items.length : 0,
  }, `${prefix} requisicao recebida`);

  if (!Array.isArray(items) || items.length === 0) {
    fastify.log.warn(`${prefix} payload invalido: items vazio/invalido`);
    return reply.code(400).send({
      success: false,
      error: 'Bad Request',
      message: 'items must be a non-empty array',
    });
  }

  if (items.length > 200) {
    fastify.log.warn(`${prefix} payload invalido: items acima do limite (${items.length})`);
    return reply.code(400).send({
      success: false,
      error: 'Bad Request',
      message: 'Maximum 200 items per batch request',
    });
  }

  const results = await Promise.all(
    items.map(async (item, itemIndex) => {
      const itemPrefix = `[clean-metadata][batch:${batchId}][item:${itemIndex}]`;
      const itemStart = Date.now();

      const rawTitle = item && typeof item.raw_title === 'string' ? item.raw_title : '';
      const uploader = item && typeof item.uploader === 'string' ? item.uploader : '';
      const albumHint = item && typeof item.album_hint === 'string' ? item.album_hint : '';

      fastify.log.warn({
        raw_title_preview: String(rawTitle || '').slice(0, 120),
        uploader_preview: String(uploader || '').slice(0, 80),
        album_hint_preview: String(albumHint || '').slice(0, 80),
      }, `${itemPrefix} processando item`);

      if (!rawTitle) {
        fastify.log.warn(`${itemPrefix} item invalido: raw_title ausente/invalido`);
        return {
          success: false,
          error: 'raw_title is required and must be a string',
          data: {
            title: basicCleanTitle(''),
            artist: uploader,
            album: albumHint,
            confidence: 0.0,
            source: 'invalid-input',
          },
        };
      }

      try {
        const data = await cleanMetadataRecord({
          rawTitle,
          uploader,
          albumHint,
          trace: { batchId, itemIndex },
        });
        fastify.log.warn({
          source: data.source,
          confidence: data.confidence,
          elapsed_ms: Date.now() - itemStart,
        }, `${itemPrefix} item concluido`);
        return { success: true, data };
      } catch (error) {
        fastify.log.warn(`${itemPrefix} erro no item, aplicando fallback: ${error.message}`);
        return {
          success: false,
          error: error.message,
          data: {
            title: basicCleanTitle(rawTitle),
            artist: String(uploader || '').trim(),
            album: String(albumHint || '').trim(),
            confidence: 0.35,
            source: 'fallback',
          },
        };
      }
    })
  );

  const successCount = results.filter((r) => r?.success).length;
  const failureCount = results.length - successCount;
  const sourceSummary = results.reduce((acc, result) => {
    const src = result?.data?.source || 'unknown';
    acc[src] = (acc[src] || 0) + 1;
    return acc;
  }, {});

  fastify.log.warn({
    total_items: results.length,
    success_count: successCount,
    failure_count: failureCount,
    source_summary: sourceSummary,
    elapsed_ms: Date.now() - batchStart,
  }, `${prefix} lote concluido`);

  return {
    success: true,
    results,
    timestamp: new Date().toISOString(),
  };
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
  let prompt = `<s>[INST] You are a music identification assistant trained on a specific dataset of songs. Your task is to identify songs based on descriptions, lyrics snippets, or metadata provided by the user.

IMPORTANT RULES:
1. Only respond with songs that are in your training dataset.
2. If you are NOT confident (less than 70% sure) that a song matches the description, respond with {"song": "Unknown", "artist": "Unknown", "confidence": 0.0}.
3. NEVER guess or suggest songs not in your training data.
4. NEVER try to fit in songs you know when the user describes an unknown song.
5. Always respond in JSON format:
{
  "song": "Song Title",
  "artist": "Artist Name",
  "album": "Album Name",
  "lyrics": "Complete lyrics or empty string if not found",
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

// ── Busca de letras via lrclib.net (gratuito, sem chave) ───────────────

async function fetchLyricsFromLrclib(title, artist) {
  const queries = [
    { track_name: title, artist_name: artist },
  ];

  for (const params of queries) {
    const qs = new URLSearchParams(params).toString();
    const getUrl = `https://lrclib.net/api/get?${qs}`;
    try {
      const data = await httpGetJson(getUrl, 5000);
      if (data && (data.syncedLyrics || data.plainLyrics)) {
        return {
          lyrics: data.syncedLyrics || data.plainLyrics || '',
          synced: Boolean(data.syncedLyrics),
          source: 'lrclib',
        };
      }
    } catch (_) { /* ignore, try next */ }
  }

  // Fallback: pesquisa livre
  const searchUrl = `https://lrclib.net/api/search?q=${encodeURIComponent(`${artist} ${title}`)}`;
  try {
    const results = await httpGetJson(searchUrl, 5000);
    if (Array.isArray(results) && results.length > 0) {
      // Ranquear por similaridade com o título
      const titleNorm = normalizeText(title);
      let bestItem = null;
      let bestScore = -1;
      for (const item of results.slice(0, 10)) {
        const score = jaccardScore(titleNorm, normalizeText(item.trackName || ''))
                    + jaccardScore(normalizeText(artist), normalizeText(item.artistName || '')) * 0.5;
        if (score > bestScore) {
          bestScore = score;
          bestItem = item;
        }
      }
      if (bestItem && (bestItem.syncedLyrics || bestItem.plainLyrics)) {
        return {
          lyrics: bestItem.syncedLyrics || bestItem.plainLyrics || '',
          synced: Boolean(bestItem.syncedLyrics),
          source: 'lrclib',
        };
      }
    }
  } catch (_) { /* ignore */ }

  return null;
}

function httpGetJson(urlStr, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlStr);
    const transport = url.protocol === 'https:' ? https : http;
    const req = transport.get(url.toString(), {
      headers: { 'User-Agent': 'FloatingLyrics/1.0' },
    }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
      });
    });
    req.on('error', reject);
    req.setTimeout(timeoutMs, () => { req.destroy(new Error('timeout')); });
  });
}

// ── Prompt para o /search: usa evidência web + letras ────────────────

function buildSearchPrompt(rawTitle, webEvidence, lyricsSnippet) {
  const hasEvidence = Array.isArray(webEvidence) && webEvidence.length > 0;
  const hasLyrics = lyricsSnippet && lyricsSnippet.length > 0;
  const cleaned = basicCleanTitle(rawTitle);

  let evidenceBlock = '';
  if (hasEvidence) {
    evidenceBlock = '\nWeb catalog results (from iTunes, searched by title only):\n'
      + webEvidence.map((item, idx) =>
          `${idx + 1}. "${item.title}" by ${item.artist} — album: ${item.album} (score: ${item.score.toFixed(2)})`
        ).join('\n')
      + '\n';
  }

  let lyricsBlock = '';
  if (hasLyrics) {
    const lines = lyricsSnippet.split('\n').slice(0, 20).join('\n');
    lyricsBlock = `\nLyrics found online for the best match:\n${lines}\n`;
  }

  return `<s>[INST] You are a music metadata expert. Given a noisy title string (often from YouTube), determine the correct song metadata.

Return ONLY strict JSON:
{
  "title": "correct song title",
  "artist": "correct artist name",
  "album": "correct album name",
  "confidence": 0.0
}

Rules:
- The input often follows "Artist - Song/Album Noise" or "Artist Song Album Noise".
- Common noise to ignore: "official video", "lyrics", "HD", "4K", "remasterizado", "remastered", track numbers, "full album".
- When the input has a separator (" - "), the part BEFORE it is usually the artist.
- For Brazilian/Portuguese music: "Acustico MTV" or "Acústico MTV" is an album name, not a song title. "Ao Vivo" means live album.
- If the input looks like "Artist - AlbumName" (no specific song), set title to the album name.
- Use web evidence if available to confirm or correct.
- If uncertain, lower confidence.
- Return JSON only. No explanation.

Raw title:
"${String(rawTitle || '').trim()}"

Pre-cleaned:
"${cleaned}"
${evidenceBlock}${lyricsBlock}
[/INST]`;
}

// ── Cache do /search ─────────────────────────────────────────────────
const searchCache = new Map();

/**
 * Endpoint principal novo: busca completa a partir de um título.
 *
 * Body esperado:
 * { "title": "Kendrick Lamar - Money Trees" }
 *
 * Retorno:
 * {
 *   "success": true,
 *   "data": {
 *     "title": "Money Trees",
 *     "artist": "Kendrick Lamar",
 *     "album": "good kid, m.A.A.d city",
 *     "lyrics": "...",
 *     "synced": true,
 *     "confidence": 0.92,
 *     "sources": { "metadata": "catalog", "lyrics": "lrclib" }
 *   }
 * }
 */
fastify.post('/search', async (request, reply) => {
  const { title } = request.body || {};
  const prefix = `[search][req:${request.id}]`;

  if (!title || typeof title !== 'string' || title.trim().length === 0) {
    return reply.code(400).send({
      success: false,
      error: 'Bad Request',
      message: 'title is required and must be a non-empty string',
    });
  }

  const rawTitle = title.trim();
  fastify.log.info(`${prefix} titulo recebido: "${rawTitle.slice(0, 120)}"`);

  // Cache
  const cacheKey = normalizeText(rawTitle);
  if (searchCache.has(cacheKey)) {
    fastify.log.info(`${prefix} cache hit`);
    return searchCache.get(cacheKey);
  }

  const startedAt = Date.now();

  try {
    // ── 1. Buscar na web usando APENAS o título ──────────────────────
    const webEvidence = await buildWebEvidenceFromTitle(rawTitle);
    const bestMatch = webEvidence.length > 0 ? webEvidence[0] : null;

    fastify.log.info({
      web_results: webEvidence.length,
      best_title: bestMatch?.title || 'none',
      best_artist: bestMatch?.artist || 'none',
      best_score: bestMatch?.score?.toFixed(3) || '0',
    }, `${prefix} busca web concluida`);

    // ── 2. Decidir: fast-path (score alto, pular LLM) ou full-path ──
    let resolvedTitle = bestMatch?.title || basicCleanTitle(rawTitle);
    let resolvedArtist = bestMatch?.artist || '';
    let resolvedAlbum = bestMatch?.album || '';
    let confidence = bestMatch?.score || 0.3;
    let metadataSource = bestMatch ? 'catalog' : 'title_only';

    const strongCatalog = bestMatch && bestMatch.score >= CATALOG_STRONG_SCORE;

    if (strongCatalog) {
      // ── FAST PATH: catálogo forte → pular LLM ────────────────────
      fastify.log.info(`${prefix} fast-path: catalog score ${bestMatch.score.toFixed(3)} >= ${CATALOG_STRONG_SCORE}`);
      confidence = bestMatch.score;
      metadataSource = 'catalog';
    } else {
      // ── FULL PATH: LLM analisa evidência web ─────────────────────
      const prompt = buildSearchPrompt(rawTitle, webEvidence, '');

      try {
        const llmResult = await callOllamaFast(prompt);

        if (llmResult) {
          const llmTitle = String(llmResult.title || llmResult.song || '').trim();
          const llmArtist = String(llmResult.artist || '').trim();
          const llmAlbum = String(llmResult.album || '').trim();
          const llmConf = Number(llmResult.confidence || 0);

          if (bestMatch && bestMatch.score >= 0.5) {
            resolvedTitle = llmTitle || bestMatch.title;
            resolvedArtist = (llmArtist && llmConf >= 0.5) ? llmArtist : bestMatch.artist;
            resolvedAlbum = (llmAlbum && llmConf >= 0.5) ? llmAlbum : (bestMatch.album || '');
            confidence = Math.max(confidence, llmConf);
            metadataSource = 'catalog+llm';
          } else if (llmTitle && llmArtist && llmConf >= 0.5) {
            resolvedTitle = llmTitle;
            resolvedArtist = llmArtist;
            resolvedAlbum = llmAlbum;
            confidence = llmConf;
            metadataSource = 'llm';
          }

          fastify.log.info({
            llm_title: llmTitle.slice(0, 80),
            llm_artist: llmArtist.slice(0, 60),
            llm_conf: llmConf,
            final_source: metadataSource,
          }, `${prefix} LLM respondeu`);
        }
      } catch (llmErr) {
        fastify.log.warn(`${prefix} LLM indisponivel: ${llmErr.message}`);
      }
    }

    // ── 3. Montar resposta (sem letras — busca via API separada) ─────
    const response = {
      success: true,
      data: {
        title: resolvedTitle,
        artist: resolvedArtist,
        album: resolvedAlbum,
        confidence: Number(Math.min(1, confidence).toFixed(3)),
        sources: {
          metadata: metadataSource,
        },
      },
      metadata: {
        elapsed_ms: Date.now() - startedAt,
        model: MODEL_NAME,
        timestamp: new Date().toISOString(),
      },
    };

    // Cachear
    if (searchCache.size > 200) {
      const oldKey = searchCache.keys().next().value;
      searchCache.delete(oldKey);
    }
    searchCache.set(cacheKey, response);

    return response;

  } catch (error) {
    fastify.log.error(error);
    return reply.code(500).send({
      success: false,
      error: 'Internal Server Error',
      message: error.message,
      timestamp: new Date().toISOString(),
    });
  }
});

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
